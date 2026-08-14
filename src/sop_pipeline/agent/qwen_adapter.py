from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from .step_revision import RevisionKind, StepRevision, validate_revision


class QwenTransport(Protocol):
    def call_text(self, messages: list[dict[str, Any]], *, seed: int) -> str: ...

    def call_vision(self, image_file: Path, prompt: str, *, seed: int) -> str: ...


class DashScopeTransport:
    """Official DashScope SDK boundary; imported lazily for offline operation."""

    def __init__(
        self,
        api_key: str,
        *,
        text_model: str = "qwen-plus",
        vision_model: str = "qwen-vl-max",
    ) -> None:
        if not api_key:
            raise ValueError("DashScope API key is required")
        self.api_key = api_key
        self.text_model = text_model
        self.vision_model = vision_model

    @classmethod
    def from_env(cls) -> DashScopeTransport:
        return cls(os.environ.get("DASHSCOPE_API_KEY", ""))

    def call_text(self, messages: list[dict[str, Any]], *, seed: int) -> str:
        try:
            from dashscope import Generation
        except ImportError as error:
            raise RuntimeError("install the official dashscope Python SDK") from error
        response = Generation.call(
            api_key=self.api_key,
            model=self.text_model,
            messages=messages,
            result_format="message",
            temperature=0,
            seed=seed,
        )
        return _extract_dashscope_text(response)

    def call_vision(self, image_file: Path, prompt: str, *, seed: int) -> str:
        try:
            from dashscope import MultiModalConversation
        except ImportError as error:
            raise RuntimeError("install the official dashscope Python SDK") from error
        response = MultiModalConversation.call(
            api_key=self.api_key,
            model=self.vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"image": image_file.resolve().as_uri()},
                        {"text": prompt},
                    ],
                }
            ],
            temperature=0,
            seed=seed,
        )
        return _extract_dashscope_text(response)


@dataclass(frozen=True)
class SemanticReview:
    passed: bool
    issues: tuple[str, ...]


class QwenAdvisor:
    """Small Qwen surface: wording, semantic review, and constrained revisions."""

    def __init__(self, transport: QwenTransport, *, seed: int = 7) -> None:
        self.transport = transport
        self.seed = seed

    def interpret_resolution(
        self,
        step_id: str,
        instruction: str,
        revision: int,
    ) -> StepRevision:
        response = self.transport.call_text(
            [
                {
                    "role": "system",
                    "content": (
                        "Return JSON only. Convert feedback into one of "
                        "presentation, installation_geometry, complete_state. "
                        "Use only bounded fields allowed by the supplied schema."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "schema_version": "step-resolution-request/v1",
                            "step_id": step_id,
                            "instruction": instruction,
                            "allowed_cameras": ["fixed_123", "fixed_456"],
                            "zoom_range": [0.5, 2.0],
                            "pan_range": [-1.0, 1.0],
                            "explosion_distance_range": [0.0, 1000.0],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            seed=self.seed,
        )
        payload = _parse_json_object(response)
        result = StepRevision(
            revision=revision,
            step_id=step_id,
            kind=RevisionKind(payload["kind"]),
            changes=dict(payload.get("changes", {})),
        )
        return validate_revision(result)

    def review_render(
        self,
        image_file: Path,
        minimized_context: dict[str, Any],
    ) -> SemanticReview:
        prompt = (
            "Evaluate whether the moving item, receiving location, and installation "
            "boundary are readable. Do not infer hidden geometry. Return JSON as "
            '{"passed": boolean, "issues": [short strings]}. Context: '
            + json.dumps(minimized_context, ensure_ascii=False, sort_keys=True)
        )
        payload = _parse_json_object(
            self.transport.call_vision(image_file, prompt, seed=self.seed)
        )
        return SemanticReview(
            passed=bool(payload.get("passed")),
            issues=tuple(str(issue) for issue in payload.get("issues", [])),
        )


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Qwen response must be a JSON object")
    return value


def _extract_dashscope_text(response: Any) -> str:
    status_code = getattr(response, "status_code", HTTPStatus.OK)
    if status_code != HTTPStatus.OK:
        code = getattr(response, "code", "DASHSCOPE_ERROR")
        message = getattr(response, "message", "request failed")
        raise RuntimeError(f"{code}: {message}")
    message = response.output.choices[0].message
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "".join(texts)
    raise RuntimeError("DashScope returned unsupported message content")
