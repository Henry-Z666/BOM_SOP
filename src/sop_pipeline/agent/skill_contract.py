from __future__ import annotations

from dataclasses import dataclass
import re

from .models import ArtifactRef, SkillStatus


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.code):
            raise ValueError("diagnostic code 必须是稳定的大写错误代码")
        if not self.message.strip():
            raise ValueError("diagnostic message 不能为空")


@dataclass(frozen=True)
class RetryScope:
    selector_type: str
    selectors: tuple[str, ...]
    max_attempts: int

    def __post_init__(self) -> None:
        if not self.selector_type.strip() or not self.selectors:
            raise ValueError("retry_scope 必须包含选择器类型和目标")
        if self.max_attempts < 1:
            raise ValueError("retry_scope max_attempts 必须大于零")


@dataclass(frozen=True)
class SkillResult:
    schema_version: str
    skill: str
    run_id: str
    status: SkillStatus
    input_fingerprint: str
    artifacts: tuple[ArtifactRef, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    retry_scope: RetryScope | None = None
    allowed_next: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != "agent-skill-result/v1":
            raise ValueError(f"不支持的 Skill 合同版本：{self.schema_version}")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.skill):
            raise ValueError("skill 名称必须使用小写 hyphen-case")
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if not self.input_fingerprint.startswith("sha256:"):
            raise ValueError("input_fingerprint 必须是 SHA-256 指纹")
        if self.status is SkillStatus.RETRYABLE and self.retry_scope is None:
            raise ValueError("retryable SkillResult 必须包含 retry_scope")
        if self.status in {SkillStatus.BLOCKED, SkillStatus.QUESTIONED} and not self.diagnostics:
            raise ValueError("blocked/questioned SkillResult 必须包含 diagnostic")
        if self.status is SkillStatus.PASSED and self.retry_scope is not None:
            raise ValueError("passed SkillResult 不得包含 retry_scope")
        if any(artifact.run_id != self.run_id for artifact in self.artifacts):
            raise ValueError("SkillResult 不得引用其他运行批次的产物")

