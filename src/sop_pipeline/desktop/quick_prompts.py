from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class QuickPrompt:
    prompt_id: str
    label: str
    text: str


class QuickPromptProvider(Protocol):
    """Provide bounded review prompts without changing CAD facts."""

    def prompts(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[QuickPrompt, ...]: ...


class StaticQuickPromptProvider:
    def __init__(self, prompts: tuple[QuickPrompt, ...]) -> None:
        self._prompts = prompts

    def prompts(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[QuickPrompt, ...]:
        del context
        return self._prompts


class ContextQuickPromptProvider:
    """Hide presentation shortcuts when a structured fact is required."""

    def prompts(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[QuickPrompt, ...]:
        code = str((context or {}).get("error_code") or "").upper()
        if code in {
            "DIRECTION_SIGN_WEAK",
            "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
            "MOVING_OCCURRENCE_UNRESOLVED",
            "RECEIVER_OCCURRENCE_UNRESOLVED",
            "NO_NATIVE_RECEIVER_GEOMETRY",
        }:
            return ()
        return (
            QuickPrompt("flip-view", "翻转视角", "翻转到另一台固定视角"),
        )


DEFAULT_QUICK_PROMPT_PROVIDER = ContextQuickPromptProvider()
