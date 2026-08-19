from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class GateCategory(StrEnum):
    HARD_BLOCK = "hard_block"
    AUTO_REPAIR = "auto_repair"
    HUMAN_REVIEW = "human_review"
    SYSTEM_RETRY = "system_retry"


@dataclass(frozen=True)
class GatePolicy:
    code: str
    category: GateCategory
    user_message: str
    suggested_action: str
    retain_real_image: bool


@dataclass(frozen=True)
class GateDecision:
    primary_code: str
    failures: tuple[str, ...]
    category: GateCategory
    retain_real_image: bool


def _policy(
    code: str,
    category: GateCategory,
    user_message: str,
    suggested_action: str,
    *,
    retain_real_image: bool,
) -> GatePolicy:
    return GatePolicy(
        code=code,
        category=category,
        user_message=user_message,
        suggested_action=suggested_action,
        retain_real_image=retain_real_image,
    )


_POLICIES = {
    # Assembly truth: output must not imply an assembly state that was not
    # proven by the BOM and Creo occurrence model.
    "ASSEMBLY_HASH_MISMATCH": _policy(
        "ASSEMBLY_HASH_MISMATCH",
        GateCategory.HARD_BLOCK,
        "装配模型已变化，当前步骤不能继续沿用。",
        "重新分析最新模型并确认装配版本。",
        retain_real_image=False,
    ),
    "MOVING_OCCURRENCE_UNRESOLVED": _policy(
        "MOVING_OCCURRENCE_UNRESOLVED",
        GateCategory.HARD_BLOCK,
        "未能在总装中唯一定位待安装零件。",
        "指定 moving_occurrences，或修正 BOM 与模型名称。",
        retain_real_image=False,
    ),
    "RECEIVER_OCCURRENCE_UNRESOLVED": _policy(
        "RECEIVER_OCCURRENCE_UNRESOLVED",
        GateCategory.HARD_BLOCK,
        "未能在总装中唯一定位承接零件。",
        "指定 receiver_occurrences，并确认承接关系。",
        retain_real_image=False,
    ),
    "NO_NATIVE_RECEIVER_GEOMETRY": _policy(
        "NO_NATIVE_RECEIVER_GEOMETRY",
        GateCategory.HARD_BLOCK,
        "承接零件缺少可验证的原生安装几何。",
        "指定正确承接零件和安装方向后重新分析。",
        retain_real_image=False,
    ),
    "BOM_QUANTITY_MISMATCH": _policy(
        "BOM_QUANTITY_MISMATCH",
        GateCategory.HARD_BLOCK,
        "BOM 数量与模型中的装配数量不一致。",
        "修正 BOM 或模型数量后重新分析。",
        retain_real_image=False,
    ),
    "VISIBLE_SET_MISMATCH": _policy(
        "VISIBLE_SET_MISMATCH",
        GateCategory.HARD_BLOCK,
        "渲染中的可见零件集合与锁定计划不一致。",
        "恢复锁定可见集并重新渲染。",
        retain_real_image=False,
    ),
    "ROTATION_CHANGED": _policy(
        "ROTATION_CHANGED",
        GateCategory.HARD_BLOCK,
        "爆炸步骤改变了零件旋转，违反纯平移约束。",
        "撤销旋转，只沿锁定安装轴平移。",
        retain_real_image=False,
    ),
    "ARROW_AUDIT_INVALID": _policy(
        "ARROW_AUDIT_INVALID",
        GateCategory.HARD_BLOCK,
        "箭头三维端点或方向未通过同一 CAD 点审计。",
        "从锁定 CAD 锚点重新生成箭头。",
        retain_real_image=False,
    ),
    "ARROW_COVERAGE_INVALID": _policy(
        "ARROW_COVERAGE_INVALID",
        GateCategory.HARD_BLOCK,
        "安装箭头没有覆盖全部移动件。",
        "为每个移动 occurrence 重新生成对应箭头。",
        retain_real_image=False,
    ),
    "TRANSLATION_AUDIT_INVALID": _policy(
        "TRANSLATION_AUDIT_INVALID",
        GateCategory.HARD_BLOCK,
        "爆炸位移未通过纯平移审计。",
        "恢复锁定方向和位移向量后重新渲染。",
        retain_real_image=False,
    ),
    "CAMERA_NOT_FIXED": _policy(
        "CAMERA_NOT_FIXED",
        GateCategory.HARD_BLOCK,
        "渲染使用了锁定目录之外的相机。",
        "恢复 fixed_123 或 fixed_456 后重新渲染。",
        retain_real_image=False,
    ),
    "ARROW_SURFACE_ANCHOR_UNAVAILABLE": _policy(
        "ARROW_SURFACE_ANCHOR_UNAVAILABLE",
        GateCategory.HARD_BLOCK,
        "箭头缺少可审计的模型表面锚点。",
        "补全移动件表面锚点后重新编译渲染任务。",
        retain_real_image=False,
    ),
    "FORBIDDEN_CONTENT_VISIBLE": _policy(
        "FORBIDDEN_CONTENT_VISIBLE",
        GateCategory.HARD_BLOCK,
        "图片中出现了当前步骤不允许显示的装配内容。",
        "恢复锁定可见集后重新渲染。",
        retain_real_image=False,
    ),
    "ARROW_COVERAGE_MISMATCH": _policy(
        "ARROW_COVERAGE_MISMATCH",
        GateCategory.HARD_BLOCK,
        "箭头覆盖对象与移动件集合不一致。",
        "按移动 occurrence 集合重新生成箭头。",
        retain_real_image=False,
    ),
    "ARROW_VECTOR_MISMATCH": _policy(
        "ARROW_VECTOR_MISMATCH",
        GateCategory.HARD_BLOCK,
        "箭头方向与锁定安装位移不一致。",
        "从锁定安装向量重新计算箭头端点。",
        retain_real_image=False,
    ),
    "ARROW_ANCHOR_MISSING": _policy(
        "ARROW_ANCHOR_MISSING",
        GateCategory.HARD_BLOCK,
        "移动件缺少箭头锚点。",
        "补全 CAD 锚点后重新渲染。",
        retain_real_image=False,
    ),
    "ARROW_OUT_OF_FRAME": _policy(
        "ARROW_OUT_OF_FRAME",
        GateCategory.HARD_BLOCK,
        "箭头审计点落在输出画面之外。",
        "修正相机或锚点后重新渲染。",
        retain_real_image=False,
    ),
    "ARROW_OVERLAP": _policy(
        "ARROW_OVERLAP",
        GateCategory.HARD_BLOCK,
        "多个安装箭头发生不可接受的重叠。",
        "重新选择可审计的箭头布局。",
        retain_real_image=False,
    ),
    # Bounded deterministic repairs.
    "CAMERA_RECEIVER_WRONG_HALF_SPACE": _policy(
        "CAMERA_RECEIVER_WRONG_HALF_SPACE",
        GateCategory.AUTO_REPAIR,
        "当前相机位于承接面错误一侧。",
        "自动切换到另一固定相机并重新计算投影。",
        retain_real_image=True,
    ),
    "CAMERA_RECEIVER_SILHOUETTE": _policy(
        "CAMERA_RECEIVER_SILHOUETTE",
        GateCategory.AUTO_REPAIR,
        "当前相机使承接面接近侧视，安装关系不清晰。",
        "自动切换到另一固定相机；仍不清晰时保留图片人工复核。",
        retain_real_image=True,
    ),
    "EXPLOSION_NOT_VISIBLE_IN_CAMERA": _policy(
        "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
        GateCategory.AUTO_REPAIR,
        "爆炸位移在当前相机中的投影过小。",
        "切换固定相机或在限值内增加爆炸距离。",
        retain_real_image=True,
    ),
    "DIRECTION_SIGN_WEAK": _policy(
        "DIRECTION_SIGN_WEAK",
        GateCategory.AUTO_REPAIR,
        "安装方向正负号证据不足。",
        "尝试相反方向和两台固定相机，保留最佳结果供复核。",
        retain_real_image=True,
    ),
    "RECEIVER_NORMAL_NOT_AXIS_ALIGNED": _policy(
        "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
        GateCategory.AUTO_REPAIR,
        "承接面法向与当前安装轴不充分对齐。",
        "重新选择最近安装轴并联动固定相机。",
        retain_real_image=True,
    ),
    # Presentation quality never discards a real image by itself.
    "SUBJECT_TOO_SMALL": _policy(
        "SUBJECT_TOO_SMALL",
        GateCategory.HUMAN_REVIEW,
        "主体在画面中偏小。",
        "人工确认，或提高 zoom 后局部重渲染。",
        retain_real_image=True,
    ),
    "SUBJECT_TOO_LARGE": _policy(
        "SUBJECT_TOO_LARGE",
        GateCategory.HUMAN_REVIEW,
        "主体在画面中偏大。",
        "人工确认，或降低 zoom 后局部重渲染。",
        retain_real_image=True,
    ),
    "EXCESSIVE_CONTEXT_CLIPPING": _policy(
        "EXCESSIVE_CONTEXT_CLIPPING",
        GateCategory.HUMAN_REVIEW,
        "装配上下文存在较多裁切。",
        "人工确认，或降低 zoom、调整 pan。",
        retain_real_image=True,
    ),
    "ARROW_NOT_VISIBLE": _policy(
        "ARROW_NOT_VISIBLE",
        GateCategory.HUMAN_REVIEW,
        "安装箭头在图片中不够清晰。",
        "调整箭头布局后局部重渲染。",
        retain_real_image=True,
    ),
    "ARROW_TOO_SMALL": _policy(
        "ARROW_TOO_SMALL",
        GateCategory.HUMAN_REVIEW,
        "安装箭头偏小。",
        "增大箭头显示尺寸后局部重渲染。",
        retain_real_image=True,
    ),
    "ARROW_CLIPPED": _policy(
        "ARROW_CLIPPED",
        GateCategory.HUMAN_REVIEW,
        "安装箭头被画面边缘裁切。",
        "调整 pan 或箭头布局后局部重渲染。",
        retain_real_image=True,
    ),
    "ACTIVITY_NOT_CENTERED": _policy(
        "ACTIVITY_NOT_CENTERED",
        GateCategory.HUMAN_REVIEW,
        "安装活动区域没有充分居中。",
        "人工确认，或调整 pan 后局部重渲染。",
        retain_real_image=True,
    ),
    "ARROW_NOT_CENTERED": _policy(
        "ARROW_NOT_CENTERED",
        GateCategory.HUMAN_REVIEW,
        "安装箭头没有充分居中。",
        "人工确认，或调整箭头布局。",
        retain_real_image=True,
    ),
    "SUBJECT_CLIPPED": _policy(
        "SUBJECT_CLIPPED",
        GateCategory.HUMAN_REVIEW,
        "安装主体接近或触及画面边缘。",
        "人工确认，或降低 zoom、调整 pan。",
        retain_real_image=True,
    ),
    "SCREEN_CENTERING_UNSOLVABLE": _policy(
        "SCREEN_CENTERING_UNSOLVABLE",
        GateCategory.HUMAN_REVIEW,
        "自动居中已达到有界修复上限。",
        "保留当前图片人工复核，必要时明确给出 pan。",
        retain_real_image=True,
    ),
    "ZOOM_RECOVERY_UNSOLVABLE": _policy(
        "ZOOM_RECOVERY_UNSOLVABLE",
        GateCategory.HUMAN_REVIEW,
        "自动缩放已达到有界修复上限。",
        "保留当前图片人工复核，必要时明确给出 zoom。",
        retain_real_image=True,
    ),
    "FRAMING_FRAME_BUDGET_EXCEEDED": _policy(
        "FRAMING_FRAME_BUDGET_EXCEEDED",
        GateCategory.HUMAN_REVIEW,
        "自动构图已达到允许的探测帧数量。",
        "保留当前图片人工复核，或明确给出 zoom 与 pan。",
        retain_real_image=True,
    ),
    # Process failures trigger rollback/retry instead of judging assembly truth.
    "SUBJECT_NOT_DETECTED": _policy(
        "SUBJECT_NOT_DETECTED",
        GateCategory.SYSTEM_RETRY,
        "渲染帧中未检测到主体，可能是相机或渲染状态异常。",
        "回退本次相机、缩放和平移修改，再重试渲染。",
        retain_real_image=False,
    ),
    "RENDER_OUTPUT_MISSING": _policy(
        "RENDER_OUTPUT_MISSING",
        GateCategory.SYSTEM_RETRY,
        "Creo 未产生预期的渲染文件。",
        "检查输出路径和 Creo 会话后重试。",
        retain_real_image=False,
    ),
    "RENDER_FRAME_INVALID": _policy(
        "RENDER_FRAME_INVALID",
        GateCategory.SYSTEM_RETRY,
        "渲染文件无法作为有效图像读取。",
        "清理本次临时文件并重试。",
        retain_real_image=False,
    ),
    "CREO_TIMEOUT": _policy(
        "CREO_TIMEOUT",
        GateCategory.SYSTEM_RETRY,
        "Creo 渲染超时。",
        "保留上一张有效图片，重启或恢复 Creo 后重试。",
        retain_real_image=True,
    ),
    "CREO_PROCESS_ERROR": _policy(
        "CREO_PROCESS_ERROR",
        GateCategory.SYSTEM_RETRY,
        "Creo 执行进程异常退出。",
        "保留上一张有效图片，检查 Creo 日志后重试。",
        retain_real_image=True,
    ),
    "CREO_RENDER_FAILED": _policy(
        "CREO_RENDER_FAILED",
        GateCategory.SYSTEM_RETRY,
        "Creo 未能完成本次渲染。",
        "保留上一张有效图片并局部重试。",
        retain_real_image=True,
    ),
    "CREO_RUNTIME_CONFIG_MISSING": _policy(
        "CREO_RUNTIME_CONFIG_MISSING",
        GateCategory.SYSTEM_RETRY,
        "当前运行批次缺少 Creo 运行时配置。",
        "恢复或重新选择 Creo 运行时后重试。",
        retain_real_image=True,
    ),
    "RENDER_FAILED": _policy(
        "RENDER_FAILED",
        GateCategory.SYSTEM_RETRY,
        "渲染工作进程返回了可重试失败。",
        "保留上一张有效图片并重试。",
        retain_real_image=True,
    ),
    "PRESENTATION_CONTRACT_INVALID": _policy(
        "PRESENTATION_CONTRACT_INVALID",
        GateCategory.SYSTEM_RETRY,
        "相机、缩放或平移参数不符合渲染合同。",
        "回退无效参数并按允许范围重新提交。",
        retain_real_image=True,
    ),
    "CAMERA_GEOMETRY_INVALID": _policy(
        "CAMERA_GEOMETRY_INVALID",
        GateCategory.SYSTEM_RETRY,
        "固定相机或安装向量缺少可计算的三维参数。",
        "回退本次修订并重新编译相机合同。",
        retain_real_image=True,
    ),
    "FRAMING_PROFILE_CONTRACT_INVALID": _policy(
        "FRAMING_PROFILE_CONTRACT_INVALID",
        GateCategory.SYSTEM_RETRY,
        "构图参数不符合锁定渲染合同。",
        "回退相机、zoom、pan 后重新编译。",
        retain_real_image=True,
    ),
    "SCREEN_CENTERING_STATE_UNAVAILABLE": _policy(
        "SCREEN_CENTERING_STATE_UNAVAILABLE",
        GateCategory.SYSTEM_RETRY,
        "自动居中所需的运行状态不可用。",
        "保留当前图片并重新建立居中状态。",
        retain_real_image=True,
    ),
    "SCREEN_CENTERING_EVIDENCE_MISSING": _policy(
        "SCREEN_CENTERING_EVIDENCE_MISSING",
        GateCategory.SYSTEM_RETRY,
        "自动居中缺少可计算的屏幕证据。",
        "回退本轮探测并重试。",
        retain_real_image=True,
    ),
    "SCREEN_CENTERING_PROBE_INVALID": _policy(
        "SCREEN_CENTERING_PROBE_INVALID",
        GateCategory.SYSTEM_RETRY,
        "自动居中探测帧无效。",
        "清理探测帧并重试。",
        retain_real_image=True,
    ),
    "IMAGE_DIMENSIONS_MISMATCH": _policy(
        "IMAGE_DIMENSIONS_MISMATCH",
        GateCategory.SYSTEM_RETRY,
        "渲染图片尺寸与合同不一致。",
        "恢复锁定输出尺寸后重试。",
        retain_real_image=False,
    ),
    "IMAGE_INVALID": _policy(
        "IMAGE_INVALID",
        GateCategory.SYSTEM_RETRY,
        "渲染图片无法读取或内容无效。",
        "删除本次无效输出并重试。",
        retain_real_image=False,
    ),
}

