from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import json

from .models import ArtifactRef, RunRecord, RunStatus, StepResult, StepStatus
from .models import SkillStatus
from .skill_contract import Diagnostic, RetryScope, SkillResult


class RunNotFoundError(KeyError):
    pass


class RunStore:
    """Persist Agent state behind a small run-oriented interface."""

    def __init__(self, database: Path) -> None:
        self._database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        bom_file TEXT NOT NULL,
                        cad_directory TEXT NOT NULL,
                        workspace TEXT NOT NULL,
                        status TEXT NOT NULL,
                        input_fingerprint TEXT NOT NULL,
                        plan_revision INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS steps (
                        run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        main_process_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        depends_on TEXT NOT NULL,
                        complete_state_hash TEXT NOT NULL,
                        output_hash TEXT,
                        PRIMARY KEY (run_id, step_id),
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (run_id, relative_path),
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skill_executions (
                        run_id TEXT NOT NULL,
                        skill TEXT NOT NULL,
                        input_fingerprint TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (run_id, skill, input_fingerprint),
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    )
                    """
                )

    def add(self, run: RunRecord) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, bom_file, cad_directory, workspace, status,
                        input_fingerprint, plan_revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        str(run.bom_file),
                        str(run.cad_directory),
                        str(run.workspace),
                        run.status.value,
                        run.input_fingerprint,
                        run.plan_revision,
                        run.created_at,
                        run.updated_at,
                    ),
                )

    def get(self, run_id: str) -> RunRecord:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return RunRecord(
            run_id=row["run_id"],
            bom_file=Path(row["bom_file"]),
            cad_directory=Path(row["cad_directory"]),
            workspace=Path(row["workspace"]),
            status=RunStatus(row["status"]),
            input_fingerprint=row["input_fingerprint"],
            plan_revision=int(row["plan_revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def transition(
        self,
        run_id: str,
        *,
        expected: set[RunStatus],
        status: RunStatus,
        updated_at: str,
        plan_revision: int | None = None,
    ) -> RunRecord:
        current = self.get(run_id)
        if current.status not in expected:
            allowed = ", ".join(sorted(item.value for item in expected))
            raise ValueError(
                f"运行 {run_id} 当前状态为 {current.status.value}，要求状态为 {allowed}"
            )
        with closing(self._connect()) as connection:
            with connection:
                if plan_revision is None:
                    connection.execute(
                        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                        (status.value, updated_at, run_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE runs
                        SET status = ?, plan_revision = ?, updated_at = ?
                        WHERE run_id = ?
                        """,
                        (status.value, plan_revision, updated_at, run_id),
                    )
        return self.get(run_id)

    def add_artifact(self, artifact: ArtifactRef) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO artifacts (
                        artifact_id, run_id, kind, relative_path, sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.run_id,
                        artifact.kind,
                        artifact.relative_path,
                        artifact.sha256,
                        artifact.created_at,
                    ),
                )

    def resolve_artifact(self, run_id: str, reference: str) -> ArtifactRef:
        """Resolve an artifact id or run-relative path without crossing runs."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE run_id = ? AND (artifact_id = ? OR relative_path = ?)
                ORDER BY CASE WHEN artifact_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (run_id, reference, reference, reference),
            ).fetchone()
        if row is None:
            raise KeyError(f"运行 {run_id} 中找不到产物：{reference}")
        return ArtifactRef(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            relative_path=row["relative_path"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )

    def find_artifact(self, run_id: str, relative_path: str) -> ArtifactRef | None:
        try:
            return self.resolve_artifact(run_id, relative_path)
        except KeyError:
            return None

    def save_skill_result(self, result: SkillResult, created_at: str) -> None:
        payload = {
            "schema_version": result.schema_version,
            "skill": result.skill,
            "run_id": result.run_id,
            "status": result.status.value,
            "input_fingerprint": result.input_fingerprint,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "run_id": item.run_id,
                    "kind": item.kind,
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "created_at": item.created_at,
                }
                for item in result.artifacts
            ],
            "diagnostics": [
                {
                    "code": item.code,
                    "message": item.message,
                    "evidence": list(item.evidence),
                }
                for item in result.diagnostics
            ],
            "retry_scope": (
                {
                    "selector_type": result.retry_scope.selector_type,
                    "selectors": list(result.retry_scope.selectors),
                    "max_attempts": result.retry_scope.max_attempts,
                }
                if result.retry_scope is not None
                else None
            ),
            "allowed_next": list(result.allowed_next),
        }
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO skill_executions (
                        run_id, skill, input_fingerprint, result_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        result.skill,
                        result.input_fingerprint,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )

    def find_skill_result(
        self, run_id: str, skill: str, input_fingerprint: str
    ) -> SkillResult | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT result_json FROM skill_executions
                WHERE run_id = ? AND skill = ? AND input_fingerprint = ?
                """,
                (run_id, skill, input_fingerprint),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["result_json"])
        retry = payload.get("retry_scope")
        return SkillResult(
            schema_version=str(payload["schema_version"]),
            skill=str(payload["skill"]),
            run_id=str(payload["run_id"]),
            status=SkillStatus(payload["status"]),
            input_fingerprint=str(payload["input_fingerprint"]),
            artifacts=tuple(
                ArtifactRef(
                    artifact_id=str(item["artifact_id"]),
                    run_id=str(item["run_id"]),
                    kind=str(item["kind"]),
                    relative_path=str(item["relative_path"]),
                    sha256=str(item["sha256"]),
                    created_at=str(item["created_at"]),
                )
                for item in payload.get("artifacts", [])
            ),
            diagnostics=tuple(
                Diagnostic(
                    code=str(item["code"]),
                    message=str(item["message"]),
                    evidence=tuple(str(value) for value in item.get("evidence", [])),
                )
                for item in payload.get("diagnostics", [])
            ),
            retry_scope=(
                RetryScope(
                    selector_type=str(retry["selector_type"]),
                    selectors=tuple(str(value) for value in retry["selectors"]),
                    max_attempts=int(retry["max_attempts"]),
                )
                if retry is not None
                else None
            ),
            allowed_next=tuple(str(value) for value in payload.get("allowed_next", [])),
        )

    def replace_steps(self, run_id: str, steps: tuple[StepResult, ...]) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
                connection.executemany(
                    """
                    INSERT INTO steps (
                        run_id, step_id, main_process_id, status, depends_on,
                        complete_state_hash, output_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            step.step_id,
                            step.main_process_id,
                            step.status.value,
                            json.dumps(step.depends_on, ensure_ascii=False),
                            step.complete_state_hash,
                            step.output_hash,
                        )
                        for step in steps
                    ],
                )

    def list_steps(self, run_id: str) -> tuple[StepResult, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY step_id", (run_id,)
            ).fetchall()
        return tuple(
            StepResult(
                step_id=row["step_id"],
                main_process_id=row["main_process_id"],
                status=StepStatus(row["status"]),
                depends_on=tuple(json.loads(row["depends_on"])),
                complete_state_hash=row["complete_state_hash"],
                output_hash=row["output_hash"],
            )
            for row in rows
        )
