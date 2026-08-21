from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont
from sop_pipeline.camera_planner import select_fixed_camera_for_stage
from sop_pipeline.explosion_planner import select_display_translation

from .bom_cad_mapper import BomCadMap, BomOccurrenceMapping, map_bom_to_occurrences
from .bom_normalizer import NormalizedBom, NormalizedBomRow, normalize_bom
from .bundle_paths import materialized_creo_script
from .creo_discovery import (
    PowerShellCreoDiscovery,
    powershell_command,
    resolve_runtime_config,
)
from .creo_worker import AgentNativeCreoWorker
from .desktop_workflow import (
    _mapping_questions,
    _planning_questions,
)
from .deterministic_resolution import structured_step_revision
from .draft_planner import DraftInstallationStep, DraftPlan, create_draft_plan
from .formal_render_planner import (
    FormalRenderPlan,
    compile_formal_render_plan,
    formal_render_plan_from_dict,
)
from .gate_policy import GateCategory, classify_failures, gate_policy
from .model_inventory import MODEL_PATTERN, ModelFile, ModelInventory, inventory_models
from .models import (
    ClarificationItem,
    ClarificationPacket,
    SkillStatus,
    StepStatus,
)
from .review import (
    ACCEPT_WITH_OVERRIDE,
    HUMAN_OVERRIDE_IMAGE_ID,
    create_human_override_decision,
)
from .render_job_compiler import compile_locked_render_jobs
from .render_scheduler import (
    FileCheckpointStore,
    RenderPlan,
    RenderScheduler,
    RenderTask,
)
from .skill_contract import Diagnostic, RetryScope
from .skill_registry import SkillInvocation
from .skill_runtime import (
    SkillArtifactValue,
    SkillContext,
    SkillHandlerOutput,
)
from .sop_publisher import SopImage, SopPublisher, SopStep
from .step_revision import (
    CURRENT_IMAGE_CANDIDATE_ID,
    RevisionKind,
    StepDependencyGraph,
    StepRevision,
    validate_revision,
)


def default_skill_handlers() -> dict[str, Any]:
    return {
        "intake-preflight": intake_preflight,
        "normalize-bom": normalize_bom_skill,
        "lock-assembly": lock_assembly,
        "discover-cad": discover_cad,
        "map-bom-cad": map_bom_cad,
        "plan-assembly": plan_assembly,
        "clarify-plan": clarify_plan,
        "compile-render-jobs": compile_render_jobs,
        "render-batch": render_batch,
        "validate-repair": validate_repair,
        "publish-delivery": publish_delivery,
        "resolve-step": resolve_step,
    }


