from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from sop_pipeline.camera_visibility import CAMERA_VISIBILITY_AUDIT_ENABLED


ACCEPT_WITH_OVERRIDE = "accept_with_override"
HUMAN_OVERRIDE_IMAGE_ID = "human-override-current-image"


_DIRECTION_CODES = {
    "DIRECTION_SIGN_WEAK",
    "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
}
_OCCURRENCE_CODES = {
    "MOVING_OCCURRENCE_UNRESOLVED",
    "RECEIVER_OCCURRENCE_UNRESOLVED",
    "NO_NATIVE_RECEIVER_GEOMETRY",
}
_CAMERA_VISIBILITY_CODES = {
    "MOVING_SET_OCCLUDED",
    "MOVING_OCCURRENCE_OCCLUDED",
    "RECEIVER_INTERFACE_OCCLUDED",
    "RECEIVER_INTERFACE_PATCH_OCCLUDED",
    "NO_ELIGIBLE_FIXED_CAMERA",
}


def prepare_review_step(
    run_workspace: Path,
    validation_step: Mapping[str, Any],
    plan_step: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the complete, UI-independent review contract for one step.

    Machine qualification and human authority are intentionally independent:
    a real image may be reviewed and explicitly accepted without rewriting the
    machine result that caused it to enter this package.
    """

    relative = str(
        validation_step.get("retained_image")
        or validation_step.get("image_path")
        or ""
    )
    image_path = _safe_run_image(run_workspace, relative)
    has_real_image = bool(
        image_path is not None
        and image_path.is_file()
        and "placeholder" not in image_path.name.casefold()
    )
    category = str(validation_step.get("category") or "")
    normal_acceptance = bool(
        validation_step.get("manual_acceptance_allowed", False)
    ) and category not in {"hard_block", "system_retry"}
    machine_status = str(validation_step.get("status") or "")
    primary_code = str(
        validation_step.get("primary_code")
        or validation_step.get("error_code")
        or ""
    ).upper()
    failures = [
        str(item.get("code") or "").upper()
        for item in validation_step.get("failures", [])
        if isinstance(item, Mapping) and str(item.get("code") or "").strip()
    ]
    if primary_code and primary_code not in failures:
        failures.insert(0, primary_code)

    actions = ["correct_and_rerender", "defer"]
    if normal_acceptance and has_real_image:
        actions.insert(0, "accept")
    elif has_real_image:
        actions.insert(0, ACCEPT_WITH_OVERRIDE)

    guided_form = _guided_form(primary_code, plan_step or {})
    if guided_form is None and normal_acceptance and has_real_image:
        guided_form = _manual_rerender_form()

    return {
        "schema_version": "step-review-package/v1",
        "machine_status": machine_status,
        "machine_category": category,
        "machine_failures": failures,
        "has_real_image": has_real_image,
        "image_path": str(image_path) if image_path is not None else "",
        "normal_acceptance_allowed": normal_acceptance and has_real_image,
        "override_allowed": has_real_image,
        "available_actions": actions,
        "guided_form": guided_form,
        "attempt_history": _attempt_history(validation_step),
    }


def create_human_override_decision(
    run_workspace: Path,
    validation_step: Mapping[str, Any],
    *,
    step_id: str,
    revision: int,
    reason: str,
) -> dict[str, Any]:
    """Validate and record an informed decision to publish an original image."""

    prepared = prepare_review_step(run_workspace, validation_step)
    if not prepared["override_allowed"]:
        raise ValueError("当前步骤没有真实图片，不能人工采用")
    image_path = Path(str(prepared["image_path"])).resolve()
    relative = image_path.relative_to(Path(run_workspace).resolve())
    digest = "sha256:" + sha256(image_path.read_bytes()).hexdigest()
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        normalized_reason = "用户已查看机器校验结果，并明确决定采用当前原始图片。"
    return {
        "schema_version": "human-review-decision/v1",
        "revision": revision,
        "step_id": step_id,
        "decision": ACCEPT_WITH_OVERRIDE,
        "machine_status": prepared["machine_status"],
        "machine_category": prepared["machine_category"],
        "acknowledged_failures": prepared["machine_failures"],
        "selected_image_path": str(relative).replace("\\", "/"),
        "selected_image_sha256": digest,
        "reason": normalized_reason,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "publication_transform": "none",
        "watermark": False,
    }


def _guided_form(code: str, plan_step: Mapping[str, Any]) -> dict[str, Any] | None:
    if code in _DIRECTION_CODES:
        axis_and_sign = _axis_and_sign(plan_step.get("receiver_normal_root"))
        if axis_and_sign is None:
            return None
        axis, sign = axis_and_sign
        return {
            "schema_version": "guided-review-form/v1",
            "title": "确认安装方向",
            "instruction": "Creo 已锁定安装轴；只需确认该轴的正负方向。",
            "sentence_template": "该零件沿设备总装{axis}轴{sign}方向装入",
            "submit_label": "按所选方向重新生成",
            "fields": [
                {
                    "name": "axis",
                    "label": "安装轴",
                    "type": "choice",
                    "options": [axis],
                    "default": axis,
                    "required": True,
                },
                {
                    "name": "sign",
                    "label": "装入方向",
                    "type": "choice",
                    "options": ["正", "负"],
                    "default": sign,
                    "required": True,
                },
            ],
        }
    if code in _CAMERA_VISIBILITY_CODES:
        if not CAMERA_VISIBILITY_AUDIT_ENABLED:
            return None
        return {
            "schema_version": "guided-review-form/v1",
            "title": "选择二次生成方式",
            "instruction": (
                "两台固定相机都未通过可见性门禁；只选择修复目标，"
                "系统会修改内部合同并重新审计。"
            ),
            "sentence_template": "按“{camera_resolution_option}”重新生成本步骤",
            "submit_label": "按所选方式重新生成",
            "fields": [
                {
                    "name": "camera_resolution_option",
                    "label": "修复方式",
                    "type": "choice",
                    "options": [
                        "增加一级爆炸距离后重新比较",
                        "聚焦移动件与安装接口后重新比较",
                    ],
                    "default": "增加一级爆炸距离后重新比较",
                    "required": True,
                }
            ],
        }
    if code in _OCCURRENCE_CODES:
        return None
    return None


def _manual_rerender_form() -> dict[str, Any]:
    """Offer only choices that have a real, deterministic task rewrite."""

    return {
        "schema_version": "manual-rerender-form/v1",
        "title": "选择图片问题并二次生成",
        "instruction": (
            "请选择最主要的问题。系统会按固定映射重写当前步骤的渲染任务，"
            "不会解析备注或要求用户编辑脚本。"
        ),
        "sentence_template": "按所选问题重写渲染任务并重新生成当前步骤",
        "submit_label": "按所选问题二次生成",
        "fields": [
            {
                "name": "rerender_option",
                "label": "图片问题",
                "type": "choice",
                "options": [
                    {
                        "value": "normal_explosion",
                        "label": "法向爆炸（沿 Creo 承接面法向）",
                    },
                    {
                        "value": "reverse_explosion",
                        "label": "爆炸方向相反",
                    },
                    {
                        "value": "switch_fixed_camera",
                        "label": "视角选错，换另一个固定视角",
                    },
                    {
                        "value": "rebuild_exact_visibility",
                        "label": "未能完全屏蔽后续件",
                    },
                    {
                        "value": "increase_explosion_distance",
                        "label": "零件被遮挡或距离太小，增大爆炸距离",
                    },
                    {
                        "value": "decrease_explosion_distance",
                        "label": "爆炸距离太大，缩短爆炸距离",
                    },
                    {
                        "value": "focus_installation_region",
                        "label": "安装区域太小，放大并聚焦",
                    },
                ],
                "default": "normal_explosion",
                "required": True,
            }
        ],
    }


def _axis_and_sign(value: object) -> tuple[str, str] | None:
    try:
        vector = [float(item) for item in value]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if len(vector) != 3 or not any(abs(item) > 1.0e-12 for item in vector):
        return None
    index = max(range(3), key=lambda item: abs(vector[item]))
    return "XYZ"[index], "正" if vector[index] >= 0.0 else "负"


def _safe_run_image(run_workspace: Path, relative: str) -> Path | None:
    if not relative:
        return None
    root = Path(run_workspace).resolve()
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _attempt_history(step: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = [str(value) for value in step.get("attempted_actions", []) if str(value)]
    return [
        {
            "sequence": index,
            "action": action,
        }
        for index, action in enumerate(actions, start=1)
    ]
