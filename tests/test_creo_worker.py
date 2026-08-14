from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from sop_pipeline.agent.creo_worker import PowerShellCreoWorker
from sop_pipeline.agent.render_scheduler import RenderPlan, RenderTask


class RecordingRunner:
    def __init__(self, prepared_models: Path) -> None:
        self.prepared_models = prepared_models
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        output_folder = Path(command[command.index("-OutputFolder") + 1])
        output_folder.mkdir(parents=True, exist_ok=True)
        index = int(command[command.index("-StartIndex") + 1])
        (output_folder / f"job-{index}.jpg").write_bytes(f"image-{index}".encode())
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"[BATCH] prepared_isolated_models {self.prepared_models}\n",
            stderr="",
        )


def _task(index: int) -> RenderTask:
    return RenderTask(
        task_id=f"job-{index}",
        step_id=f"job-{index}",
        main_process_id="30",
        depends_on=(),
        complete_state_hash=f"state-{index}",
        payload={"contract_index": index},
    )


class PowerShellCreoWorkerTests(unittest.TestCase):
    def test_one_model_copy_is_reused_for_all_tasks_in_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            prepared = workspace / "prepared-models"
            runner = RecordingRunner(prepared)
            worker = PowerShellCreoWorker(
                powershell="pwsh",
                stage_script=Path("creo_java/run_stage_batch.ps1"),
                product_config=Path("products/water-tank/product.json"),
                jobs_json=Path("data/runs/corrected-v2-render-jobs.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v1", (_task(0), _task(1)))
            session = worker.open_session(workspace, plan)

            first = worker.render(session, plan.tasks[0], 1)
            second = worker.render(session, plan.tasks[1], 1)
            worker.close_session(session)

        self.assertEqual(first.disposition, "passed")
        self.assertEqual(second.disposition, "passed")
        self.assertNotIn("-PreparedModelsRoot", runner.commands[0])
        self.assertIn("-PreparedModelsRoot", runner.commands[1])
        prepared_arg = runner.commands[1].index("-PreparedModelsRoot") + 1
        self.assertEqual(Path(runner.commands[1][prepared_arg]), prepared)

    def test_task_without_contract_index_is_rejected_before_creo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runner = RecordingRunner(workspace / "prepared-models")
            worker = PowerShellCreoWorker(
                powershell="pwsh",
                stage_script=Path("stage.ps1"),
                product_config=Path("product.json"),
                jobs_json=Path("jobs.json"),
                runner=runner,
            )
            task = RenderTask("job", "step", "30", (), "state")
            session = worker.open_session(
                workspace, RenderPlan("render-plan/v1", (task,))
            )
            attempt = worker.render(session, task, 1)

        self.assertEqual(attempt.disposition, "failed")
        self.assertEqual(attempt.error_code, "INVALID_RENDER_TASK")
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
