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


_CORRECTION_CONTRACT: dict[str, Any] = {
    "presentation": {
        "camera_id": {"type": "enum", "values": ["fixed_123", "fixed_456"]},
        "zoom": {"type": "number", "range": [0.5, 2.0]},
        "pan": {"type": "xy_vector", "length": 2, "item_range": [-1.0, 1.0]},
        "explosion_distance": {"type": "number", "range": [0.0, 1000.0]},
        "arrow_layout": {"type": "string"},
    },
    "installation_geometry": {
        "direction": {
            "type": "xyz_vector",
            "length": 3,
            "description": "root-coordinate installation direction; non-zero numbers",
        },
        "moving_occurrences": {"type": "string_array"},
        "receiver_occurrences": {"type": "string_array"},
    },
    "complete_state": {
        "direction": {
            "type": "xyz_vector",
            "length": 3,
            "description": "root-coordinate installation direction; non-zero numbers",
        },
        "moving_occurrences": {"type": "string_array"},
        "receiver_occurrences": {"type": "string_array"},
        "depends_on": {"type": "step_id_array"},
        "order": {"type": "integer"},
    },
}

_REQUIRED_FIELDS_BY_ERROR = {
    "DIRECTION_SIGN_WEAK": ["direction"],
    "RECEIVER_NORMAL_NOT_AXIS_ALIGNED": ["direction"],
    "MOVING_OCCURRENCE_UNRESOLVED": ["moving_occurrences"],
    "RECEIVER_OCCURRENCE_UNRESOLVED": ["receiver_occurrences"],
    "NO_NATIVE_RECEIVER_GEOMETRY": ["receiver_occurrences", "direction"],
}


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
        current_context: dict[str, Any] | None = None,
    ) -> StepRevision:
        minimized_context = _minimized_resolution_context(current_context or {})
        required_fields = ", ".join(
            str(field)
            for field in minimized_context.get("required_correction_fields", [])
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Return one JSON object only with exactly two top-level fields: "
                    'kind and changes. kind is one of "presentation", '
                    '"installation_geometry", "complete_state". Use only fields and value '
                    "types listed in correction_contract. changes must contain the concrete "
                    "bounded correction, not only a category. For a placeholder or a geometry "
                    "gate, include every required_correction_fields item; a camera/view change "
                    "alone cannot repair missing geometry. The selected step is already bound: "
                    "step numbers, component names, drawing numbers, material codes, and BOM rows "
                    "in the instruction are human-facing references. Resolve them only against "
                    "current_step; the user is never required to provide an internal occurrence "
                    "ID. Copy known occurrence IDs from current_step when the contract requires "
                    "them, and never guess occurrence IDs. Example: "
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
                        "correction_contract": _CORRECTION_CONTRACT,
                        "current_step": minimized_context,
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
                    changes=_canonicalize_revision_changes(payload["changes"]),
                )
                validated = validate_revision(result)
                _validate_resolution_context(validated, minimized_context)
                return validated
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
                                f"{type(error).__name__}: {error}. Return the exact required "
                                "JSON with a concrete non-empty changes object. Use only the "
                                "correction_contract types and include current_step."
                                "required_correction_fields when present."
                                + (
                                    f" Required fields: {required_fields}."
                                    if required_fields
                                    else ""
                                )
                            ),
                        },
                    ]
                )
        fallback = _bounded_resolution_fallback(step_id, instruction, revision)
        if fallback is not None:
            try:
                _validate_resolution_context(fallback, minimized_context)
            except ValueError as error:
                last_error = error
            else:
                return fallback
        raise ValueError(
            _resolution_failure_message(current_context or {}, last_error)
        ) from last_error

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


