"""Qwen function-calling tool definitions and dispatch table.

Each tool exposed to the Qwen model maps to a deterministic pipeline function.
The model decides *when* to call a tool and with which arguments; the actual
execution is always handled by the existing Python pipeline code.  This keeps
the CAD truth, geometry, and validation logic fully deterministic while letting
Qwen orchestrate the workflow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .bom import read_bom
from .cad_graph import CadGraph
from .io import read_json, write_json
from .product import Product, load_product
from .auto_planner import plan as auto_plan_fn
from .render_jobs import create_render_jobs as create_render_jobs_fn
from .validation import validate_contract, validate_camera_contract


# ---------------------------------------------------------------------------
# Tool schema list (OpenAI function-calling format, compatible with Qwen)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "load_product_config",
            "description": "加载并验证产品配置 (product.json)。返回产品 ID、BOM 路径、模型目录等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "config_path": {
                        "type": "string",
                        "description": "product.json 的绝对或相对路径",
                    }
                },
                "required": ["config_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_bom_items",
            "description": "读取 BOM Excel 文件，返回所有物料行（图号、名称、数量、工序文本等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bom_path": {"type": "string", "description": "BOM Excel 文件路径"},
                    "sheet_name": {
                        "type": "string",
                        "description": "可选的工作表名称；不指定则使用唯一工作表",
                    },
                },
                "required": ["bom_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_cad_graph",
            "description": "从 discovery 输出的 JSON 文件加载 CAD 图谱（occurrence、约束、变换）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "CAD 图谱 JSON 路径"},
                },
                "required": ["graph_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auto_plan_step",
            "description": "根据 BOM 合同和 CAD 图谱自动生成安装步骤规划（活动件、接收件、爆炸方向、相机选择）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_path": {"type": "string", "description": "步骤合同 JSON 路径"},
                    "graph_path": {"type": "string", "description": "CAD 图谱 JSON 路径"},
                },
                "required": ["contract_path", "graph_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_render_jobs",
            "description": "从 CAD 图谱生成渲染任务列表 (creo-render-jobs/v3)，包含活动件分组、爆炸向量和相机选择。",
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "CAD 图谱 JSON 路径"},
                    "output_path": {"type": "string", "description": "输出渲染任务 JSON 路径"},
                },
                "required": ["graph_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_step_contract",
            "description": "校验步骤合同是否满足所有硬门条件（版本、活动件、接收件、爆炸向量、相机等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_path": {"type": "string", "description": "步骤合同 JSON 路径"},
                    "require_render": {
                        "type": "boolean",
                        "description": "是否同时校验渲染输出（默认 false）",
                    },
                },
                "required": ["contract_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_camera_contract",
            "description": "校验相机合同（视角组、坐标系、接收面法向、构图参数）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_path": {"type": "string", "description": "相机合同 JSON 路径"},
                },
                "required": ["contract_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_render_jobs",
            "description": "读取已生成的渲染任务 JSON，返回任务列表摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs_path": {"type": "string", "description": "渲染任务 JSON 路径"},
                },
                "required": ["jobs_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_run_artifact",
            "description": "读取 data/runs/ 下的任意运行时产物 JSON（总装清单、相机基准、discovery 结果等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_path": {"type": "string", "description": "产物 JSON 路径"},
                },
                "required": ["artifact_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_json_artifact",
            "description": "将结构化数据写入 JSON 文件（用于创建合同、配置等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_path": {"type": "string", "description": "输出 JSON 路径"},
                    "content": {"type": "object", "description": "要写入的 JSON 内容"},
                },
                "required": ["output_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_pipeline_error",
            "description": "分析流水线错误并给出诊断建议。输入错误信息和上下文，返回可能原因和修复建议。",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_type": {"type": "string", "description": "错误类型或阶段"},
                    "error_message": {"type": "string", "description": "错误详细信息"},
                    "context": {
                        "type": "object",
                        "description": "出错时的上下文（合同、图谱等）",
                    },
                },
                "required": ["error_type", "error_message"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch table: tool_name → callable
# ---------------------------------------------------------------------------

def _load_product_config(config_path: str) -> dict[str, Any]:
    product = load_product(Path(config_path))
    return {
        "product_id": product.product_id,
        "bom_file": str(product.bom_file),
        "models_dir": str(product.models_dir),
        "sop_template": str(product.sop_template),
        "final_assembly": product.final_assembly,
        "bom_sheet": product.bom_sheet,
        "final_assembly_path": str(product.final_assembly_path),
    }


def _read_bom_items(bom_path: str, sheet_name: str | None = None) -> list[dict[str, Any]]:
    items = read_bom(Path(bom_path), sheet_name)
    return [
        {
            "row": item.row,
            "level": item.level,
            "material_code": item.material_code,
            "drawing_no": item.drawing_no,
            "name": item.name,
            "model": item.model,
            "quantity": item.quantity,
            "unit": item.unit,
            "assembly_text": item.assembly_text,
            "control_points": item.control_points,
            "tools": item.tools,
        }
        for item in items
    ]


def _load_cad_graph(graph_path: str) -> dict[str, Any]:
    data = read_json(Path(graph_path))
    graph = CadGraph.from_json(data)
    return {
        "schema_version": data.get("schema_version"),
        "assembly_file": graph.assembly_file,
        "root_occurrence": graph.root_occurrence,
        "occurrence_count": len(graph.occurrences),
        "constraint_count": len(graph.constraints),
        "occurrences": graph.occurrences,
        "constraints": graph.constraints,
    }


def _auto_plan_step(contract_path: str, graph_path: str) -> dict[str, Any]:
    contract = read_json(Path(contract_path))
    graph = CadGraph.from_json(read_json(Path(graph_path)))
    result = auto_plan_fn(contract, graph)
    write_json(Path(contract_path), result)
    return {
        "phase": result["automation"]["phase"],
        "confidence": result["automation"]["confidence"],
        "reasons": result["automation"].get("reasons", []),
        "moving_occurrences": result.get("moving_occurrences", []),
        "receiver_occurrences": result.get("receiver_occurrences", []),
        "translation": result.get("translation", {}),
        "camera": result.get("camera", {}),
    }


def _create_render_jobs(graph_path: str, output_path: str) -> dict[str, Any]:
    graph = CadGraph.from_json(read_json(Path(graph_path)))
    result_path = create_render_jobs_fn(graph, Path(output_path))
    jobs_data = read_json(result_path)
    return {
        "output_path": str(result_path),
        "schema_version": jobs_data.get("schema_version"),
        "assembly_file": jobs_data.get("assembly_file"),
        "job_count": len(jobs_data.get("jobs", [])),
        "jobs_summary": [
            {
                "job_id": j["job_id"],
                "moving": j.get("moving_occurrences", []),
                "receivers": j.get("receiver_occurrences", []),
                "phase": j.get("automation", {}).get("phase"),
            }
            for j in jobs_data.get("jobs", [])
        ],
    }


def _validate_step_contract(contract_path: str, require_render: bool = False) -> dict[str, Any]:
    contract = read_json(Path(contract_path))
    errors = validate_contract(contract, require_render=require_render)
    return {"passed": len(errors) == 0, "errors": errors}


def _validate_camera_contract_tool(contract_path: str) -> dict[str, Any]:
    contract = read_json(Path(contract_path))
    errors = validate_camera_contract(contract)
    return {"passed": len(errors) == 0, "errors": errors}


def _read_render_jobs(jobs_path: str) -> dict[str, Any]:
    data = read_json(Path(jobs_path))
    return {
        "schema_version": data.get("schema_version"),
        "assembly_file": data.get("assembly_file"),
        "job_count": len(data.get("jobs", [])),
        "jobs": data.get("jobs", []),
    }


def _read_run_artifact(artifact_path: str) -> dict[str, Any]:
    return read_json(Path(artifact_path))


def _write_json_artifact(output_path: str, content: dict[str, Any]) -> dict[str, str]:
    write_json(Path(output_path), content)
    return {"status": "ok", "path": output_path}


def _diagnose_pipeline_error(
    error_type: str, error_message: str, context: dict[str, Any] | None = None
) -> dict[str, str]:
    """Return a structured diagnosis. The Qwen model itself is the actual
    diagnostic brain — this stub records the error for the model to analyse
    in the next conversation turn."""
    return {
        "error_type": error_type,
        "error_message": error_message,
        "context_summary": json.dumps(context or {}, ensure_ascii=False, default=str)[:2000],
        "hint": "请将以上错误信息交给 Qwen 模型进行分析，它会在下一轮给出诊断建议。",
    }


DISPATCH_TABLE: dict[str, Callable[..., Any]] = {
    "load_product_config": _load_product_config,
    "read_bom_items": _read_bom_items,
    "load_cad_graph": _load_cad_graph,
    "auto_plan_step": _auto_plan_step,
    "create_render_jobs": _create_render_jobs,
    "validate_step_contract": _validate_step_contract,
    "validate_camera_contract": _validate_camera_contract_tool,
    "read_render_jobs": _read_render_jobs,
    "read_run_artifact": _read_run_artifact,
    "write_json_artifact": _write_json_artifact,
    "diagnose_pipeline_error": _diagnose_pipeline_error,
}
