from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class QuickPrompt:
    prompt_id: str
    label: str
    text: str


class QuickPromptProvider(Protocol):
    """Provide review prompts; future providers may use history or step context."""

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


DEFAULT_QUICK_PROMPT_PROVIDER = StaticQuickPromptProvider(
    (
        QuickPrompt("flip-view", "翻转视角", "翻转视角"),
        QuickPrompt(
            "two-arrows",
            "改为两个箭头",
            "箭头数量不对，应该为两个",
        ),
        QuickPrompt(
            "zoom-installation",
            "放大安装部位",
            "以安装部位为中心放大",
        ),
        QuickPrompt(
            "point-to-interface",
            "箭头指向接口",
            "箭头应准确指向安装接口",
        ),
    )
)
