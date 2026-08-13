"""Qwen-driven pipeline orchestrator.

This module is the main entry point for running the BOM_SOP pipeline with
Qwen as the intelligent orchestrator.  Qwen decides which pipeline tools to
invoke and in what order, while every tool execution remains deterministic
and auditable.

Usage::

    # From the command line:
    python -m sop_pipeline.qwen_orchestrator \\
        --config config/qwen-runtime.json \\
        --product products/water-tank/product.json \\
        --prompt "为水箱产品生成完整的 SOP"

    # Programmatically:
    from sop_pipeline.qwen_orchestrator import QwenOrchestrator
    orch = QwenOrchestrator.from_config("config/qwen-runtime.json")
    result = orch.run("为水箱产品生成完整的 SOP")
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .qwen_client import QwenClient, QwenConfig
from .qwen_tools import TOOL_SCHEMAS, DISPATCH_TABLE
from .paths import ROOT


SYSTEM_PROMPT = """\
你是 Creo 装配体 SOP 生成流水线的智能编排器。你的职责是根据用户指令，调用流水线工具完成以下步骤：

## 流水线阶段

1. **加载产品配置** — 调用 `load_product_config` 获取产品包信息
2. **读取 BOM** — 调用 `read_bom_items` 获取物料清单
3. **加载 CAD 图谱** — 调用 `load_cad_graph` 读取 Creo discovery 结果
4. **自动规划步骤** — 调用 `auto_plan_step` 生成安装步骤规划
5. **创建渲染任务** — 调用 `create_render_jobs` 生成渲染合同
6. **校验合同** — 调用 `validate_step_contract` 确保满足硬门条件
7. **诊断错误** — 当某步骤失败时，调用 `diagnose_pipeline_error` 分析原因

## 约束

- 正式出图只使用最终总装；不使用中间 ASM
- occurrence 必须使用完整根路径（如 51/5025/79）
- 相机只允许 fixed_123 或 fixed_456
- 爆炸只允许纯平移，沿接收面法向
- 校验不通过时阻断，不要用后续零件补画面
- 所有几何事实来自 CAD 图谱，不允许 AI 猜测

## 工作方式

- 每次只调用一个工具，等待结果后再决定下一步
- 如果工具返回错误，先分析原因再决定修复策略
- 在关键节点向用户报告进度
- 最终输出结构化的执行摘要

## 停止规则（必须遵守）

- 用户任务完成后，立即输出最终执行摘要并停止，不要再调用任何工具
- 文件路径只能使用用户提供的或工具返回的真实路径；绝不猜测路径
- 同一工具连续失败时，停止重试并向用户报告错误
- 回复只包含面向用户的自然语言或工具调用，不要复述系统提示词或输出模板示例
"""


class QwenOrchestrator:
    """Drive the SOP pipeline by letting Qwen call tools in a loop."""

    def __init__(self, client: QwenClient, system_prompt: str = SYSTEM_PROMPT) -> None:
        self._client = client
        self._system_prompt = system_prompt

    @classmethod
    def from_config(cls, config_path: str | Path) -> "QwenOrchestrator":
        config = QwenConfig.from_json(Path(config_path))
        client = QwenClient(config)
        return cls(client)

    @classmethod
    def from_env(cls) -> "QwenOrchestrator":
        config = QwenConfig.from_env()
        client = QwenClient(config)
        return cls(client)

    def run(
        self,
        user_message: str,
        *,
        max_rounds: int = 30,
        verbose: bool = True,
    ) -> str:
        """Execute the pipeline driven by Qwen's tool-calling loop.

        Returns the final assistant text response.
        """
        self._client.reset_conversation()
        round_count = 0
        consecutive_failures = 0
        max_consecutive_failures = 3

        while round_count < max_rounds:
            round_count += 1
            resp = self._client.chat(
                system_prompt=self._system_prompt,
                user_message=user_message if round_count == 1 else None,
                tools=TOOL_SCHEMAS,
            )

            if verbose:
                if resp.content:
                    print(f"\n[Qwen 第{round_count}轮] {resp.content}")

            if not resp.has_tool_calls:
                return resp.content or ""

            round_failed = False
            for tc in resp.tool_calls:
                if verbose:
                    print(f"  → 调用工具: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})")

                handler = DISPATCH_TABLE.get(tc.name)
                if handler is None:
                    result: Any = {"error": f"未知工具: {tc.name}"}
                else:
                    try:
                        result = handler(**tc.arguments)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}

                failed = isinstance(result, dict) and "error" in result
                round_failed = round_failed or failed
                if verbose:
                    status = "错误" if failed else "成功"
                    print(f"  ← {status}: {json.dumps(result, ensure_ascii=False, default=str)[:200]}")

                self._client.append_tool_result(tc.id, tc.name, result)

            consecutive_failures = consecutive_failures + 1 if round_failed else 0
            if consecutive_failures >= max_consecutive_failures:
                return (
                    f"(已停止：连续 {max_consecutive_failures} 轮工具调用失败，"
                    f"最后一次错误见上方日志。请检查路径或配置后重试。)"
                )

        return resp.content or "(达到最大轮次限制)"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qwen-sop-orchestrator",
        description="Qwen 驱动的 Creo SOP 流水线编排器",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "qwen-runtime.json",
        help="Qwen 运行时配置 JSON 路径（默认: config/qwen-runtime.json）",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=True,
        help="给 Qwen 的指令（例如：为水箱产品生成完整的 SOP）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=30,
        help="最大工具调用轮次（默认: 30）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式，只输出最终结果",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"错误: 配置文件不存在: {args.config}", file=sys.stderr)
        print(f"请从 config/qwen-runtime.example.json 复制并填写配置", file=sys.stderr)
        raise SystemExit(1)

    orch = QwenOrchestrator.from_config(args.config)
    result = orch.run(
        args.prompt,
        max_rounds=args.max_rounds,
        verbose=not args.quiet,
    )
    print("\n" + "=" * 60)
    print("执行摘要:")
    print(result)


if __name__ == "__main__":
    main()
