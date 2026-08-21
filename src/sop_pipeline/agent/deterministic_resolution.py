from __future__ import annotations

from typing import Any, Mapping

from .step_revision import RevisionKind, StepRevision, validate_revision


_CAMERA_RESOLUTION_CHOICES = {
    "增加一级爆炸距离后重新比较": (
        "increase_bounded_explosion_distance",
        RevisionKind.INSTALLATION_GEOMETRY,
    ),
    "聚焦移动件与安装接口后重新比较": (
        "focus_receiver_interface",
        RevisionKind.PRESENTATION,
    ),
}

_RERENDER_CHOICES = {
    "normal_explosion": RevisionKind.INSTALLATION_GEOMETRY,
    "reverse_explosion": RevisionKind.INSTALLATION_GEOMETRY,
    "switch_fixed_camera": RevisionKind.PRESENTATION,
    "rebuild_exact_visibility": RevisionKind.PRESENTATION,
    "increase_explosion_distance": RevisionKind.INSTALLATION_GEOMETRY,
    "decrease_explosion_distance": RevisionKind.INSTALLATION_GEOMETRY,
    "focus_installation_region": RevisionKind.PRESENTATION,
}


def structured_step_revision(
    step_id: str,
    instruction: str,
    revision: int,
    *,
    structured_inputs: Mapping[str, Any] | None = None,
) -> StepRevision:
    """Translate one bounded review form into a validated step revision."""

    del instruction

    fields = {
        str(key): str(value).strip()
        for key, value in (structured_inputs or {}).items()
        if str(value).strip()
    }
    rerender_option = fields.get("rerender_option")
    if rerender_option:
        kind = _RERENDER_CHOICES.get(rerender_option)
        if kind is None:
            raise ValueError("二次生成选项不属于当前脚本重写映射")
        return validate_revision(
            StepRevision(
                revision=revision,
                step_id=step_id,
                kind=kind,
                changes={"rerender_option": rerender_option},
            )
        )
    camera_choice = fields.get("camera_resolution_option")
    if camera_choice:
        selected = _CAMERA_RESOLUTION_CHOICES.get(camera_choice)
        if selected is None:
            raise ValueError("相机修复选项不属于当前有界选项集")
        option_id, kind = selected
        return validate_revision(
            StepRevision(
                revision=revision,
                step_id=step_id,
                kind=kind,
                changes={"camera_resolution_option": option_id},
            )
        )
    direction = _direction_from_fields(fields)
    if direction is not None:
        return validate_revision(
            StepRevision(
                revision=revision,
                step_id=step_id,
                kind=RevisionKind.INSTALLATION_GEOMETRY,
                changes={"direction": direction},
            )
        )
    if "camera_id" in fields:
        raise ValueError("固定视角已由根坐标系和承接面法向锁定，不能在渲染后修改")
    if {"moving_name", "receiver_name"} & set(fields):
        raise ValueError("安装对象必须通过 BOM/Creo 唯一映射修订，不能由文本名称猜测")
    raise ValueError(
        "纯脚本版本不从自由文本生成坐标，只接受结构化的正/负 X、Y、Z 安装方向；"
        "视角和 occurrence 由锁定的 BOM/Creo 事实决定"
    )


def _direction_from_fields(fields: Mapping[str, str]) -> list[float] | None:
    axis = str(fields.get("axis", "")).upper()
    sign = str(fields.get("sign", ""))
    if not axis and not sign:
        return None
    if axis not in {"X", "Y", "Z"} or sign not in {"正", "负"}:
        raise ValueError("安装方向表单必须提供 X/Y/Z 轴和正/负方向")
    value = 1.0 if sign == "正" else -1.0
    result = [0.0, 0.0, 0.0]
    result["XYZ".index(axis)] = value
    return result
