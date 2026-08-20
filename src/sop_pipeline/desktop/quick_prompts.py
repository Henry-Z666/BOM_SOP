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
    """The pure pipeline exposes no free-form presentation shortcuts."""

    def prompts(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[QuickPrompt, ...]:
        del context
        return ()


DEFAULT_QUICK_PROMPT_PROVIDER = ContextQuickPromptProvider()
