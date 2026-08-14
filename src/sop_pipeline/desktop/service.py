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
    ) -> Future:
        return self._executor.submit(
            self.backend.resolve,
            run_id,
            {"step_id": step_id, "instruction": instruction},
        )

    def resume(self, run_id: str) -> Future:
        return self._executor.submit(self.backend.resume, run_id)

    def pause(self) -> bool:
        pause = getattr(self.backend, "pause", None)
        return bool(pause and pause())

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