def intake_preflight(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    del invocation
    run = context.run
    if not run.bom_file.is_file():
        return _blocked("BOM_NOT_FOUND", f"找不到BOM文件：{run.bom_file}")
    if run.bom_file.suffix.casefold() != ".xlsx":
        return _blocked("BOM_FORMAT_UNSUPPORTED", "当前只接受可验证的XLSX BOM")
    if not run.cad_directory.is_dir():
        return _blocked("CAD_DIRECTORY_NOT_FOUND", f"找不到CAD目录：{run.cad_directory}")
    model_files = tuple(
        sorted(
            path
            for path in run.cad_directory.rglob("*")
            if path.is_file() and MODEL_PATTERN.match(path.name)
        )
    )
    if not model_files:
        return _blocked("CAD_MODELS_NOT_FOUND", "CAD目录中没有Creo ASM/PRT文件")
    manifest = {
        "schema_version": "input-manifest/v1",
        "input_fingerprint": run.input_fingerprint,
        "bom": {
            "name": run.bom_file.name,
            "sha256": _file_hash(run.bom_file),
        },
        "cad": [
            {
                "relative_path": path.relative_to(run.cad_directory).as_posix(),
                "sha256": _file_hash(path),
            }
            for path in model_files
        ],
    }
    runtime_configured = bool(
        context.adapters.get("creo_discovery")
        or resolve_runtime_config(run.workspace) is not None
    )
    report = {
        "schema_version": "preflight-report/v1",
        "passed": True,
        "runtime_configured": runtime_configured,
        "decision_mode": "deterministic_only/v1",
        "excel_verifier_configured": bool(context.adapters.get("workbook_verifier")),
        "model_file_count": len(model_files),
    }
    return _passed(
        SkillArtifactValue("preflight-report", report),
        SkillArtifactValue("input-manifest", manifest),
    )


def normalize_bom_skill(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    del invocation
    try:
        bom = normalize_bom(context.run.bom_file)
    except (OSError, ValueError) as error:
        return _blocked("BOM_NORMALIZATION_FAILED", str(error))
    return _passed(SkillArtifactValue("normalized-bom", bom))


def lock_assembly(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        bom = _normalized(context.read_json(_require_ref(invocation, "normalized-bom")))
        inventory = inventory_models(context.run.cad_directory, bom)
        assembly_path = context.run.cad_directory / inventory.final_assembly
        lock = {
            "schema_version": "assembly-lock/v1",
            "assembly_file": inventory.final_assembly,
            "version": _creo_version(inventory.final_assembly),
            "sha256": _file_hash(assembly_path),
            "candidates": list(inventory.assembly_candidates),
            "root_coordinate_system": "pending-creo-discovery",
        }
    except (KeyError, OSError, ValueError) as error:
        return _blocked("ASSEMBLY_LOCK_FAILED", str(error))
    status = (
        SkillStatus.QUESTIONED
        if len(inventory.assembly_candidates) > 1
        else SkillStatus.PASSED
    )
    diagnostics = (
        (
            Diagnostic(
                "MULTIPLE_FINAL_ASSEMBLIES",
                "发现多个同等可信的最终总装候选，已保留到生成前确认。",
                inventory.assembly_candidates,
            ),
        )
        if status is SkillStatus.QUESTIONED
        else ()
    )
    return SkillHandlerOutput(
        status=status,
        artifacts=(
            SkillArtifactValue("model-inventory", inventory),
            SkillArtifactValue("assembly-lock", lock),
        ),
        diagnostics=diagnostics,
    )


def discover_cad(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        lock = context.read_json(_require_ref(invocation, "assembly-lock"))
        discovery = context.adapters.get("creo_discovery")
        if discovery is None:
            runtime_config = resolve_runtime_config(context.run.workspace)
            if runtime_config is None:
                return _blocked(
                    "CREO_RUNTIME_NOT_CONFIGURED",
                    "尚未配置可用的Creo/J-Link运行环境。",
                )
            discovery = PowerShellCreoDiscovery(
                powershell=powershell_command(),
                script=materialized_creo_script(
                    context.run.workspace, "run_input_discovery.ps1"
                ),
                runtime_config=runtime_config,
            )
        graph = discovery.discover(
            context.run.cad_directory,
            str(lock["assembly_file"]),
            context.run.workspace,
        )
        if graph.get("schema_version") != "creo-cad-graph/v3":
            raise ValueError("Creo discovery没有返回creo-cad-graph/v3")
        if str(graph.get("assembly_file")) != str(lock["assembly_file"]):
            raise ValueError("Creo打开的总装与锁定版本不一致")
        graph = dict(graph)
        authoritative = dict(graph.get("authoritative_assembly", {}))
        authoritative.setdefault("sha256", lock["sha256"])
        authoritative.setdefault("coordinate_system", "root_asm")
        graph["authoritative_assembly"] = authoritative
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        return _blocked("CREO_DISCOVERY_FAILED", str(error))
    return _passed(SkillArtifactValue("creo-cad-graph", graph))


def map_bom_cad(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        bom = _normalized(context.read_json(_require_ref(invocation, "normalized-bom")))
        inventory = _inventory(context.read_json(_require_ref(invocation, "model-inventory")))
        graph = context.read_json(_require_ref(invocation, "creo-cad-graph"))
        draft = create_draft_plan(bom, inventory)
        mapping = map_bom_to_occurrences(bom, inventory, graph, draft)
    except (KeyError, TypeError, ValueError) as error:
        return _blocked("BOM_CAD_MAPPING_FAILED", str(error))
    questioned = bool(
        mapping.ambiguous_rows
        or mapping.missing_rows
        or mapping.quantity_mismatch_rows
    )
    return SkillHandlerOutput(
        status=SkillStatus.QUESTIONED if questioned else SkillStatus.PASSED,
        artifacts=(SkillArtifactValue("bom-cad-map", mapping),),
        diagnostics=(
            (
                Diagnostic(
                    "BOM_CAD_MAPPING_QUESTIONS",
                    "部分BOM行需要在生成前确认，未猜测occurrence。",
                    tuple(
                        str(value)
                        for value in sorted(
                            set(
                                mapping.ambiguous_rows
                                + mapping.missing_rows
                                + mapping.quantity_mismatch_rows
                            )
                        )
                    ),
                ),
            )
            if questioned
            else ()
        ),
    )


def plan_assembly(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        bom = _normalized(context.read_json(_require_ref(invocation, "normalized-bom")))
        inventory = _inventory(context.read_json(_require_ref(invocation, "model-inventory")))
        mapping = _mapping(context.read_json(_require_ref(invocation, "bom-cad-map")))
        graph = context.read_json(_require_ref(invocation, "creo-cad-graph"))
        draft = create_draft_plan(bom, inventory)
        formal = compile_formal_render_plan(bom, draft, mapping, graph)
    except (KeyError, TypeError, ValueError) as error:
        return _blocked("ASSEMBLY_PLANNING_FAILED", str(error))
    questioned = formal.questioned_steps > 0
    return SkillHandlerOutput(
        status=SkillStatus.QUESTIONED if questioned else SkillStatus.PASSED,
        artifacts=(
            SkillArtifactValue("draft-plan", draft),
            SkillArtifactValue("formal-render-plan", formal),
        ),
        diagnostics=(
            tuple(
                Diagnostic(item.code, item.message, tuple(item.affected_steps))
                for item in formal.diagnostics
            )
            if questioned
            else ()
        ),
    )


def clarify_plan(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        bom = _normalized(context.read_json(_require_ref(invocation, "normalized-bom")))
        inventory = _inventory(context.read_json(_require_ref(invocation, "model-inventory")))
        mapping = _mapping(context.read_json(_require_ref(invocation, "bom-cad-map")))
        draft = _draft(context.read_json(_require_ref(invocation, "draft-plan")))
        formal = formal_render_plan_from_dict(
            context.read_json(_require_ref(invocation, "formal-render-plan"))
        )
        questions = list(_base_questions(bom, inventory))
        questions.extend(_mapping_questions(mapping))
        questions.extend(_planning_questions(formal))
        packet = ClarificationPacket(
            schema_version="clarification-packet/v1",
            summary=(
                f"已识别{len(bom.rows)}行物料、{len(bom.main_process_numbers)}个主工序，"
                f"锁定总装{inventory.final_assembly}，并规划{len(formal.steps)}个安装步骤。"
            ),
            items=tuple(questions),
            facts={
                "creo_discovery": "passed",
                "bom_sheet": bom.sheet_name,
                "bom_rows": len(bom.rows),
                "main_processes": len(bom.main_process_numbers),
                "renderable_main_processes": len(bom.renderable_process_numbers),
                "final_assembly": inventory.final_assembly,
                "model_files": len(inventory.files),
                "cad_occurrences": len(
                    context.read_json(_require_ref(invocation, "creo-cad-graph")).get(
                        "occurrences", []
                    )
                ),
                "cad_constraints": len(
                    context.read_json(_require_ref(invocation, "creo-cad-graph")).get(
                        "constraints", []
                    )
                ),
                "mapped_bom_rows": mapping.matched_rows,
                "ambiguous_occurrence_rows": len(mapping.ambiguous_rows),
                "missing_occurrence_rows": len(mapping.missing_rows),
                "quantity_mismatch_rows": len(mapping.quantity_mismatch_rows),
                "formal_render_steps": len(formal.steps),
                "formal_ready_steps": formal.ready_steps,
                "formal_questioned_steps": formal.questioned_steps,
                "formal_plan_fingerprint": formal.fingerprint,
                "decision_mode": "deterministic_only/v1",
            },
        )
    except (KeyError, TypeError, ValueError) as error:
        return _blocked("CLARIFICATION_FAILED", str(error))
    return SkillHandlerOutput(
        status=SkillStatus.QUESTIONED if packet.items else SkillStatus.PASSED,
        artifacts=(SkillArtifactValue("clarification-packet", packet),),
        diagnostics=(
            (
                Diagnostic(
                    "PLAN_CONFIRMATION_REQUIRED",
                    "装配方案包含需要在生成前统一确认的项目。",
                    tuple(item.item_id for item in packet.items),
                ),
            )
            if packet.items
            else ()
        ),
    )


def compile_render_jobs(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        plan = formal_render_plan_from_dict(
            context.read_json(_require_ref(invocation, "locked-render-plan"))
        )
        jobs = compile_locked_render_jobs(plan)
        revision_ref = _optional_ref(invocation, "step-revision")
        if revision_ref is not None:
            jobs = _apply_step_revision(jobs, context.read_json(revision_ref))
    except (KeyError, TypeError, ValueError) as error:
        return _blocked("RENDER_JOB_COMPILATION_FAILED", str(error))
    return _passed(SkillArtifactValue("locked-render-jobs", jobs))


def render_batch(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        input_manifest = context.read_json(
            _require_ref(invocation, "input-manifest")
        )
        _assert_input_manifest_unchanged(context.run, input_manifest)
        jobs_ref = context.artifact(_require_ref(invocation, "locked-render-jobs"))
        plan = _render_plan(context.read_json(jobs_ref.relative_path))
        invalidation_ref = _optional_ref(invocation, "invalidation-set")
        prior_validation_ref = _optional_ref(invocation, "results/validation-")
        requested_steps = {
            str(value) for value in invocation.parameters.get("step_ids", [])
        }
        known_steps = {task.step_id for task in plan.tasks}
        if requested_steps - known_steps:
            raise ValueError(
                "render-batch包含未知步骤："
                + ", ".join(sorted(requested_steps - known_steps))
            )
        invalidated = requested_steps or (
            set(context.read_json(invalidation_ref).get("steps", []))
            if invalidation_ref is not None
            else known_steps
        )
        prior_steps = {}
        if prior_validation_ref is not None:
            prior_steps = {
                str(item["step_id"]): item
                for item in context.read_json(prior_validation_ref).get("steps", [])
            }
        formal_ids = {
            task.step_id
            for task in plan.tasks
            if task.payload.get("execution_mode")
            in {"formal", "diagnostic_preview"}
            and task.step_id in invalidated
        }
        formal_tasks = tuple(
            replace(
                task,
                depends_on=tuple(
                    dependency
                    for dependency in task.depends_on
                    if dependency in formal_ids
                ),
            )
            for task in plan.tasks
            if task.step_id in formal_ids
        )
        formal_plan = RenderPlan("render-plan/v2", formal_tasks)
        if invalidation_ref is not None and invalidated and not formal_tasks:
            blocked_tasks = [
                task for task in plan.tasks if task.step_id in invalidated
            ]
            codes = sorted(
                {
                    str(code)
                    for task in blocked_tasks
                    for code in task.payload.get("diagnostics", [])
                    if str(code)
                }
            )
            detail = "、".join(codes) or "任务缺少可验证的安装几何"
            payload = {
                "schema_version": "render-batch-result/v2",
                "plan_fingerprint": plan.fingerprint,
                "steps": [],
                "metrics": {
                    "total_tasks": 0,
                    "rendered_tasks": 0,
                    "render_attempts": 0,
                    "worker_sessions": 0,
                    "restored_steps": 0,
                },
                "failure_summary": {
                    "BLOCKED:TARGET_NOT_RENDERABLE": len(blocked_tasks)
                },
                "diagnostics_directory": "internal/render-diagnostics",
            }
            return SkillHandlerOutput(
                status=SkillStatus.BLOCKED,
                artifacts=(SkillArtifactValue("render-batch-result", payload),),
                diagnostics=(
                    Diagnostic(
                        "TARGET_RERENDER_PRODUCED_NO_IMAGE",
                        "本次修订没有解除当前几何硬门，因此没有生成新的真实图片。"
                        f"仍需处理：{detail}",
                        tuple(sorted(invalidated)),
                    ),
                ),
                allowed_next=("resolve-step",),
            )
        worker = context.adapters.get("render_worker")
        if formal_tasks and worker is None:
            runtime_config = resolve_runtime_config(context.run.workspace)
            if runtime_config is None:
                return _blocked(
                    "CREO_RUNTIME_NOT_CONFIGURED",
                    "正式渲染找不到当前运行批次的Creo运行时配置。",
                )
            worker = AgentNativeCreoWorker(
                powershell=powershell_command(),
                batch_script=materialized_creo_script(
                    context.run.workspace, "run_agent_native_batch.ps1"
                ),
                stop_script=materialized_creo_script(
                    context.run.workspace, "stop_agent_native_worker.ps1"
                ),
                models_root=context.run.cad_directory,
                render_plan_json=context.run.workspace / jobs_ref.relative_path,
                runtime_config=runtime_config,
            )
        if formal_tasks:
            scheduled = RenderScheduler(max_attempts=3, tasks_per_session=20).execute(
                formal_plan,
                worker,
                context.run.workspace,
                FileCheckpointStore(
                    context.run.workspace
                    / "internal"
                    / (
                        f"render-checkpoint-{context.run.plan_revision:04d}.json"
                        if invalidation_ref is None
                        else f"render-checkpoint-{context.run.plan_revision:04d}-{Path(invalidation_ref).stem}.json"
                    )
                ),
            )
            formal_results = {step.step_id: step for step in scheduled.steps}
            metrics = asdict(scheduled.metrics)
        else:
            formal_results = {}
            metrics = {
                "total_tasks": 0,
                "rendered_tasks": 0,
                "render_attempts": 0,
                "worker_sessions": 0,
                "restored_steps": 0,
            }
        _assert_input_manifest_unchanged(context.run, input_manifest)
        result_tasks = (
            tuple(task for task in plan.tasks if task.step_id in invalidated)
            if requested_steps
            else plan.tasks
        )
        steps = []
        for task in result_tasks:
            if task.step_id not in invalidated and task.step_id in prior_steps:
                restored = dict(prior_steps[task.step_id])
                restored["restored"] = True
                steps.append(restored)
                continue
            result = formal_results.get(task.step_id)
            if result is None:
                unresolved = [
                    str(code)
                    for code in task.payload.get("diagnostics", [])
                    if str(code)
                ] or ["TASK_NOT_FORMAL"]
                decision = classify_failures(unresolved)
                status = (
                    StepStatus.QUESTIONED
                    if task.payload.get("execution_mode") == "diagnostic_preview"
                    else StepStatus.FAILED
                )
                steps.append(
                    {
                        "step_id": task.step_id,
                        "main_process_id": task.main_process_id,
                        "status": status.value,
                        "depends_on": list(task.depends_on),
                        "complete_state_hash": task.complete_state_hash,
                        "output_hash": None,
                        "image_path": None,
                        "execution_mode": task.payload.get("execution_mode"),
                        "restored": False,
                        "error_code": decision.primary_code,
                        "primary_code": decision.primary_code,
                        "failures": _failure_details(unresolved),
                        "category": decision.category.value,
                        "expected": None,
                        "actual": None,
                        "attempted_actions": [],
                        "suggested_actions": [
                            gate_policy(code).suggested_action for code in unresolved
                        ],
                        "retained_image": None,
                        "error_message": (
                            "该步骤尚未满足正式渲染条件："
                            + ", ".join(unresolved)
                        ),
                    }
                )
                continue
            image_path = context.run.workspace / "rendered" / f"{task.task_id}.jpg"
            diagnostic = None
            diagnostic_reader = getattr(worker, "diagnostic_for", None)
            if callable(diagnostic_reader):
                diagnostic = diagnostic_reader(task.task_id)
            final_attempt = scheduled.final_attempts.get(task.step_id)
            effective_error_code = _render_diagnostic_code(
                diagnostic, final_attempt
            )
            gate_failures = (
                [str(value) for value in diagnostic.get("failures", ())]
                if diagnostic and diagnostic.get("failures")
                else ([effective_error_code] if effective_error_code else [])
            )
            execution_mode = str(task.payload.get("execution_mode") or "formal")
            planning_diagnostics = [
                str(code)
                for code in task.payload.get("diagnostics", [])
                if str(code)
            ]
            all_failures = list(
                dict.fromkeys(planning_diagnostics + gate_failures)
            )
            decision = classify_failures(all_failures)
            result_status = (
                StepStatus.FAILED
                if execution_mode == "diagnostic_preview"
                else result.status
            )
            deterministic_geometry_passed = (
                execution_mode != "diagnostic_preview"
                and (
                    result.status is StepStatus.PASSED
                    or bool(
                        gate_failures
                        and all(
                            gate_policy(str(failure)).category
                            in {GateCategory.AUTO_REPAIR, GateCategory.HUMAN_REVIEW}
                            for failure in gate_failures
                        )
                    )
                )
            )
            retained_image: str | None = None
            if (
                result.status is StepStatus.FAILED
                and image_path.is_file()
                and decision.category
                in {GateCategory.AUTO_REPAIR, GateCategory.HUMAN_REVIEW}
            ):
                result_status = StepStatus.QUESTIONED
            if (
                result.status is StepStatus.FAILED
                and decision.category is GateCategory.SYSTEM_RETRY
            ):
                previous = prior_steps.get(task.step_id, {})
                previous_relative = str(previous.get("image_path") or "")
                previous_path = (
                    context.run.workspace / previous_relative
                    if previous_relative
                    else None
                )
                if (
                    previous_path is not None
                    and previous_path.is_file()
                    and "placeholder" not in previous_path.name.casefold()
                ):
                    image_path = previous_path
                    retained_image = previous_relative.replace("\\", "/")
                    result_status = StepStatus.QUESTIONED
            has_real_image = (
                image_path.is_file()
                and "placeholder" not in image_path.name.casefold()
            )
            steps.append(
                {
                    **asdict(result),
                    "status": result_status.value,
                    "depends_on": list(result.depends_on),
                    "image_path": (
                        str(image_path.relative_to(context.run.workspace))
                        if image_path.is_file()
                        else None
                    ),
                    "execution_mode": execution_mode,
                    "restored": False,
                    "error_code": (
                        planning_diagnostics[0]
                        if execution_mode == "diagnostic_preview"
                        and planning_diagnostics
                        else effective_error_code
                    ),
                    "primary_code": decision.primary_code or None,
                    "failures": _failure_details(all_failures),
                    "category": (
                        decision.category.value if all_failures else None
                    ),
                    "expected": diagnostic.get("expected") if diagnostic else None,
                    "actual": diagnostic.get("actual") if diagnostic else None,
                    "attempted_actions": (
                        list(diagnostic.get("attempted_actions", []))
                        if diagnostic
                        else []
                    ),
                    "suggested_actions": [
                        gate_policy(code).suggested_action for code in all_failures
                    ],
                    "retained_image": (
                        retained_image
                        or (
                            str(diagnostic.get("retained_image") or "")
                            if diagnostic
                            else ""
                        )
                        or None
                    ),
                    "error_message": (
                        (
                            "安装几何存在待确认项，已保留推定结果的真实图片供人工复核："
                            + "、".join(planning_diagnostics)
                            if has_real_image
                            else (
                                _render_diagnostic_message(diagnostic)
                                if diagnostic
                                else (
                                    f"渲染结束：{final_attempt.error_code}"
                                    if final_attempt and final_attempt.error_code
                                    else (
                                        "安装几何存在待确认项，但未能生成可供复核的真实图片："
                                        + "、".join(planning_diagnostics)
                                    )
                                )
                            )
                        )
                        if execution_mode == "diagnostic_preview"
                        else (
                            _render_diagnostic_message(diagnostic)
                            if diagnostic
                            else (
                                f"渲染结束：{final_attempt.error_code}"
                                if final_attempt and final_attempt.error_code
                                else None
                            )
                        )
                    ),
                    "gate_failures": gate_failures,
                    "deterministic_geometry_passed": deterministic_geometry_passed,
                }
            )
        payload = {
            "schema_version": "render-batch-result/v2",
            "plan_fingerprint": plan.fingerprint,
            "steps": steps,
            "metrics": metrics,
            "failure_summary": _failure_summary(steps),
            "diagnostics_directory": "internal/render-diagnostics",
        }
        formal_step_payloads = [
            item for item in steps if item.get("execution_mode") == "formal"
        ]
        if formal_step_payloads and not any(
            item.get("status")
            in {StepStatus.PASSED.value, StepStatus.QUESTIONED.value}
            for item in formal_step_payloads
        ):
            first = next(
                (
                    item
                    for item in formal_step_payloads
                    if item.get("error_code") or item.get("error_message")
                ),
                formal_step_payloads[0],
            )
            error_code = str(first.get("error_code") or "FORMAL_RENDER_FAILED")
            detail = str(first.get("error_message") or "没有生成任何真实Creo图片")
            return SkillHandlerOutput(
                status=SkillStatus.BLOCKED,
                artifacts=(SkillArtifactValue("render-batch-result", payload),),
                diagnostics=(
                    Diagnostic(
                        "RENDER_BATCH_ZERO_SUCCESS",
                        f"正式渲染零张成功，已停止出版。首个错误 {error_code}：{detail}",
                        tuple(str(item["step_id"]) for item in formal_step_payloads[:10]),
                    ),
                ),
                allowed_next=("render-batch",),
            )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        return SkillHandlerOutput(
            status=SkillStatus.RETRYABLE,
            diagnostics=(Diagnostic("RENDER_BATCH_FAILED", str(error)),),
            retry_scope=RetryScope("render_plan", ("all-pending",), 3),
            allowed_next=("render-batch",),
        )
    return _passed(SkillArtifactValue("render-batch-result", payload))


def _render_diagnostic_code(
    diagnostic: Mapping[str, object] | None,
    final_attempt: object | None,
) -> str | None:
    if diagnostic:
        for key in ("error_code", "gate_code"):
            value = str(diagnostic.get(key) or "").strip()
            if value:
                return value
    value = str(getattr(final_attempt, "error_code", "") or "").strip()
    return value or None


def _render_diagnostic_message(diagnostic: Mapping[str, object]) -> str:
    for key in ("stderr_tail", "message", "stdout_tail"):
        value = str(diagnostic.get(key) or "").strip()
        if value:
            return value[-1000:]
    error_code = str(
        diagnostic.get("error_code") or diagnostic.get("gate_code") or ""
    ).strip()
    if error_code:
        return gate_policy(error_code).user_message
    return "Creo渲染进程未返回详细错误信息"


def _failure_details(codes: list[str]) -> list[dict[str, object]]:
    return [
        {
            "code": policy.code,
            "category": policy.category.value,
            "message": policy.user_message,
            "suggested_action": policy.suggested_action,
            "retain_real_image": policy.retain_real_image,
        }
        for code in codes
        for policy in (gate_policy(code),)
    ]


def _failure_summary(steps: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in steps:
        status = str(item.get("status") or "UNKNOWN")
        code = str(item.get("error_code") or status)
        key = f"{status}:{code}"
        summary[key] = summary.get(key, 0) + 1
    return dict(sorted(summary.items()))


def validate_repair(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        batch = context.read_json(_require_ref(invocation, "render-batch-"))
        jobs = _render_plan(context.read_json(_require_ref(invocation, "locked-render-jobs")))
        by_step = {task.step_id: task for task in jobs.tasks}
        validated: list[dict[str, Any]] = []
        candidate_groups: list[dict[str, Any]] = []
        questioned = False
        for item in batch.get("steps", []):
            step_id = str(item["step_id"])
            task = by_step[step_id]
            status = StepStatus(item["status"])
            relative_image = item.get("image_path")
            image_path = (
                context.run.workspace / str(relative_image)
                if relative_image
                else None
            )
            issues: list[str] = []
            structured_failures = [
                dict(value)
                for value in item.get("failures", [])
                if isinstance(value, dict)
            ]
            for failure in structured_failures:
                message = str(failure.get("message") or failure.get("code") or "").strip()
                action = str(failure.get("suggested_action") or "").strip()
                detail = f"{message} 建议：{action}" if message and action else message
                if detail and detail not in issues:
                    issues.append(detail)
            planning_review_required = (
                str(item.get("execution_mode")) == "diagnostic_preview"
            )
            if planning_review_required:
                planning_issue = str(
                    item.get("error_message")
                    or item.get("error_code")
                    or "安装几何需要人工确认"
                ).strip()
                if planning_issue:
                    issues.append(planning_issue)
            discovered_candidates: tuple[Path, ...] = ()
            geometry_passed = status is StepStatus.PASSED or bool(
                item.get("deterministic_geometry_passed", False)
            )
            manual_acceptance_allowed = False
            if geometry_passed and image_path is not None and image_path.is_file():
                # Image quality is now decided exclusively by the person who
                # sees the real Creo raster. Structural gates have already
                # established that the image is safe to present for review.
                status = StepStatus.QUESTIONED
                questioned = True
                manual_acceptance_allowed = not planning_review_required
            else:
                status = (
                    StepStatus.QUESTIONED
                    if status is StepStatus.QUESTIONED
                    else StepStatus.FAILED
                )
                failure_detail = str(
                    item.get("error_message") or item.get("error_code") or ""
                ).strip()
                if failure_detail:
                    issues.append(failure_detail)
            if status is not StepStatus.PASSED:
                questioned = True
                category = str(item.get("category") or "")
                if geometry_passed and category not in {"hard_block", "system_retry"}:
                    discovered_candidates = tuple(
                        sorted(
                            (context.run.workspace / "rendered").glob(
                                f"{_safe_id(step_id)}-candidate-*.*"
                            )
                        )
                    )
                if 2 <= len(discovered_candidates) <= 4:
                    candidate_groups.append(
                        {
                            "step_id": step_id,
                            "factor": "bounded-render-variant",
                            "selection_allowed": True,
                            "candidates": [
                                {
                                    "candidate_id": f"candidate-{index}",
                                    "image_path": str(
                                        candidate.relative_to(context.run.workspace)
                                    ),
                                    "sha256": _file_hash(candidate),
                                    "recommended": index == 1,
                                }
                                for index, candidate in enumerate(
                                    discovered_candidates, start=1
                                )
                            ],
                        }
                    )
                    image_path = discovered_candidates[0]
                    status = StepStatus.QUESTIONED
                elif geometry_passed and image_path is not None and image_path.is_file():
                    # A real Creo render which passed every structural/geometric
                    # gate remains a legitimate human-selectable option even
                    # when no alternate variants were produced.
                    manual_acceptance_allowed = True
                elif image_path is None or not image_path.is_file():
                    placeholder = (
                        context.run.workspace
                        / "internal"
                        / "validation"
                        / f"{_safe_id(step_id)}-placeholder.png"
                    )
                    _write_placeholder(placeholder, step_id)
                    image_path = placeholder
            review_error_code = item.get("error_code")
            review_error_message = item.get("error_message")
            review_primary_code = item.get("primary_code")
            review_failures = structured_failures
            review_category = item.get("category")
            if (
                geometry_passed
                and image_path is not None
                and image_path.is_file()
                and not planning_review_required
            ):
                review_error_code = "MANUAL_IMAGE_REVIEW_REQUIRED"
                review_error_message = (
                    "图片质量不做自动判定；请人工预览后采用，或选择问题二次生成。"
                )
                review_primary_code = "MANUAL_IMAGE_REVIEW_REQUIRED"
                review_failures = []
                review_category = "human_review"
                issues = [review_error_message]

            diagnostic_payload = {
                "schema_version": "validation-diagnostic/v1",
                "step_id": step_id,
                "input_status": str(item["status"]),
                "input_error_code": item.get("error_code"),
                "input_error_message": item.get("error_message"),
                "primary_code": item.get("primary_code"),
                "failures": structured_failures,
                "category": item.get("category"),
                "expected": item.get("expected"),
                "actual": item.get("actual"),
                "attempted_actions": item.get("attempted_actions", []),
                "suggested_actions": item.get("suggested_actions", []),
                "retained_image": item.get("retained_image"),
                "review_mode": "deterministic_only/v1",
                "image_review_mode": "human_only/v1",
                "review_issues": issues,
                "final_status": status.value,
                "image_path": (
                    str(image_path.relative_to(context.run.workspace))
                    if image_path is not None
                    else None
                ),
            }
            diagnostic_root = (
                context.run.workspace / "internal" / "validation-diagnostics"
            )
            diagnostic_root.mkdir(parents=True, exist_ok=True)
            diagnostic_path = diagnostic_root / f"{_safe_id(step_id)}.json"
            temporary_diagnostic = diagnostic_path.with_suffix(".json.tmp")
            temporary_diagnostic.write_text(
                json.dumps(
                    diagnostic_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary_diagnostic.replace(diagnostic_path)
            validated.append(
                {
                    "step_id": step_id,
                    "main_process_id": str(item["main_process_id"]),
                    "status": status.value,
                    "depends_on": list(item.get("depends_on", [])),
                    "complete_state_hash": str(item["complete_state_hash"]),
                    "output_hash": _file_hash(image_path) if image_path else None,
                    "image_path": (
                        str(image_path.relative_to(context.run.workspace))
                        if image_path
                        else None
                    ),
                    "error_code": review_error_code,
                    "error_message": review_error_message,
                    "issues": issues,
                    "primary_code": review_primary_code,
                    "failures": review_failures,
                    "category": review_category,
                    "machine_observations": {
                        "error_code": item.get("error_code"),
                        "primary_code": item.get("primary_code"),
                        "failures": structured_failures,
                        "category": item.get("category"),
                    },
                    "expected": item.get("expected"),
                    "actual": item.get("actual"),
                    "attempted_actions": item.get("attempted_actions", []),
                    "suggested_actions": item.get("suggested_actions", []),
                    "retained_image": item.get("retained_image"),
                    "manual_acceptance_allowed": manual_acceptance_allowed,
                    "image_kind": (
                        "rendered"
                        if image_path is not None
                        and image_path.is_file()
                        and "placeholder" not in image_path.name.casefold()
                        else (
                            "candidate"
                            if discovered_candidates
                            else "placeholder"
                        )
                    ),
                }
            )
        validation = {
            "schema_version": "validation-result/v2",
            "steps": validated,
            "failure_summary": _failure_summary(validated),
            "diagnostics_directory": "internal/validation-diagnostics",
        }
        candidates = {
            "schema_version": "candidate-set/v1",
            "groups": candidate_groups,
        }
    except (KeyError, OSError, TypeError, ValueError) as error:
        return _blocked("VALIDATION_FAILED", str(error))
    return SkillHandlerOutput(
        status=SkillStatus.QUESTIONED if questioned else SkillStatus.PASSED,
        artifacts=(
            SkillArtifactValue("validation-result", validation),
            SkillArtifactValue("candidate-set", candidates),
        ),
        diagnostics=(
            (Diagnostic("STEPS_NEED_REVIEW", "部分步骤需要候选选择或重新生成。"),)
            if questioned
            else ()
        ),
    )


def publish_delivery(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    try:
        _assert_input_manifest_unchanged(
            context.run,
            context.read_json(_require_ref(invocation, "input-manifest")),
        )
        validation = context.read_json(_require_ref(invocation, "results/validation-"))
        formal = formal_render_plan_from_dict(
            context.read_json(_require_ref(invocation, "locked-render-plan"))
        )
        bom = _normalized(context.read_json(_require_ref(invocation, "normalized-bom")))
        root_bom_row = bom.rows[0] if bom.rows else None
        candidates = context.read_json(_require_ref(invocation, "candidate-set"))
        groups = {
            str(item["step_id"]): item for item in candidates.get("groups", [])
        }
        selected_step = None
        selected_candidate = None
        decision_ref = _optional_ref(invocation, "human-review-decision")
        human_decision = (
            context.read_json(decision_ref) if decision_ref is not None else None
        )
        prior_publication_ref = _optional_ref(
            invocation, "results/publication-"
        )
        prior_publication = (
            context.read_json(prior_publication_ref)
            if prior_publication_ref is not None
            else {}
        )
        prior_steps = {
            str(item.get("step_id")): item
            for item in prior_publication.get("steps", [])
            if isinstance(item, dict)
        }
        committed_steps = {
            step.step_id: step
            for step in context.run_store.list_steps(context.run.run_id)
        }
        invalidation_ref = _optional_ref(invocation, "invalidation-set")
        invalidated = (
            {
                str(step_id)
                for step_id in context.read_json(invalidation_ref).get("steps", [])
            }
            if invalidation_ref is not None
            else set()
        )
        revision_ref = _optional_ref(invocation, "step-revision")
        if revision_ref is not None:
            revision = context.read_json(revision_ref)
            selected_step = str(revision.get("step_id", ""))
            selected_candidate = str(
                revision.get("changes", {}).get("candidate_id", "")
            )
        formal_by_step = {step.step_id: step for step in formal.steps}
        bom_by_row = {row.row: row for row in bom.rows}
        sop_steps: list[SopStep] = []
        result_steps: list[dict[str, Any]] = []
        pending = False
        for item in validation.get("steps", []):
            step_id = str(item["step_id"])
            step = formal_by_step[step_id]
            status = StepStatus(item["status"])
            machine_status = status.value
            # Legacy validation artifacts may say PASSED, but image-quality
            # approval is now human-only. Publication must not silently carry
            # that old machine disposition across this seam.
            if status is StepStatus.PASSED:
                status = StepStatus.QUESTIONED
            human_disposition: str | None = None
            applied_review_decision_ref: str | None = None
            image_path = context.run.workspace / str(item["image_path"])
            candidate_images = tuple(
                SopImage(
                    image_id=str(candidate["candidate_id"]),
                    path=context.run.workspace / str(candidate["image_path"]),
                    candidate_id=str(candidate["candidate_id"]),
                    recommended=bool(candidate.get("recommended", False)),
                )
                for candidate in groups.get(step_id, {}).get("candidates", [])
            )
            prior = prior_steps.get(step_id)
            committed = committed_steps.get(step_id)
            result_depends_on = tuple(item.get("depends_on", step.depends_on))
            result_complete_state_hash = str(
                item.get("complete_state_hash") or step.complete_state_hash
            )
            if (
                committed is not None
                and committed.status is StepStatus.PASSED
                and step_id not in invalidated
                and str((prior or {}).get("human_disposition") or "")
                in {
                    "accept_current_image",
                    "accept_candidate",
                    ACCEPT_WITH_OVERRIDE,
                }
            ):
                prior_evidence = dict(prior or {})
                prior_evidence["output_hash"] = committed.output_hash
                image_path = _historical_publication_image(
                    context.run.workspace,
                    prior_evidence,
                    image_path,
                    candidate_images,
                )
                status = StepStatus.PASSED
                result_depends_on = committed.depends_on
                result_complete_state_hash = committed.complete_state_hash
                machine_status = str(prior.get("machine_status") or machine_status)
                human_disposition = prior.get("human_disposition")
                applied_review_decision_ref = prior.get("review_decision_ref")
            if (
                step_id == selected_step
                and selected_candidate == CURRENT_IMAGE_CANDIDATE_ID
            ):
                status = StepStatus.PASSED
                human_disposition = "accept_current_image"
            elif (
                step_id == selected_step
                and selected_candidate == HUMAN_OVERRIDE_IMAGE_ID
            ):
                if not isinstance(human_decision, Mapping):
                    raise ValueError("人工特批缺少独立审核决定记录")
                if (
                    str(human_decision.get("decision")) != ACCEPT_WITH_OVERRIDE
                    or str(human_decision.get("step_id")) != step_id
                ):
                    raise ValueError("人工审核决定与当前步骤不一致")
                selected_relative = str(
                    human_decision.get("selected_image_path") or ""
                )
                selected_path = (context.run.workspace / selected_relative).resolve()
                root = context.run.workspace.resolve()
                if root not in selected_path.parents or not selected_path.is_file():
                    raise ValueError("人工选定的原始图片不存在或超出当前运行区")
                if "placeholder" in selected_path.name.casefold():
                    raise ValueError("占位图不能通过人工特批进入正式SOP")
                if _file_hash(selected_path) != str(
                    human_decision.get("selected_image_sha256") or ""
                ):
                    raise ValueError("人工选定图片已变化，必须重新审核")
                if (
                    human_decision.get("publication_transform") != "none"
                    or bool(human_decision.get("watermark", True))
                ):
                    raise ValueError("人工特批必须直接采用无水印原始图片")
                image_path = selected_path
                status = StepStatus.PASSED
                human_disposition = ACCEPT_WITH_OVERRIDE
                applied_review_decision_ref = decision_ref
            elif step_id == selected_step and selected_candidate:
                chosen = next(
                    (
                        candidate
                        for candidate in candidate_images
                        if candidate.candidate_id == selected_candidate
                    ),
                    None,
                )
                if chosen is None:
                    raise ValueError("选中的候选图不属于当前步骤")
                image_path = chosen.path
                status = StepStatus.PASSED
                human_disposition = "accept_candidate"
            if status is StepStatus.PASSED:
                # Once a step is resolved, its alternatives leave both the
                # review queue and the user delivery directory.
                candidate_images = ()
            pending = pending or status is not StepStatus.PASSED
            material_rows = [
                bom_by_row[row]
                for row in step.source_bom_rows
                if row in bom_by_row
            ]
            materials = tuple(
                (
                    row.material_code or row.drawing_no,
                    row.name,
                    int(row.quantity) if isinstance(row.quantity, (int, float)) else 1,
                )
                for row in material_rows
            )
            source = bom_by_row.get(step.source_bom_rows[0]) if step.source_bom_rows else None
            sop_steps.append(
                SopStep(
                    step_id=step_id,
                    main_process_id=step.main_process_id,
                    main_process_name=(source.name if source and source.name else step.main_process_id),
                    title=step.title,
                    image=SopImage(
                        image_id=f"image-{step_id}",
                        path=image_path,
                        placeholder=status is not StepStatus.PASSED,
                    ),
                    materials=materials,
                    process_text=source.assembly_text if source else step.title,
                    control_points=source.control_points if source else "",
                    tools=source.tools if source else "",
                    project_name=(
                        root_bom_row.name
                        if root_bom_row and root_bom_row.name
                        else "待填写"
                    ),
                    document_no=(
                        (root_bom_row.drawing_no or root_bom_row.material_code)
                        if root_bom_row
                        else "待填写"
                    ) or "待填写",
                    applicable_model=(
                        root_bom_row.model
                        if root_bom_row and root_bom_row.model
                        else "待填写"
                    ),
                    questioned=status is not StepStatus.PASSED,
                    candidates=candidate_images,
                )
            )
            result_steps.append(
                {
                    "step_id": step_id,
                    "main_process_id": step.main_process_id,
                    "status": status.value,
                    "depends_on": list(result_depends_on),
                    "complete_state_hash": result_complete_state_hash,
                    "output_hash": _file_hash(image_path),
                    "image_path": _run_relative_path(
                        context.run.workspace, image_path
                    ),
                    "machine_status": machine_status,
                    "human_disposition": human_disposition,
                    "review_decision_ref": applied_review_decision_ref,
                    "publication_transform": (
                        "none" if human_disposition == ACCEPT_WITH_OVERRIDE else None
                    ),
                }
            )
        verifier = context.adapters.get("workbook_verifier")
        publisher = SopPublisher(verifier=verifier) if verifier is not None else SopPublisher()
        delivery = context.run.workspace / "delivery"
        workbook = publisher.publish(tuple(sop_steps), delivery, pending=pending)
        publication = {
            "schema_version": "publication-result/v2",
            "pending": pending,
            "workbook": workbook.name,
            "delivery_directory": str(delivery),
            "steps": result_steps,
        }
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        return SkillHandlerOutput(
            status=SkillStatus.RETRYABLE,
            diagnostics=(Diagnostic("PUBLICATION_FAILED", str(error)),),
            retry_scope=RetryScope("publication", ("current-run",), 3),
            allowed_next=("publish-delivery",),
        )
    return SkillHandlerOutput(
        status=SkillStatus.QUESTIONED if pending else SkillStatus.PASSED,
        artifacts=(SkillArtifactValue("publication-result", publication),),
        diagnostics=(
            (Diagnostic("DELIVERY_NEEDS_REVIEW", "交付包含待确认步骤。"),)
            if pending
            else ()
        ),
        allowed_next=("resolve-step",) if pending else (),
    )


def resolve_step(
    context: SkillContext, invocation: SkillInvocation
) -> SkillHandlerOutput:
    step_id = str(invocation.parameters.get("step_id", "")).strip()
    raw_candidate = invocation.parameters.get("candidate_id")
    raw_instruction = invocation.parameters.get("instruction")
    raw_action = invocation.parameters.get("action")
    candidate_id = str(raw_candidate).strip() if raw_candidate is not None else ""
    instruction = str(raw_instruction).strip() if raw_instruction is not None else ""
    action = str(raw_action).strip() if raw_action is not None else ""
    supplied = sum(bool(value) for value in (candidate_id, instruction, action))
    if not step_id or supplied != 1:
        return _blocked(
            "INVALID_STEP_RESOLUTION",
            "释疑必须指定步骤，并且只能提供候选图、修正说明或人工决定之一。",
        )
    steps = context.run_store.list_steps(context.run.run_id)
    if step_id not in {step.step_id for step in steps}:
        return _blocked("STEP_NOT_FOUND", f"找不到待释疑步骤：{step_id}")
    revision_number = int(invocation.parameters.get("revision", 1))
    review_decision: dict[str, Any] | None = None
    try:
        if action:
            if action != ACCEPT_WITH_OVERRIDE:
                raise ValueError(f"不支持的人工决定：{action}")
            metadata = invocation.parameters.get("metadata", {})
            if not isinstance(metadata, Mapping) or not bool(
                metadata.get("acknowledged", False)
            ):
                raise ValueError("知情采用前必须明确确认已阅读机器校验结果")
            validation = context.read_json(
                _require_ref(invocation, "results/validation-")
            )
            selected_step = next(
                (
                    item
                    for item in validation.get("steps", [])
                    if str(item.get("step_id")) == step_id
                ),
                None,
            )
            if not isinstance(selected_step, Mapping):
                raise ValueError("当前校验结果中找不到待审核步骤")
            review_decision = create_human_override_decision(
                context.run.workspace,
                selected_step,
                step_id=step_id,
                revision=revision_number,
                reason=str(metadata.get("reason") or ""),
            )
            revision = StepRevision(
                revision_number,
                step_id,
                RevisionKind.PRESENTATION,
                {"candidate_id": HUMAN_OVERRIDE_IMAGE_ID},
            )
        elif candidate_id:
            if candidate_id == CURRENT_IMAGE_CANDIDATE_ID:
                validation = context.read_json(
                    _require_ref(invocation, "results/validation-")
                )
                selected_step = next(
                    (
                        item
                        for item in validation.get("steps", [])
                        if str(item.get("step_id")) == step_id
                    ),
                    None,
                )
                if selected_step is None or not _current_image_is_acceptable(
                    context.run.workspace, selected_step
                ):
                    raise ValueError("当前图片未通过基础几何硬门，不能直接采用")
            else:
                candidates = context.read_json(_require_ref(invocation, "candidate-set"))
                selected_group = next(
                    (
                        group
                        for group in candidates.get("groups", [])
                        if str(group.get("step_id")) == step_id
                    ),
                    None,
                )
                selected = next(
                    (
                        candidate
                        for candidate in (
                            selected_group.get("candidates", [])
                            if isinstance(selected_group, dict)
                            else []
                        )
                        if str(candidate.get("candidate_id")) == candidate_id
                    ),
                    None,
                )
                validation = context.read_json(
                    _require_ref(invocation, "results/validation-")
                )
                validation_step = next(
                    (
                        item
                        for item in validation.get("steps", [])
                        if str(item.get("step_id")) == step_id
                    ),
                    None,
                )
                selection_allowed = bool(
                    isinstance(selected_group, dict)
                    and selected_group.get("selection_allowed", False)
                    and isinstance(validation_step, dict)
                    and str(validation_step.get("category") or "")
                    not in {"hard_block", "system_retry"}
                )
                if selected is None or not selection_allowed:
                    raise ValueError("候选图不属于当前步骤或未通过基础几何硬门")
            revision = StepRevision(
                revision_number,
                step_id,
                RevisionKind.PRESENTATION,
                {"candidate_id": candidate_id},
            )
        else:
            validation = context.read_json(
                _require_ref(invocation, "results/validation-")
            )
            current_step = next(
                (
                    item
                    for item in validation.get("steps", [])
                    if str(item.get("step_id")) == step_id
                ),
                {},
            )
            current_step = dict(current_step)
            plan_ref = _optional_ref(invocation, "locked-render-plan")
            if plan_ref is not None:
                locked_plan = context.read_json(plan_ref)
                for index, item in enumerate(locked_plan.get("steps", []), start=1):
                    if str(item.get("step_id")) != step_id:
                        continue
                    current_step.update(
                        {
                            "step_number": index,
                            "step_title": str(item.get("title", "")),
                            "source_bom_rows": list(
                                item.get("source_bom_rows", [])
                            ),
                            "moving_occurrences": list(
                                item.get("moving_occurrences", [])
                            ),
                            "receiver_occurrences": list(
                                item.get("receiver_occurrences", [])
                            ),
                            "direction": item.get("receiver_normal_root"),
                            "current_camera_id": item.get("camera_id"),
                            "allowed_camera_ids": list(
                                item.get(
                                    "allowed_camera_ids",
                                    ("fixed_123", "fixed_456"),
                                )
                            ),
                        }
                    )
                    break
            normalized_ref = _optional_ref(invocation, "normalized-bom")
            if normalized_ref is not None:
                source_rows = {
                    int(value)
                    for value in current_step.get("source_bom_rows", [])
                }
                normalized = context.read_json(normalized_ref)
                current_step["source_bom_items"] = [
                    {
                        "bom_row": int(item["bom_row"]),
                        "name": str(item.get("name", "")),
                        "drawing_no": str(item.get("drawing_no", "")),
                        "material_code": str(item.get("material_code", "")),
                    }
                    for item in normalized.get("rows", [])
                    if int(item.get("bom_row", -1)) in source_rows
                ]
            metadata = invocation.parameters.get("metadata", {})
            structured_inputs = (
                metadata.get("structured_inputs", {})
                if isinstance(metadata, Mapping)
                else {}
            )
            revision = structured_step_revision(
                step_id,
                instruction,
                revision_number,
                structured_inputs=(
                    structured_inputs
                    if isinstance(structured_inputs, Mapping)
                    else {}
                ),
            )
            unresolved_code = str(current_step.get("error_code") or "")
            if (
                revision.kind is RevisionKind.PRESENTATION
                and unresolved_code
                in {"DIRECTION_SIGN_WEAK", "RECEIVER_NORMAL_NOT_AXIS_ALIGNED"}
            ):
                raise ValueError(
                    f"当前步骤仍有安装方向待确认项 {unresolved_code}；"
                    "仅调整视角、Zoom或箭头不会确认该方向。"
                    "请使用当前步骤提供的结构化轴符号表单。"
                )
            if (
                revision.kind is RevisionKind.PRESENTATION
                and not _validation_has_real_image(context.run.workspace, current_step)
            ):
                code = str(current_step.get("error_code") or "GEOMETRY_GATE_FAILED")
                raise ValueError(
                    "当前步骤未通过几何硬门 "
                    f"{code}；仅调整相机、Zoom或箭头没有生成新的真实图片。"
                    "请修复 BOM/Creo 映射或补齐结构化几何证据。"
                )
        validate_revision(revision)
        graph = StepDependencyGraph(
            {step.step_id: step.depends_on for step in steps}
        )
        invalidated = tuple(sorted(graph.invalidated_by(revision)))
    except (KeyError, RuntimeError, TypeError, ValueError) as error:
        return _blocked("STEP_RESOLUTION_REJECTED", str(error))
    artifacts = [
        SkillArtifactValue(
            "step-revision",
            {
                "schema_version": "step-revision/v1",
                "revision": revision.revision,
                "step_id": revision.step_id,
                "kind": revision.kind.value,
                "changes": revision.changes,
            },
        ),
        SkillArtifactValue(
            "invalidation-set",
            {
                "schema_version": "invalidation-set/v1",
                "step_revision": revision.revision,
                "steps": invalidated,
            },
        ),
    ]
    if review_decision is not None:
        artifacts.append(
            SkillArtifactValue("human-review-decision", review_decision)
        )
    return _passed(*artifacts)


def _passed(*artifacts: SkillArtifactValue) -> SkillHandlerOutput:
    return SkillHandlerOutput(SkillStatus.PASSED, tuple(artifacts))


def _blocked(code: str, message: str) -> SkillHandlerOutput:
    return SkillHandlerOutput(
        SkillStatus.BLOCKED,
        diagnostics=(Diagnostic(code, message),),
        allowed_next=(),
    )


def _current_image_is_acceptable(run_workspace: Path, item: Mapping[str, Any]) -> bool:
    if str(item.get("status")) != StepStatus.QUESTIONED.value:
        return False
    if str(item.get("category") or "") in {"hard_block", "system_retry"}:
        return False
    relative = str(item.get("image_path") or "").replace("\\", "/")
    if not bool(item.get("manual_acceptance_allowed", False)):
        return False
    path = (Path(run_workspace) / relative).resolve()
    try:
        path.relative_to(Path(run_workspace).resolve())
    except ValueError:
        return False
    return path.is_file() and "placeholder" not in path.name.casefold()


def _validation_has_real_image(
    run_workspace: Path, item: Mapping[str, Any]
) -> bool:
    relative = str(item.get("image_path") or "").replace("\\", "/")
    if not relative or str(item.get("image_kind")) == "placeholder":
        return False
    path = (Path(run_workspace) / relative).resolve()
    try:
        path.relative_to(Path(run_workspace).resolve())
    except ValueError:
        return False
    return path.is_file() and "placeholder" not in path.name.casefold()


def _historical_publication_image(
    run_workspace: Path,
    prior: Mapping[str, Any],
    current_image: Path,
    candidates: tuple[SopImage, ...],
) -> Path:
    """Recover the exact image accepted by an earlier successful resolution."""

    root = Path(run_workspace).resolve()
    relative = str(prior.get("image_path") or "").strip()
    if relative:
        selected = (root / relative).resolve()
        try:
            selected.relative_to(root)
        except ValueError as error:
            raise ValueError("历史已选图片路径逃逸运行区") from error
        if selected.is_file():
            return selected

    expected_hash = str(prior.get("output_hash") or "")
    possible = (Path(current_image), *(candidate.path for candidate in candidates))
    if expected_hash:
        for path in possible:
            if path.is_file() and _file_hash(path) == expected_hash:
                return path
        raise ValueError("历史已选图片缺失，禁止悄悄换回其他候选图")
    return Path(current_image)


def _run_relative_path(run_workspace: Path, image_path: Path) -> str:
    root = Path(run_workspace).resolve()
    selected = Path(image_path).resolve()
    try:
        relative = selected.relative_to(root)
    except ValueError as error:
        raise ValueError("出版图片必须位于当前运行区") from error
    return relative.as_posix()


def _require_ref(invocation: SkillInvocation, kind: str) -> str:
    for reference in invocation.input_refs:
        if kind in reference:
            return reference
    raise KeyError(f"{invocation.skill_name}缺少输入产物：{kind}")


def _optional_ref(invocation: SkillInvocation, marker: str) -> str | None:
    return next(
        (reference for reference in invocation.input_refs if marker in reference),
        None,
    )


def _apply_step_revision(plan: RenderPlan, payload: dict[str, Any]) -> RenderPlan:
    revision = StepRevision(
        revision=int(payload["revision"]),
        step_id=str(payload["step_id"]),
        kind=RevisionKind(payload["kind"]),
        changes=dict(payload["changes"]),
    )
    validate_revision(revision)
    tasks: list[RenderTask] = []
    found = False
    for task in plan.tasks:
        if task.step_id != revision.step_id:
            tasks.append(task)
            continue
        found = True
        contract = deepcopy(task.payload)
        changes = revision.changes
        frozen_fields = {
            "zoom",
            "pan",
            "explosion_distance",
            "arrow_anchor",
            "arrow_layout",
        } & set(changes)
        if frozen_fields:
            raise ValueError(
                "当前生产构图策略已冻结，禁止修改："
                + "、".join(sorted(frozen_fields))
            )
        occurrence_fields = {
            "moving_occurrences",
            "receiver_occurrences",
        } & set(changes)
        if occurrence_fields:
            raise ValueError(
                "occurrence 只能由确定性 BOM/CAD 映射修订，不能由文本说明写入"
            )
        presentation = dict(contract.get("presentation", {}))
        variants = [
            dict(value)
            for value in presentation.get("variants", [])
            if isinstance(value, dict)
        ]
        if not variants:
            raise ValueError("锁定渲染任务缺少视角变体")
        variant = dict(variants[0])
        variant["variant_id"] = f"step-revision-{revision.revision}"
        presentation["variants"] = [variant]
        contract["presentation"] = presentation
        rerender_option = str(changes.get("rerender_option") or "")
        if rerender_option:
            current_translation = [
                float(value)
                for value in contract.get("translation_vector_root", ())
            ]
            if len(current_translation) != 3 or not all(
                math.isfinite(value) for value in current_translation
            ):
                raise ValueError("锁定渲染任务缺少可修订的三维爆炸向量")
            if rerender_option == "normal_explosion":
                normal = _unit_vector(
                    contract.get("receiver_normal_root"), "Creo 承接面法向"
                )
                distance = _vector_length(current_translation)
                if distance <= 1.0e-9:
                    raise ValueError("锁定渲染任务的爆炸距离无效")
                contract["translation_vector_root"] = [
                    round(value * distance, 6) for value in normal
                ]
                _refresh_arrow_endpoints(contract)
            elif rerender_option == "reverse_explosion":
                contract["translation_vector_root"] = [
                    round(-value, 6) for value in current_translation
                ]
                _refresh_arrow_endpoints(contract)
            elif rerender_option == "switch_fixed_camera":
                current_camera = str(contract.get("camera_id") or "")
                allowed = [
                    str(value)
                    for value in contract.get(
                        "allowed_camera_ids", ("fixed_123", "fixed_456")
                    )
                ]
                if set(allowed) != {"fixed_123", "fixed_456"}:
                    raise ValueError("锁定渲染任务缺少固定双视角")
                if current_camera not in allowed:
                    raise ValueError("当前相机不属于固定双视角")
                next_camera = (
                    "fixed_456" if current_camera == "fixed_123" else "fixed_123"
                )
                catalog = contract.get("camera_catalog")
                if not isinstance(catalog, Mapping) or not isinstance(
                    catalog.get(next_camera), Mapping
                ):
                    raise ValueError("锁定渲染任务缺少另一台固定相机参数")
                variant["camera_id"] = next_camera
                presentation["variants"] = [variant]
                contract["presentation"] = presentation
                contract["camera_id"] = next_camera
                contract["camera"] = deepcopy(catalog[next_camera])
                contract.pop("camera_selection", None)
            elif rerender_option == "rebuild_exact_visibility":
                exact_visible = sorted(
                    {
                        str(value)
                        for value in contract.get("visible_occurrences", ())
                        if str(value)
                    }
                )
                required_visible = {
                    str(value)
                    for value in (
                        *contract.get("moving_occurrences", ()),
                        *contract.get("receiver_occurrences", ()),
                    )
                }
                if not exact_visible or not required_visible.issubset(exact_visible):
                    raise ValueError("锁定渲染任务的精确可见集不完整")
                contract["visible_occurrences"] = exact_visible
                contract["visibility_enforcement"] = {
                    "schema_version": "visibility-enforcement/v1",
                    "mode": "rebuild_exact_exclusions/v1",
                    "exact_visible_occurrences": exact_visible,
                    "revision": revision.revision,
                }
            elif rerender_option in {
                "increase_explosion_distance",
                "decrease_explosion_distance",
            }:
                scale = (
                    1.15
                    if rerender_option == "increase_explosion_distance"
                    else 0.85
                )
                contract["translation_vector_root"] = [
                    round(value * scale, 6) for value in current_translation
                ]
                _refresh_arrow_endpoints(contract)
            elif rerender_option == "focus_installation_region":
                selected_fit = presentation.get("native_selected_fit")
                if not isinstance(selected_fit, dict):
                    raise ValueError("锁定渲染任务缺少原生安装区域聚焦合同")
                selected_fit = dict(selected_fit)
                selected_fit["zoom_to_selected_level"] = 0.95
                presentation["native_selected_fit"] = selected_fit
                contract["presentation"] = presentation
            else:
                raise ValueError("二次生成选项没有对应的脚本重写映射")
            contract["manual_rerender_application"] = {
                "schema_version": "manual-rerender-application/v1",
                "option_id": rerender_option,
                "revision": revision.revision,
            }
        camera_resolution_option = str(
            changes.get("camera_resolution_option") or ""
        )
        if camera_resolution_option:
            if camera_resolution_option == "increase_bounded_explosion_distance":
                current_translation = [
                    float(value)
                    for value in contract.get("translation_vector_root", ())
                ]
                if len(current_translation) != 3 or not all(
                    math.isfinite(value) for value in current_translation
                ):
                    raise ValueError("锁定渲染任务缺少可修订的三维爆炸向量")
                prior_resolution = contract.get("camera_resolution", {})
                prior_scale = (
                    float(prior_resolution.get("explosion_scale", 1.0))
                    if isinstance(prior_resolution, Mapping)
                    else 1.0
                )
                if not math.isclose(prior_scale, 1.0, abs_tol=1.0e-9):
                    raise ValueError("本步骤已使用唯一允许的一级爆炸距离修订")
                next_scale = 1.15
                contract["translation_vector_root"] = [
                    round(value * next_scale, 6) for value in current_translation
                ]
                contract["camera_resolution"] = {
                    "schema_version": "camera-resolution-application/v1",
                    "option_id": camera_resolution_option,
                    "explosion_scale": next_scale,
                    "revision": revision.revision,
                }
                _refresh_arrow_endpoints(contract)
            elif camera_resolution_option == "focus_receiver_interface":
                selected_fit = presentation.get("native_selected_fit")
                if not isinstance(selected_fit, dict):
                    raise ValueError("锁定渲染任务缺少原生接口聚焦合同")
                selected_fit = dict(selected_fit)
                current_level = float(
                    selected_fit.get("zoom_to_selected_level", 0.0)
                )
                if not math.isclose(current_level, 0.85, abs_tol=1.0e-9):
                    raise ValueError("本步骤已使用唯一允许的接口聚焦修订")
                selected_fit["zoom_to_selected_level"] = 0.95
                presentation["native_selected_fit"] = selected_fit
                contract["presentation"] = presentation
                contract["camera_resolution"] = {
                    "schema_version": "camera-resolution-application/v1",
                    "option_id": camera_resolution_option,
                    "native_selected_fit_level": 0.95,
                    "revision": revision.revision,
                }
            else:
                raise ValueError("相机修复选项不属于当前有界选项集")
            # A prior decision is evidence for the superseded contract only.
            # The revised geometry/framing must generate four fresh rasters.
            contract.pop("camera_selection", None)
            contract["diagnostics"] = [
                code
                for code in contract.get("diagnostics", [])
                if code
                not in {
                    "MOVING_SET_OCCLUDED",
                    "MOVING_OCCURRENCE_OCCLUDED",
                    "RECEIVER_INTERFACE_OCCLUDED",
                    "RECEIVER_INTERFACE_PATCH_OCCLUDED",
                    "NO_ELIGIBLE_FIXED_CAMERA",
                }
            ]
        if "direction" in changes:
            requested_direction = _unit_vector(changes["direction"], "安装方向")
            measured_value = contract.get("receiver_normal_root")
            direction = _confirmed_receiver_direction(
                measured_value, requested_direction
            )
            contract["receiver_normal_root"] = direction
            contract["direction_confirmation"] = {
                "schema_version": "structured-axis-confirmation/v1",
                "requested_direction_root": requested_direction,
                "preserved_creo_axis": measured_value is not None,
            }
            current_translation = contract.get("translation_vector_root")
            distance = _vector_length(current_translation)
            if distance <= 1.0e-9:
                distance = 80.0
            display_selection = _revised_display_translation(
                contract, direction, distance
            )
            contract["translation_vector_root"] = list(
                display_selection["translation_vector_root"]
            )
            contract["display_translation_audit"] = display_selection
            contract["diagnostics"] = [
                code
                for code in contract.get("diagnostics", [])
                if code
                not in {
                    "DIRECTION_SIGN_WEAK",
                    "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
                    "CAMERA_RECEIVER_WRONG_HALF_SPACE",
                    "CAMERA_RECEIVER_SILHOUETTE",
                    "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
                }
            ]
        if "direction" in changes:
            _refresh_arrow_endpoints(contract)
        if "direction" in changes:
            _link_revision_cameras(
                contract,
                presentation,
                revision_number=revision.revision,
            )
            contract["diagnostics"] = [
                code
                for code in contract.get("diagnostics", [])
                if code
                not in {
                    "CAMERA_RECEIVER_WRONG_HALF_SPACE",
                    "CAMERA_RECEIVER_SILHOUETTE",
                    "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
                }
            ]
            contract["presentation"] = presentation
        contract["execution_mode"] = _revised_execution_mode(contract)
        depends_on = (
            tuple(str(value) for value in changes["depends_on"])
            if "depends_on" in changes
            else task.depends_on
        )
        tasks.append(
            replace(
                task,
                task_id=f"{task.step_id}-revision-{revision.revision:04d}",
                depends_on=depends_on,
                payload=contract,
            )
        )
    if not found:
        raise ValueError(f"找不到待修订渲染步骤：{revision.step_id}")
    return RenderPlan(plan.schema_version, tuple(tasks))


def _link_revision_cameras(
    contract: dict[str, Any],
    presentation: dict[str, Any],
    *,
    revision_number: int,
) -> None:
    """Relock one fixed camera from revised root-coordinate geometry."""

    catalog = contract.get("camera_catalog", {})
    if not isinstance(catalog, dict):
        raise ValueError("锁定渲染任务缺少相机目录")
    variants = [
        dict(value)
        for value in presentation.get("variants", [])
        if isinstance(value, dict)
    ]
    if not variants:
        raise ValueError("锁定渲染任务缺少视角变体")
    base_variant = variants[0]
    normal = _unit_vector(contract.get("receiver_normal_root"), "承接面法向")
    translation = [float(value) for value in contract.get("translation_vector_root", ())]
    if len(translation) != 3:
        raise ValueError("锁定渲染任务缺少三维爆炸向量")
    fixed_123 = catalog.get("fixed_123")
    fixed_456 = catalog.get("fixed_456")
    if not isinstance(fixed_123, dict) or not isinstance(fixed_456, dict):
        raise ValueError("锁定渲染任务缺少完整的固定双视角目录")
    basis = {
        "fixed_123_position_direction_root": _unit_vector(
            fixed_123.get("position_direction_root"), "fixed_123 视线方向"
        ),
        "fixed_456_position_direction_root": _unit_vector(
            fixed_456.get("position_direction_root"), "fixed_456 视线方向"
        ),
        "up_reference_root": _unit_vector(
            fixed_123.get("up_reference_root", [0.0, 0.0, 1.0]),
            "固定视角 UP",
        ),
    }
    stage_geometry = contract.get("stage_geometry_root", {})
    if not isinstance(stage_geometry, Mapping):
        stage_geometry = {}
    selected = select_fixed_camera_for_stage(
        basis,
        normal,
        translation,
        [
            dict(value)
            for value in stage_geometry.get("moving_bounds", [])
            if isinstance(value, Mapping)
        ],
        [
            dict(value)
            for value in stage_geometry.get("context_bounds", [])
            if isinstance(value, Mapping)
        ],
    )
    selected_id = str(selected["id"])
    linked = dict(base_variant)
    linked["camera_id"] = selected_id
    linked["variant_id"] = f"step-revision-{revision_number}-locked-camera"
    contract["camera_id"] = selected_id
    contract["camera"] = deepcopy(catalog[selected_id])
    presentation["variants"] = [linked]
    contract["attempted_actions"] = [
        "已按修订后的安装方向重新验证根坐标系双视角",
        f"唯一正式相机锁定为 {selected_id}",
    ]


def _unit_vector(value: Any, label: str) -> list[float]:
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label}必须是三维向量") from error
    if len(vector) != 3:
        raise ValueError(f"{label}必须是三维向量")
    length = _vector_length(vector)
    if length <= 1.0e-9:
        raise ValueError(f"{label}不能是零向量")
    return [round(item / length, 9) for item in vector]


def _confirmed_receiver_direction(
    measured_value: Any, requested_direction: list[float]
) -> list[float]:
    """Use structured input only to choose the sign of a measured Creo axis."""

    if measured_value is None:
        raise ValueError("缺少已测 Creo 承接轴，不能由结构化输入创建新的几何轴")
    measured = _unit_vector(measured_value, "Creo承接面法向")
    alignment = sum(
        measured[index] * requested_direction[index] for index in range(3)
    )
    if abs(alignment) < 0.95:
        raise ValueError(
            "结构化方向只能确认已测 Creo 承接轴的正负号，不能改变承接轴"
        )
    return [value if alignment >= 0.0 else -value for value in measured]


def _revised_display_translation(
    contract: Mapping[str, Any], direction: list[float], distance: float
) -> dict[str, Any]:
    """Re-run the same normal/lateral rule after a receiver-axis sign confirmation."""

    stage_geometry = contract.get("stage_geometry_root", {})
    if not isinstance(stage_geometry, Mapping):
        stage_geometry = {}
    moving_bounds = [
        dict(value)
        for value in stage_geometry.get("moving_bounds", [])
        if isinstance(value, Mapping)
    ]
    context_bounds = [
        dict(value)
        for value in stage_geometry.get("context_bounds", [])
        if isinstance(value, Mapping)
    ]
    contact_points = [
        item.get("complete_point_root")
        for item in contract.get("arrow_anchors", [])
        if isinstance(item, Mapping) and item.get("complete_point_root") is not None
    ]
    return select_display_translation(
        direction,
        distance,
        moving_bounds,
        context_bounds,
        contact_points,
    )


def _vector_length(value: Any) -> float:
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError):
        return 0.0
    if len(vector) != 3:
        return 0.0
    return sum(item * item for item in vector) ** 0.5


def _refresh_arrow_endpoints(contract: dict[str, Any]) -> None:
    translation = [float(value) for value in contract["translation_vector_root"]]
    anchors = []
    for item in contract.get("arrow_anchors", []):
        anchor = dict(item)
        complete = [float(value) for value in anchor["complete_point_root"]]
        anchor["expected_exploded_point_root"] = [
            round(complete[index] + translation[index], 6) for index in range(3)
        ]
        anchors.append(anchor)
    contract["arrow_anchors"] = anchors


def _revised_execution_mode(contract: Mapping[str, Any]) -> str:
    if contract.get("diagnostics"):
        return str(contract.get("execution_mode") or "placeholder")
    required = (
        contract.get("receiver_point_root"),
        contract.get("receiver_normal_root"),
        contract.get("translation_vector_root"),
        contract.get("camera_id"),
        contract.get("receiver_occurrences"),
        contract.get("constraint_ids"),
        contract.get("arrow_anchors"),
    )
    return "formal" if all(required) else str(
        contract.get("execution_mode") or "placeholder"
    )


def _file_hash(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _assert_input_manifest_unchanged(run: Any, manifest: Mapping[str, Any]) -> None:
    """Fail closed if BOM or any Creo model changed after intake."""

    if manifest.get("schema_version") != "input-manifest/v1":
        raise ValueError("输入清单版本无效，禁止继续渲染或出版")
    bom = manifest.get("bom")
    if not isinstance(bom, Mapping) or _file_hash(run.bom_file) != str(
        bom.get("sha256") or ""
    ):
        raise ValueError("BOM_SHA256_CHANGED：分析后 BOM 已变化")
    expected_items = manifest.get("cad")
    if not isinstance(expected_items, list):
        raise ValueError("输入清单缺少 CAD 全树哈希")
    expected = {
        str(item.get("relative_path")): str(item.get("sha256"))
        for item in expected_items
        if isinstance(item, Mapping)
    }
    current_paths = tuple(
        sorted(
            path
            for path in run.cad_directory.rglob("*")
            if path.is_file() and MODEL_PATTERN.match(path.name)
        )
    )
    current_names = {
        path.relative_to(run.cad_directory).as_posix() for path in current_paths
    }
    if current_names != set(expected):
        raise ValueError("SOURCE_CAD_TREE_CHANGED：Creo 模型文件集合已变化")
    changed = [
        relative
        for relative in sorted(expected)
        if _file_hash(run.cad_directory / Path(relative)) != expected[relative]
    ]
    if changed:
        raise ValueError(
            "SOURCE_CAD_HASH_CHANGED：分析后 Creo 模型已变化："
            + "、".join(changed[:10])
        )


def _creo_version(filename: str) -> int:
    tail = filename.rsplit(".", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _normalized(payload: dict[str, Any]) -> NormalizedBom:
    return NormalizedBom(
        schema_version=str(payload["schema_version"]),
        sheet_name=str(payload["sheet_name"]),
        header_row=int(payload["header_row"]),
        columns={str(key): int(value) for key, value in payload["columns"].items()},
        rows=tuple(NormalizedBomRow(**item) for item in payload["rows"]),
        sheet_candidates=tuple(str(value) for value in payload["sheet_candidates"]),
    )


def _inventory(payload: dict[str, Any]) -> ModelInventory:
    return ModelInventory(
        schema_version=str(payload["schema_version"]),
        files=tuple(ModelFile(**item) for item in payload["files"]),
        final_assembly=str(payload["final_assembly"]),
        assembly_candidates=tuple(payload["assembly_candidates"]),
        missing_bom_rows=tuple(int(value) for value in payload["missing_bom_rows"]),
        ambiguous_bom_rows=tuple(int(value) for value in payload["ambiguous_bom_rows"]),
        non_modeled_bom_rows=tuple(int(value) for value in payload["non_modeled_bom_rows"]),
    )


def _draft(payload: dict[str, Any]) -> DraftPlan:
    return DraftPlan(
        schema_version=str(payload["schema_version"]),
        final_assembly=str(payload["final_assembly"]),
        steps=tuple(
            DraftInstallationStep(
                step_id=str(item["step_id"]),
                main_process_id=str(item["main_process_id"]),
                title=str(item["title"]),
                depends_on=tuple(item["depends_on"]),
                source_bom_rows=tuple(int(value) for value in item["source_bom_rows"]),
                candidate_model_files=tuple(item["candidate_model_files"]),
                state_delta=tuple(item["state_delta"]),
                complete_state_hash=str(item["complete_state_hash"]),
                provisional=bool(item.get("provisional", True)),
            )
            for item in payload["steps"]
        ),
        checkpoint_interval=int(payload["checkpoint_interval"]),
    )


def _mapping(payload: dict[str, Any]) -> BomCadMap:
    return BomCadMap(
        schema_version=str(payload["schema_version"]),
        assembly_file=str(payload["assembly_file"]),
        rows=tuple(
            BomOccurrenceMapping(
                bom_row=int(item["bom_row"]),
                level=str(item["level"]),
                drawing_no=str(item["drawing_no"]),
                name=str(item["name"]),
                expected_quantity=(
                    int(item["expected_quantity"])
                    if item.get("expected_quantity") is not None
                    else None
                ),
                parent_bom_row=(
                    int(item["parent_bom_row"])
                    if item.get("parent_bom_row") is not None
                    else None
                ),
                occurrence_ids=tuple(item["occurrence_ids"]),
                status=str(item["status"]),
                evidence=str(item["evidence"]),
            )
            for item in payload["rows"]
        ),
        matched_rows=int(payload["matched_rows"]),
        ambiguous_rows=tuple(int(value) for value in payload["ambiguous_rows"]),
        missing_rows=tuple(int(value) for value in payload["missing_rows"]),
        quantity_mismatch_rows=tuple(
            int(value) for value in payload["quantity_mismatch_rows"]
        ),
    )


def _render_plan(payload: dict[str, Any]) -> RenderPlan:
    return RenderPlan(
        schema_version=str(payload["schema_version"]),
        tasks=tuple(
            RenderTask(
                task_id=str(item["task_id"]),
                step_id=str(item["step_id"]),
                main_process_id=str(item["main_process_id"]),
                depends_on=tuple(item.get("depends_on", [])),
                complete_state_hash=str(item["complete_state_hash"]),
                blocks_dependents_on_failure=bool(
                    item.get("blocks_dependents_on_failure", False)
                ),
                payload=dict(item.get("payload", {})),
            )
            for item in payload["tasks"]
        ),
    )


def _base_questions(
    bom: NormalizedBom, inventory: ModelInventory
) -> tuple[ClarificationItem, ...]:
    questions: list[ClarificationItem] = []
    if len(bom.sheet_candidates) > 1:
        questions.append(
            ClarificationItem(
                "select-bom-sheet",
                "CONFIRMATION",
                "检测到多个结构相同的BOM工作表，请确认本次使用哪一个。",
                bom.sheet_candidates,
                bom.sheet_name,
                ("候选工作表有效表头和物料行评分相同。",),
            )
        )
    if len(inventory.assembly_candidates) > 1:
        questions.append(
            ClarificationItem(
                "select-final-assembly",
                "CONFIRMATION",
                "检测到多个同等可信的最终总装，请确认本次使用哪一个。",
                inventory.assembly_candidates,
                inventory.final_assembly,
                ("候选ASM与BOM根物料匹配分数和版本相同。",),
            )
        )
    if inventory.missing_bom_rows:
        questions.append(
            ClarificationItem(
                "unmatched-bom-rows",
                "CONFIRMATION",
                "部分BOM行未完成文件级匹配，是否保留疑问并继续？",
                ("按推荐方案继续", "返回检查CAD文件夹"),
                "按推荐方案继续",
                ("涉及BOM行：" + ", ".join(map(str, inventory.missing_bom_rows[:20])),),
            )
        )
    if inventory.ambiguous_bom_rows:
        questions.append(
            ClarificationItem(
                "ambiguous-bom-models",
                "CONFIRMATION",
                "部分BOM行对应多个Creo模型，是否使用最高版本继续？",
                ("使用每个模型的最高版本", "返回检查CAD文件夹"),
                "使用每个模型的最高版本",
                ("涉及BOM行：" + ", ".join(map(str, inventory.ambiguous_bom_rows[:20])),),
            )
        )
    return tuple(questions)


def _safe_id(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )


def _write_placeholder(path: Path, step_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 1560, 1560), outline="#C00000", width=18)
    draw.line((250, 250, 1350, 1350), fill="#C00000", width=55)
    draw.line((1350, 250, 250, 1350), fill="#C00000", width=55)
    font = _font(70)
    draw.text((190, 700), "REGENERATION REQUIRED", fill="#C00000", font=font)
    draw.text((190, 810), step_id, fill="#333333", font=_font(44))
    image.save(path)


def _font(size: int):
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()
