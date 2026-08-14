"""Qwen LLM client using DashScope's OpenAI-compatible API.

This module wraps the openai SDK to call Qwen models via DashScope.
DashScope exposes an OpenAI-compatible endpoint, so we reuse the same SDK
with a different base_url and api_key.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI


@dataclass
class QwenConfig:
    """Runtime configuration for the Qwen model endpoint."""

    api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: str = "qwen-max"
    api_key: str = ""
    max_tokens: int = 8192
    temperature: float = 0.1
    tool_choice: str = "auto"

    @classmethod
    def from_json(cls, path: Path) -> "QwenConfig":
        """Load configuration from a qwen-runtime.json file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        api_key_env = data.get("api_key_env", "DASHSCOPE_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"环境变量 {api_key_env} 未设置。请先 export {api_key_env}=<your-dashscope-api-key>"
            )
        return cls(
            api_base_url=data.get("api_base_url", cls.api_base_url),
            model_name=data.get("model_name", cls.model_name),
            api_key=api_key,
            max_tokens=data.get("max_tokens", cls.max_tokens),
            temperature=data.get("temperature", cls.temperature),
            tool_choice=data.get("tool_choice", cls.tool_choice),
        )

    @classmethod
    def from_env(cls) -> "QwenConfig":
        """Build config purely from environment variables."""
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("环境变量 DASHSCOPE_API_KEY 未设置。")
        return cls(
            api_base_url=os.environ.get(
                "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            model_name=os.environ.get("QWEN_MODEL", "qwen-max"),
            api_key=api_key,
        )


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class QwenResponse:
    """Parsed response from the Qwen model."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class QwenClient:
    """Thin wrapper around the OpenAI SDK targeting DashScope / Qwen.

    Usage::

        config = QwenConfig.from_json(Path("config/qwen-runtime.json"))
        client = QwenClient(config)
        response = client.chat(
            system_prompt="You are a Creo SOP pipeline orchestrator.",
            user_message="Run preflight for the water-tank product.",
            tools=[...],
        )
        if response.has_tool_calls:
            for tc in response.tool_calls:
                result = dispatch(tc.name, tc.arguments)
                client.append_tool_result(tc.id, tc.name, result)
    """

    def __init__(self, config: QwenConfig) -> None:
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.api_base_url,
        )
        self._messages: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._config.model_name

    def reset_conversation(self) -> None:
        """Clear the conversation history."""
        self._messages.clear()

    def chat(
        self,
        *,
        system_prompt: str,
        user_message: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> QwenResponse:
        """Send a chat completion request to Qwen.

        On the first call the system prompt is prepended automatically.
        Subsequent calls reuse the accumulated message history so that the
        model can see prior tool calls and results within the same session.
        """
        if not self._messages:
            self._messages.append({"role": "system", "content": system_prompt})
        if user_message is not None:
            self._messages.append({"role": "user", "content": user_message})

        request_kwargs: dict[str, Any] = {
            "model": self._config.model_name,
            "messages": self._messages,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = self._config.tool_choice

        response = self._client.chat.completions.create(**request_kwargs)
        choice = response.choices[0]
        message = choice.message

        # Persist assistant reply for multi-turn context.
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        self._messages.append(assistant_msg)

        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments),
            )
            for tc in (message.tool_calls or [])
        ]

        return QwenResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            raw=response.model_dump(),
        )

    def append_tool_result(self, tool_call_id: str, name: str, result: Any) -> None:
        """Feed a tool execution result back into the conversation."""
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            }
        )

    def run_tool_loop(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        dispatcher: dict[str, Any],
        max_rounds: int = 20,
    ) -> str:
        """Convenience: chat → tool calls → dispatch → feed back → repeat.

        *dispatcher* maps tool-name → callable(**kwargs) → Any.
        Returns the final assistant text after all tool rounds complete.
        """
        self.reset_conversation()
        for _ in range(max_rounds):
            resp = self.chat(
                system_prompt=system_prompt,
                user_message=user_message,
                tools=tools,
            )
            user_message = None  # only inject user text on the first turn
            if not resp.has_tool_calls:
                return resp.content or ""
            for tc in resp.tool_calls:
                handler = dispatcher.get(tc.name)
                if handler is None:
                    result = {"error": f"未知工具: {tc.name}"}
                else:
                    try:
                        result = handler(**tc.arguments)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                self.append_tool_result(tc.id, tc.name, result)
        return resp.content or ""
