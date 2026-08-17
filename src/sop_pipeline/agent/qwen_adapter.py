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


@dataclass(frozen=True)
class PlanChoiceRecommendation:
    decision_id: str
    recommended: str
    reason: str


class QwenAdvisor:
    """Small Qwen surface: wording, semantic review, and constrained revisions."""

    def __init__(
        self,
        transport: QwenTransport,
        *,
        seed: int = 7,
        max_schema_attempts: int = 3,
    ) -> None:
        if not 1 <= max_schema_attempts <= 3:
            raise ValueError("max_schema_attempts must be between 1 and 3")
        self.transport = transport
        self.seed = seed
        self.max_schema_attempts = max_schema_attempts

    def interpret_resolution(
        self,
        step_id: str,
        instruction: str,
        revision: int,
    ) -> StepRevision:
        messages = [
            {
                "role": "system",
                "content": (
                    "Return one JSON object only with exactly two top-level fields: "
                    'kind and changes. kind is one of "presentation", '
                    '"installation_geometry", "complete_state". changes must contain '
                    "the concrete bounded correction, not only a category. Example: "
                    '{"kind":"presentation","changes":{"camera_id":"fixed_456",'
                    '"zoom":1.1}}. Never return type, explanation, paths, or markdown.'
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
        ]
        last_error: Exception | None = None
        for attempt in range(1, self.max_schema_attempts + 1):
            response = self.transport.call_text(messages, seed=self.seed)
            try:
                payload = _parse_json_object(response)
                if set(payload) != {"kind", "changes"}:
                    raise ValueError("response requires exactly kind and changes")
                if not isinstance(payload["changes"], dict) or not payload["changes"]:
                    raise ValueError("changes must be a non-empty object")
                result = StepRevision(
                    revision=revision,
                    step_id=step_id,
                    kind=RevisionKind(payload["kind"]),
                    changes=dict(payload["changes"]),
                )
                return validate_revision(result)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                if attempt == self.max_schema_attempts:
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": response},
                        {
                            "role": "user",
                            "content": (
                                "SCHEMA_VALIDATION_FAILED: "
                                f"{type(error).__name__}. Return the exact required JSON "
                                "with a concrete non-empty changes object."
                            ),
                        },
                    ]
                )
        raise ValueError("Qwen did not return a valid StepRevision") from last_error

    def recommend_plan_choices(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[PlanChoiceRecommendation, ...]:
        """Recommend semantic scope only; never return or alter CAD geometry."""

        minimized = [
            {
                "decision_id": str(item["decision_id"]),
                "assembly_name": str(item.get("assembly_name", "")),
                "assembly_text": str(item.get("assembly_text", "")),
                "process_text": str(item.get("process_text", "")),
                "child_items": [
                    {
                        "name": str(child.get("name", "")),
                        "drawing_no": str(child.get("drawing_no", "")),
                        "quantity": child.get("quantity"),
                    }
                    for child in item.get("child_items", [])
                ],
            }
            for item in items
        ]
        expected_ids = {item["decision_id"] for item in minimized}
        messages = [
            {
                "role": "system",
                "content": (
                    "Decide only whether each listed subassembly should be built from its "
                    "BOM children at this workstation (expand) or treated as an already "
                    "completed rigid unit (whole). Return JSON only as "
                    '{"decisions":[{"decision_id":"...","recommended":"expand|whole",'
                    '"reason":"简短的简体中文依据"}]}. Return every decision_id '
                    "exactly once. Do not return CAD paths, geometry, directions, cameras, "
                    "output paths, markdown, or extra fields. Every reason must be concise "
                    "Simplified Chinese."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema_version": "subassembly-scope-request/v1",
                        "items": minimized,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        last_error: Exception | None = None
        for attempt in range(1, self.max_schema_attempts + 1):
            response = self.transport.call_text(messages, seed=self.seed)
            try:
                payload = _parse_json_object(response)
                if set(payload) != {"decisions"} or not isinstance(
                    payload["decisions"], list
                ):
                    raise ValueError("response requires exactly a decisions array")
                decisions: list[PlanChoiceRecommendation] = []
                for item in payload["decisions"]:
                    if not isinstance(item, dict) or set(item) != {
                        "decision_id",
                        "recommended",
                        "reason",
                    }:
                        raise ValueError("each decision has invalid fields")
                    decision_id = str(item["decision_id"])
                    recommended = str(item["recommended"])
                    reason = str(item["reason"]).strip()
                    if (
                        recommended not in {"expand", "whole"}
                        or not reason
                        or len(reason) > 120
                    ):
                        raise ValueError("decision recommendation is invalid")
                    decisions.append(
                        PlanChoiceRecommendation(decision_id, recommended, reason)
                    )
                actual_ids = [item.decision_id for item in decisions]
                if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
                    raise ValueError("decision IDs must match the request exactly")
                return tuple(sorted(decisions, key=lambda item: item.decision_id))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                if attempt == self.max_schema_attempts:
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": response},
                        {
                            "role": "user",
                            "content": (
                                "SCHEMA_VALIDATION_FAILED. Return every requested decision_id "
                                "once with only decision_id, recommended, and reason."
                            ),
                        },
                    ]
                )
        raise ValueError("Qwen did not return valid subassembly recommendations") from last_error

    def review_render(
        self,
        image_file: Path,
        minimized_context: dict[str, Any],
    ) -> SemanticReview:
        prompt = (
            "The deterministic geometry gate has already verified the model occurrences, "
            "visibility set, camera, pure translation, and native-arrow endpoint math. "
            "Review only whether the shown installation action is visually readable for "
            "an SOP reader. Do not fail merely because occurrence IDs, receiver outlines, "
            "or installation-boundary labels are not printed on the image. Question the "
            "image only when the moving part/arrow/receiver relationship is visibly "
            "occluded, contradictory, or genuinely ambiguous. Return JSON as "
            '{"passed": boolean, "issues": [short strings]}. Context: '
            + json.dumps(minimized_context, ensure_ascii=False, sort_keys=True)
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_schema_attempts + 1):
            response = self.transport.call_vision(image_file, prompt, seed=self.seed)
            try:
                payload = _parse_json_object(response)
                if set(payload) != {"passed", "issues"}:
                    raise ValueError("review requires exactly passed and issues")
                if not isinstance(payload["passed"], bool) or not isinstance(
                    payload["issues"], list
                ):
                    raise ValueError("review fields have invalid types")
                return SemanticReview(
                    passed=payload["passed"],
                    issues=tuple(str(issue) for issue in payload["issues"]),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                if attempt == self.max_schema_attempts:
                    break
                prompt += (
                    " Previous response failed schema validation. Return exactly passed "
                    "as a boolean and issues as an array of short strings."
                )
        raise ValueError("Qwen-VL did not return a valid semantic review") from last_error


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
