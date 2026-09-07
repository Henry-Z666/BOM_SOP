from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol


@dataclass(frozen=True)
class ImageReviewDecision:
    """The bounded decision returned by an image-review model."""

    passed: bool
    issues: tuple[str, ...]
    model: str


class VisionTransport(Protocol):
    model: str

    def call(self, image_file: Path, prompt: str, *, seed: int) -> str: ...


class DashScopeVisionTransport:
    """Official DashScope SDK boundary, imported lazily for offline runs."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen3-vl-plus",
        enable_thinking: bool = False,
    ) -> None:
        if not str(api_key).strip():
            raise ValueError("DashScope API key is required")
        self.api_key = str(api_key).strip()
        self.model = str(model).strip() or "qwen3-vl-plus"
        self.enable_thinking = bool(enable_thinking)

    @classmethod
    def from_env(cls) -> "DashScopeVisionTransport":
        return cls(
            os.environ.get("DASHSCOPE_API_KEY", ""),
            model=os.environ.get(
                "CREO_SOP_AI_REVIEW_MODEL", "qwen3-vl-plus"
            ),
            enable_thinking=os.environ.get(
                "CREO_SOP_AI_REVIEW_THINKING", "0"
            ).strip() == "1",
        )

    def call(self, image_file: Path, prompt: str, *, seed: int) -> str:
        try:
            from dashscope import MultiModalConversation
        except ImportError as error:
            raise RuntimeError("请安装官方 dashscope Python SDK") from error
        response = MultiModalConversation.call(
            api_key=self.api_key,
            model=self.model,
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
            enable_thinking=self.enable_thinking,
        )
        return _extract_dashscope_text(response)


class AIImageReviewer:
    """Reviews presentation only; it has no authority over CAD hard gates."""

    def __init__(
        self,
        transport: VisionTransport,
        *,
        seed: int = 7,
        max_schema_attempts: int = 2,
    ) -> None:
        if not 1 <= max_schema_attempts <= 3:
            raise ValueError("max_schema_attempts must be between 1 and 3")
        self.transport = transport
        self.seed = seed
        self.max_schema_attempts = max_schema_attempts

    def review_render(
        self,
        image_file: Path,
        minimized_context: dict[str, Any],
    ) -> ImageReviewDecision:
        prompt = (
            "装配结构硬门已经验证了零件身份、数量、可见集、固定相机、纯平移、"
            "箭头CAD端点和文件完整性。你只审核这张装配SOP图片是否足够清楚且没有"
            "明显错误：移动件、绿色安装箭头和接收位置的关系应可辨认；不得有明显遮挡、"
            "裁切、箭头反向或互相矛盾。不要因为图片没有打印occurrence编号、接收面轮廓"
            "或CAD标签而判错。只返回严格JSON："
            '{"passed":true或false,"issues":["简短原因"]}。'
            "当图片可直接用于SOP时 passed=true；不确定或发现明显问题时 passed=false。"
            "上下文："
            + json.dumps(minimized_context, ensure_ascii=False, sort_keys=True)
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_schema_attempts + 1):
            response = self.transport.call(image_file, prompt, seed=self.seed)
            try:
                payload = _parse_json_object(response)
                if set(payload) != {"passed", "issues"}:
                    raise ValueError("AI审核结果必须只包含 passed 和 issues")
                if not isinstance(payload["passed"], bool):
                    raise ValueError("AI审核 passed 必须为布尔值")
                issues = payload["issues"]
                if not isinstance(issues, list) or any(
                    not isinstance(issue, str) for issue in issues
                ):
                    raise ValueError("AI审核 issues 必须为字符串数组")
                normalized_issues = tuple(
                    issue.strip() for issue in issues if issue.strip()
                )
                if payload["passed"] and normalized_issues:
                    raise ValueError("AI审核通过时 issues 必须为空")
                if not payload["passed"] and not normalized_issues:
                    raise ValueError("AI审核不通过时必须说明原因")
                return ImageReviewDecision(
                    passed=payload["passed"],
                    issues=normalized_issues,
                    model=str(getattr(self.transport, "model", "unknown")),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < self.max_schema_attempts:
                    prompt += " 上次响应格式无效；请只返回规定的JSON对象。"
        raise ValueError("AI图片审核未返回有效结果") from last_error


def reviewer_from_env() -> AIImageReviewer | None:
    if os.environ.get("CREO_SOP_AI_REVIEW", "1").strip() == "0":
        return None
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return None
    return AIImageReviewer(DashScopeVisionTransport.from_env())


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("AI审核结果必须为JSON对象")
    return value


def _extract_dashscope_text(response: Any) -> str:
    status_code = getattr(response, "status_code", HTTPStatus.OK)
    if status_code != HTTPStatus.OK:
        code = getattr(response, "code", "DASHSCOPE_ERROR")
        message = getattr(response, "message", "request failed")
        raise RuntimeError(f"{code}: {message}")
    try:
        content = response.output.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise RuntimeError("DashScope返回内容不完整") from error
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict)
        )
    raise RuntimeError("DashScope返回了不支持的消息格式")
