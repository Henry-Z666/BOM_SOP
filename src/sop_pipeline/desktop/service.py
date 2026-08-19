from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol


class AgentBackend(Protocol):
    def start_analysis(self, bom_file: Path, cad_directory: Path) -> dict[str, Any]: ...
    def confirm(self, run_id: str, answers: dict[str, str]) -> dict[str, Any]: ...
    def generate(self, run_id: str) -> dict[str, Any]: ...
    def resolve(self, run_id: str, resolution: dict[str, Any]) -> dict[str, Any]: ...
    def resume(self, run_id: str) -> dict[str, Any]: ...
    def pause(self) -> bool: ...
    def progress_snapshot(self, run_id: str | None = None) -> dict[str, Any]: ...
    def review_packet(self, run_id: str) -> dict[str, Any]: ...


class DesktopAgentService:
    """Non-Qt UI seam. Every blocking backend call runs outside the UI thread."""

    def __init__(self, backend: AgentBackend) -> None:
        self.backend = backend
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-ui")

    def start_analysis(self, bom_file: Path, cad_directory: Path) -> Future:
        return self._executor.submit(
            self.backend.start_analysis, Path(bom_file), Path(cad_directory)
        )

    @staticmethod
    def recommended_answers(packet: dict[str, Any]) -> dict[str, str]:
        answers: dict[str, str] = {}
        for item in packet.get("items", []):
            if item.get("category") == "CONFIRMATION":
                answers[str(item["item_id"])] = str(item["recommended_option"])
        return answers

    def confirm_and_generate(
        self,
        run_id: str,
        answers: dict[str, str],
    ) -> Future:
        def task() -> dict[str, Any]:
            self.backend.confirm(run_id, answers)
            return self.backend.generate(run_id)

        return self._executor.submit(task)

    def resolve_candidate(
        self,
        run_id: str,
        step_id: str,
        candidate_id: str,
    ) -> Future:
        return self._executor.submit(
            self.backend.resolve,
            run_id,
            {"step_id": step_id, "candidate_id": candidate_id},
        )

    def resolve_instruction(
        self,
        run_id: str,
        step_id: str,
        instruction: str,
        *,
        structured_inputs: dict[str, Any] | None = None,
    ) -> Future:
        return self._executor.submit(
            self.backend.resolve,
            run_id,
            {
                "step_id": step_id,
                "instruction": instruction,
                "metadata": {"structured_inputs": dict(structured_inputs or {})},
            },
        )

    def accept_with_override(
        self,
        run_id: str,
        step_id: str,
        *,
        reason: str = "",
    ) -> Future:
        return self._executor.submit(
            self.backend.resolve,
            run_id,
            {
                "step_id": step_id,
                "action": "accept_with_override",
                "metadata": {
                    "acknowledged": True,
                    "reason": str(reason).strip(),
                },
            },
        )

    def resume(self, run_id: str) -> Future:
        return self._executor.submit(self.backend.resume, run_id)

    def pause(self) -> bool:
        pause = getattr(self.backend, "pause", None)
        return bool(pause and pause())

    def progress_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        snapshot = getattr(self.backend, "progress_snapshot", None)
        if snapshot is None:
            return {"available": False, "percent": 0, "stage": "正在处理"}
        return dict(snapshot(run_id))

    def review_packet(self, run_id: str) -> dict[str, Any]:
        packet = getattr(self.backend, "review_packet", None)
        if packet is None:
            return {"run_id": run_id, "items": [], "message": "无法读取待处理步骤。"}
        return dict(packet(run_id))

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