_CATEGORY_PRIORITY = {
    GateCategory.HUMAN_REVIEW: 0,
    GateCategory.AUTO_REPAIR: 1,
    GateCategory.SYSTEM_RETRY: 2,
    GateCategory.HARD_BLOCK: 3,
}


def gate_policy(code: str) -> GatePolicy:
    normalized = str(code or "").strip().upper()
    known = _POLICIES.get(normalized)
    if known is not None:
        return known
    return _policy(
        normalized or "UNKNOWN_GATE",
        GateCategory.HARD_BLOCK,
        f"门禁代码 {normalized or 'UNKNOWN_GATE'} 尚未分类，为避免错误装配已停止。",
        "请将该代码加入统一门禁策略并给出明确处置方式。",
        retain_real_image=False,
    )


def classify_failures(codes: Iterable[str]) -> GateDecision:
    failures = tuple(
        normalized
        for code in codes
        if (normalized := str(code or "").strip().upper())
    )
    if not failures:
        return GateDecision(
            primary_code="",
            failures=(),
            category=GateCategory.HUMAN_REVIEW,
            retain_real_image=True,
        )
    policies = tuple(gate_policy(code) for code in failures)
    primary = max(
        policies,
        key=lambda item: _CATEGORY_PRIORITY[item.category],
    )
    return GateDecision(
        primary_code=primary.code,
        failures=failures,
        category=primary.category,
        retain_real_image=all(policy.retain_real_image for policy in policies),
    )
