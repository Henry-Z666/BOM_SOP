from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sop_pipeline.agent.bundle_paths import (
    bundled_creo_script,
    materialized_creo_script,
)
from sop_pipeline.agent.progress import write_progress
from sop_pipeline.desktop.run_view import progress_snapshot, review_packet


class ProgressViewTests(unittest.TestCase):
    def test_progress_publish_retries_transient_windows_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            original_replace = Path.replace
            attempts = 0

            def transient_denial(path: Path, target: Path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(5, "Access is denied", str(target))
                return original_replace(path, target)

            with patch.object(Path, "replace", transient_denial):
                write_progress(
                    run_workspace,
                    run_id="run-1",
                    skill="normalize-bom",
                    state="RUNNING",
                )

            snapshot = progress_snapshot(workspace, "run-1")
            temporary_files = list(
                (run_workspace / "internal").glob("progress.json.*.tmp")
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(snapshot["skill"], "normalize-bom")
        self.assertEqual(temporary_files, [])

    def test_progress_snapshot_reports_skill_stage(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            write_progress(
                run_workspace,
                run_id="run-1",
                skill="map-bom-cad",
                state="RUNNING",
            )

            snapshot = progress_snapshot(workspace, "run-1")

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["percent"], 30)
        self.assertEqual(snapshot["stage"], "映射 BOM 与 CAD 零部件")

    def test_render_progress_uses_atomic_checkpoint_counts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            (run_workspace / "plans").mkdir(parents=True)
            (run_workspace / "internal").mkdir(parents=True)
            tasks = [
                {"step_id": f"step-{index}", "payload": {"execution_mode": "formal"}}
                for index in range(10)
            ]
            (run_workspace / "plans" / "locked-render-jobs-0001.json").write_text(
                json.dumps({"tasks": tasks}), encoding="utf-8"
            )
            (run_workspace / "internal" / "render-checkpoint-0001.json").write_text(
                json.dumps({"steps": [{"step_id": f"step-{index}"} for index in range(4)]}),
                encoding="utf-8",
            )
            write_progress(
                run_workspace,
                run_id="run-1",
                skill="render-batch",
                state="RUNNING",
            )

            snapshot = progress_snapshot(workspace, "run-1")

        self.assertEqual(snapshot["completed_tasks"], 4)
        self.assertEqual(snapshot["total_tasks"], 10)
        self.assertEqual(snapshot["percent"], 68)
        self.assertIn("4 / 10", snapshot["detail"])

    def test_frozen_creo_scripts_resolve_inside_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys, "_MEIPASS", folder, create=True):
                path = bundled_creo_script("run_agent_native_batch.ps1")

        self.assertEqual(
            path,
            Path(folder) / "creo_java" / "run_agent_native_batch.ps1",
        )

    def test_frozen_creo_runtime_is_materialized_outside_transient_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transient = root / "_MEI-old"
            run_workspace = root / "run"
            required = (
                "creo_java/run_input_discovery.ps1",
                "creo_java/run_agent_native_batch.ps1",
                "creo_java/stop_agent_native_worker.ps1",
                "creo_java/build/AutoCadDiscovery.class",
                "creo_java/build/NativeArrowWorker.class",
                "scripts/fit_creo_image.ps1",
            )
            for relative in required:
                path = transient / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("utf-8"))
            with patch.object(sys, "_MEIPASS", str(transient), create=True):
                script = materialized_creo_script(
                    run_workspace, "run_agent_native_batch.ps1"
                )

            self.assertTrue(script.is_file())
            self.assertIn(run_workspace / "internal" / "bundled-runtime", script.parents)
            self.assertNotIn(transient, script.parents)
            self.assertEqual(script.read_text(encoding="utf-8"), "creo_java/run_agent_native_batch.ps1")

    def test_review_packet_exposes_candidates_and_placeholder_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            (run_workspace / "results").mkdir(parents=True)
            (run_workspace / "rendered").mkdir()
            (run_workspace / "plans").mkdir()
            (run_workspace / "internal" / "validation").mkdir(parents=True)
            candidate = run_workspace / "rendered" / "step-1-candidate-1.png"
            placeholder = run_workspace / "internal" / "validation" / "step-2-placeholder.png"
            candidate.write_bytes(b"candidate")
            placeholder.write_bytes(b"placeholder")
            (run_workspace / "results" / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {"step_id": "step-1", "status": "QUESTIONED", "image_path": "rendered/step-1-candidate-1.png", "issues": ["箭头待确认"]},
                            {"step_id": "step-2", "status": "FAILED", "image_path": "internal/validation/step-2-placeholder.png", "issues": []},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_workspace / "results" / "candidate-set-0001.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "step_id": "step-1",
                                "selection_allowed": True,
                                "candidates": [
                                    {"candidate_id": "candidate-1", "image_path": "rendered/step-1-candidate-1.png", "recommended": True}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_workspace / "plans" / "locked-render-plan-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "title": "安装阀门",
                                "source_bom_rows": [12],
                                "camera_id": "fixed_456",
                                "receiver_normal_root": [0.0, 0.0, 1.0],
                                "translation_vector_root": [120.0, 0.0, 0.0],
                                "moving_occurrences": [[1, 8]],
                                "receiver_occurrences": [[1, 2]],
                            },
                            {
                                "step_id": "step-2",
                                "title": "连接管路",
                                "source_bom_rows": [15],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 1)
        self.assertEqual(packet["items"][0]["kind"], "candidate")
        self.assertEqual(packet["items"][0]["step_number"], 1)
        self.assertEqual(packet["items"][0]["source_bom_rows"], [12])
        self.assertIn("第 1 步", packet["items"][0]["label"])
        self.assertIn("BOM 第 12 行", packet["items"][0]["label"])
        self.assertIn("安装阀门", packet["items"][0]["label"])
        self.assertTrue(packet["items"][0]["image_path"].endswith("step-1-candidate-1.png"))
        facts = "；".join(packet["items"][0]["deterministic_facts"])
        self.assertIn("fixed_456", facts)
        self.assertIn("Creo 接口法向 +Z", facts)
        self.assertIn("爆炸向量 +X", facts)
        self.assertIn("接收面内侧向爆开", facts)
        self.assertIn("禁止换相机", facts)
        self.assertEqual(packet["items"][1]["kind"], "placeholder")
        self.assertEqual(packet["items"][1]["step_number"], 2)
        self.assertIn("没有可交付图片", packet["items"][1]["issues"][0])

    def test_review_packet_keeps_weak_direction_image_for_informed_review(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            rendered = run_workspace / "rendered"
            results = run_workspace / "results"
            rendered.mkdir(parents=True)
            results.mkdir(parents=True)
            original = rendered / "step-9-candidate-1-original-fixed_123.jpg"
            flipped = rendered / "step-9-candidate-2-flipped-fixed_456.jpg"
            original.write_bytes(b"original")
            flipped.write_bytes(b"flipped")
            (results / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-9",
                                "status": "QUESTIONED",
                                "error_code": "DIRECTION_SIGN_WEAK",
                                "category": "hard_block",
                                "image_path": "rendered/step-9-candidate-1-original-fixed_123.jpg",
                                "issues": ["安装方向正负号证据不足。"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "step_id": "step-9",
                                "factor": "bounded-render-variant",
                                "candidates": [
                                    {
                                        "candidate_id": "candidate-1",
                                        "image_path": "rendered/step-9-candidate-1-original-fixed_123.jpg",
                                        "recommended": True,
                                    },
                                    {
                                        "candidate_id": "candidate-2",
                                        "image_path": "rendered/step-9-candidate-2-flipped-fixed_456.jpg",
                                        "recommended": False,
                                    },
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 1)
        self.assertEqual(len(packet["items"]), 1)
        self.assertEqual(packet["items"][0]["kind"], "failed_image")
        self.assertEqual(packet["items"][0]["category"], "hard_block")
        self.assertTrue(packet["items"][0]["override_allowed"])
        self.assertIsNone(packet["items"][0]["guided_form"])

    def test_review_packet_explains_when_no_candidates_passed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            results = workspace / "runs" / "run-1" / "results"
            results.mkdir(parents=True)
            (results / "validation-0001.json").write_text(
                json.dumps({"steps": [{"step_id": "step-9", "status": "FAILED", "image_path": ""}]}),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps({"groups": []}), encoding="utf-8"
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 0)
        self.assertIn("没有可采用的真实 Creo 图片", packet["message"])
        self.assertIn("自由文本不能生成坐标", packet["message"])

    def test_questioned_real_image_can_be_adopted_without_generated_variants(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            results = run_workspace / "results"
            rendered = run_workspace / "rendered"
            results.mkdir(parents=True)
            rendered.mkdir()
            image = rendered / "step-1.jpg"
            image.write_bytes(b"real-creo-image")
            (results / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "status": "QUESTIONED",
                                "image_path": "rendered/step-1.jpg",
                                "issues": ["构图待人工确认"],
                                "manual_acceptance_allowed": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps({"groups": []}), encoding="utf-8"
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 1)
        self.assertEqual(packet["items"][0]["kind"], "current")
        self.assertEqual(packet["items"][0]["candidate_id"], "current-image")
        self.assertIn("可直接采用", packet["items"][0]["label"])

    def test_review_packet_hides_steps_already_passed_by_publication(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            results = run_workspace / "results"
            rendered = run_workspace / "rendered"
            results.mkdir(parents=True)
            rendered.mkdir()
            for step_id in ("step-1", "step-2"):
                (rendered / f"{step_id}.jpg").write_bytes(b"real-creo-image")
            (results / "render-batch-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "status": "QUESTIONED",
                                "image_path": "rendered/step-1.jpg",
                                "error_code": "SUBJECT_TOO_SMALL",
                                "primary_code": "SUBJECT_TOO_SMALL",
                                "category": "human_review",
                                "failures": [
                                    {
                                        "code": "SUBJECT_TOO_SMALL",
                                        "message": "主体在画面中偏小。",
                                        "suggested_action": "人工确认或提高 zoom。",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "status": "QUESTIONED",
                                "image_path": "rendered/step-1.jpg",
                                "manual_acceptance_allowed": True,
                            },
                            {
                                "step_id": "step-2",
                                "status": "QUESTIONED",
                                "image_path": "rendered/step-2.jpg",
                                "manual_acceptance_allowed": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps({"groups": []}), encoding="utf-8"
            )
            (results / "publication-0001.json").write_text(
                json.dumps(
                    {
                        "delivery_directory": str(run_workspace / "delivery"),
                        "steps": [
                            {"step_id": "step-1", "status": "PASSED"},
                            {"step_id": "step-2", "status": "QUESTIONED"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(
            [item["step_id"] for item in packet["items"]],
            ["step-2"],
        )

    def test_review_packet_does_not_override_validation_with_raw_render_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            results = run_workspace / "results"
            rendered = run_workspace / "rendered"
            plans = run_workspace / "plans"
            results.mkdir(parents=True)
            rendered.mkdir()
            plans.mkdir()
            previous = rendered / "step-1.jpg"
            previous.write_bytes(b"previous-valid-image")
            (results / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "status": "PASSED",
                                "image_path": "rendered/step-1.jpg",
                                "issues": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "render-batch-0002.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "status": "QUESTIONED",
                                "image_path": "rendered/step-1.jpg",
                                "error_code": "SUBJECT_NOT_DETECTED",
                                "primary_code": "SUBJECT_NOT_DETECTED",
                                "category": "system_retry",
                                "failures": [
                                    {
                                        "code": "SUBJECT_NOT_DETECTED",
                                        "message": "渲染帧中未检测到主体。",
                                        "suggested_action": "回退本次相机参数后重试。",
                                    }
                                ],
                                "expected": {"subject_span": [0.2, 0.8]},
                                "actual": {"composition": {"foreground_pixels": 0}},
                                "attempted_actions": ["已重渲染修订视角"],
                                "suggested_actions": ["回退本次相机参数后重试。"],
                                "retained_image": "rendered/step-1.jpg",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps({"groups": []}), encoding="utf-8"
            )
            (results / "publication-0001.json").write_text(
                json.dumps({"steps": [{"step_id": "step-1", "status": "PASSED"}]}),
                encoding="utf-8",
            )
            (plans / "locked-render-plan-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": "step-1",
                                "title": "安装事故回放零件",
                                "source_bom_rows": [21],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["items"], [])
        self.assertEqual(packet["message"], "没有待处理步骤。")



if __name__ == "__main__":
    unittest.main()
