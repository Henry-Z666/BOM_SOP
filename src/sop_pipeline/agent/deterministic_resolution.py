from __future__ import annotations

import re
from typing import Any, Mapping

from .step_revision import RevisionKind, StepRevision, validate_revision


def explicit_axis_direction(instruction: str) -> list[float] | None:
    """Return only an axis direction explicitly stated by the operator."""

    normalized = re.sub(r"\s+", "", str(instruction)).casefold()
    if not any(
        marker in normalized for marker in ("装入", "安装方向", "装配方向", "插入")
    ):
        return None
    axis_vectors = {
        "x轴正方向": [1.0, 0.0, 0.0],
        "正x方向": [1.0, 0.0, 0.0],
        "+x方向": [1.0, 0.0, 0.0],
        "x轴负方向": [-1.0, 0.0, 0.0],
        "负x方向": [-1.0, 0.0, 0.0],
        "-x方向": [-1.0, 0.0, 0.0],
        "y轴正方向": [0.0, 1.0, 0.0],
        "正y方向": [0.0, 1.0, 0.0],
        "+y方向": [0.0, 1.0, 0.0],
        "y轴负方向": [0.0, -1.0, 0.0],
        "负y方向": [0.0, -1.0, 0.0],
        "-y方向": [0.0, -1.0, 0.0],
        "z轴正方向": [0.0, 0.0, 1.0],
        "正z方向": [0.0, 0.0, 1.0],
        "+z方向": [0.0, 0.0, 1.0],
        "z轴负方向": [0.0, 0.0, -1.0],
        "负z方向": [0.0, 0.0, -1.0],
        "-z方向": [0.0, 0.0, -1.0],
    }
    matched = {
        tuple(vector)
        for marker, vector in axis_vectors.items()
        if marker in normalized
    }
    return list(next(iter(matched))) if len(matched) == 1 else None


def structured_step_revision(
    step_id: str,
    instruction: str,
    revision: int,
    *,
    structured_inputs: Mapping[str, Any] | None = None,
) -> StepRevision:
    """Translate one bounded review form into a validated step revision."""

    fields = {
        str(key): str(value).strip()
        for key, value in (structured_inputs or {}).items()
        if str(value).strip()
    }
    direction = _direction_from_fields(fields) or explicit_axis_direction(instruction)
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
        "纯脚本版本只接受结构化的正/负 X、Y、Z 安装方向；"
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