def _canonicalize_revision_changes(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize a small, unambiguous set of common model aliases."""

    changes = dict(value)
    component_keys = {"pan_x", "pan_y"} & set(changes)
    if component_keys:
        if component_keys != {"pan_x", "pan_y"} or "pan" in changes:
            raise ValueError("pan_x and pan_y must be supplied together without pan")
        changes["pan"] = [changes.pop("pan_x"), changes.pop("pan_y")]
    pan = changes.get("pan")
    if isinstance(pan, dict):
        if set(pan) != {"x", "y"}:
            raise ValueError("pan object requires exactly x and y")
        changes["pan"] = [pan["x"], pan["y"]]
    return changes


def _bounded_resolution_fallback(
    step_id: str,
    instruction: str,
    revision: int,
) -> StepRevision | None:
    """Handle a very small set of unambiguous user corrections.

    Qwen remains the primary interpreter.  This fallback exists so a clear,
    bounded presentation request does not become a system-blocking error only
    because the model wrapped or malformed its JSON.  It cannot change CAD
    facts, dependencies, paths, or installation geometry.
    """

    normalized = re.sub(r"\s+", "", str(instruction)).casefold()
    installation_focus = any(
        marker in normalized
        for marker in ("安装部位", "安装位置", "装配部位", "装配位置")
    )
    if installation_focus and "放大" in normalized:
        return validate_revision(
            StepRevision(
                revision,
                step_id,
                RevisionKind.PRESENTATION,
                {"zoom": 1.25, "pan": [0.0, 0.0]},
            )
        )
    if installation_focus and "缩小" in normalized:
        return validate_revision(
            StepRevision(
                revision,
                step_id,
                RevisionKind.PRESENTATION,
                {"zoom": 0.85, "pan": [0.0, 0.0]},
            )
        )
    if any(marker in normalized for marker in ("装入", "安装方向", "装配方向", "插入")):
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
        matched = [
            vector for marker, vector in axis_vectors.items() if marker in normalized
        ]
        if len(matched) == 1:
            return validate_revision(
                StepRevision(
                    revision,
                    step_id,
                    RevisionKind.INSTALLATION_GEOMETRY,
                    {"direction": matched[0]},
                )
            )
    return None


def _minimized_resolution_context(value: dict[str, Any]) -> dict[str, Any]:
    """Expose only bounded state needed to understand why a step is pending."""

    allowed = {
        "status",
        "error_code",
        "error_message",
        "issues",
        "image_kind",
        "execution_mode",
        "planning_diagnostics",
        "step_number",
        "step_title",
        "source_bom_rows",
        "moving_occurrences",
        "receiver_occurrences",
        "direction",
        "current_camera_id",
        "allowed_camera_ids",
    }
    minimized = {key: value[key] for key in sorted(allowed) if key in value}
    source_items = value.get("source_bom_items")
    if isinstance(source_items, list):
        public_item_fields = {
            "bom_row",
            "name",
            "drawing_no",
            "material_code",
        }
        minimized["source_bom_items"] = [
            {
                key: item[key]
                for key in sorted(public_item_fields)
                if key in item
            }
            for item in source_items
            if isinstance(item, dict)
        ]
    error_code = str(value.get("error_code", ""))
    required = _REQUIRED_FIELDS_BY_ERROR.get(error_code)
    if required:
        minimized["required_correction_fields"] = required
    return minimized


def _resolution_failure_message(
    current_context: dict[str, Any],
    last_error: Exception | None,
) -> str:
    error_code = str(current_context.get("error_code", ""))
    if error_code in {"DIRECTION_SIGN_WEAK", "RECEIVER_NORMAL_NOT_AXIS_ALIGNED"}:
        return (
            "当前步骤缺少明确的安装方向，现有说明不足以生成真实图片。"
            "请给出方向，例如：该零件沿 Z 轴正方向装入（也可说明正/负 X、Y、Z）。"
        )
    if error_code in {
        "MOVING_OCCURRENCE_UNRESOLVED",
        "RECEIVER_OCCURRENCE_UNRESOLVED",
        "NO_NATIVE_RECEIVER_GEOMETRY",
    }:
        return (
            "当前说明不足以确定安装对象或接收部件。"
            "请明确说明“把哪个零件安装到哪个部件/接口”，必要时同时给出安装方向。"
        )
    detail = f"：{last_error}" if last_error is not None else ""
    return "Qwen 返回的修正信息不符合步骤修订合同" + detail


def _validate_resolution_context(
    revision: StepRevision,
    current_context: dict[str, Any],
) -> None:
    required = {
        str(field)
        for field in current_context.get("required_correction_fields", [])
    }
    missing = required - set(revision.changes)
    if missing:
        raise ValueError(
            "changes missing required correction fields: "
            + ", ".join(sorted(missing))
        )
    occurrence_fields = {
        "moving_occurrences",
        "receiver_occurrences",
    } & set(revision.changes)
    if occurrence_fields:
        raise ValueError(
            "occurrence IDs cannot be inferred from unrestricted user text"
        )
    if (
        current_context.get("image_kind") == "placeholder"
        and revision.kind is RevisionKind.PRESENTATION
    ):
        raise ValueError(
            "a presentation change cannot repair a geometry placeholder"
        )


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
