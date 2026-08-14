from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import json

from .models import ArtifactRef, RunRecord, RunStatus, StepResult, StepStatus


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
