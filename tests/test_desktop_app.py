from __future__ import annotations

from concurrent.futures import Future
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sop_pipeline.desktop.app import MainWindow, _actionable_review_details
from sop_pipeline.desktop.quick_prompts import QuickPrompt, StaticQuickPromptProvider


class _PendingResolutionService:
    def __init__(self) -> None:
        self.future: Future = Future()

    def resolve_candidate(self, run_id: str, step_id: str, candidate_id: str):
        del run_id, step_id, candidate_id
        return self.future

    def close(self) -> None:
        pass

    def review_packet(self, run_id: str):
        del run_id
        return {
            "message": "还剩 1 个步骤",
            "delivery_directory": "",
            "items": [
                {
                    "kind": "placeholder",
                    "step_id": "step-2",
                    "step_number": 2,
                    "step_title": "安装支架",
                    "candidate_id": "",
                    "image_path": "",
                    "issues": ["需要重新生成"],
                    "label": "step-2 · 待重新生成",
                }
            ],
        }


class DesktopResolutionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_resolving_a_picture_stays_on_review_page(self) -> None:
        service = _PendingResolutionService()
        window = MainWindow(service)  # type: ignore[arg-type]
        window.current_run_id = "run-1"
        window.current_review_item = {
            "kind": "current",
            "step_id": "step-1",
            "candidate_id": "current-image",
        }
        window.pages.setCurrentWidget(window.review_page)

        window._resolve_candidate()

        try:
            self.assertIs(window.pages.currentWidget(), window.review_page)
        finally:
            window.progress_timer.stop()
            window.close()

    def test_quick_prompt_provider_appends_without_replacing_manual_text(self) -> None:
        service = _PendingResolutionService()
        provider = StaticQuickPromptProvider(
            (
                QuickPrompt(
                    "custom-two-arrows",
                    "两个箭头",
                    "箭头数量不对，应该为两个",
                ),
            )
        )
        window = MainWindow(  # type: ignore[arg-type]
            service,
            quick_prompt_provider=provider,
        )
        window.review_instruction.setPlainText("保留这条人工说明")

        window.quick_prompt_buttons["custom-two-arrows"].click()
        window.quick_prompt_buttons["custom-two-arrows"].click()

        try:
            self.assertEqual(
                window.review_instruction.toPlainText(),
                "保留这条人工说明\n箭头数量不对，应该为两个",
            )
        finally:
            window.close()

    def test_default_quick_prompts_include_view_and_arrow_repairs(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]

        try:
            self.assertIn("flip-view", window.quick_prompt_buttons)
            self.assertIn("two-arrows", window.quick_prompt_buttons)
            self.assertEqual(
                window.quick_prompt_buttons["flip-view"].text(),
                "翻转视角",
            )
        finally:
            window.close()

    def test_review_instruction_explains_human_facing_identifiers(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]

        try:
            guidance = window.review_instruction_help.text()
            self.assertIn("无需重复步骤序号", guidance)
            self.assertIn("部件名称", guidance)
            self.assertIn("图号或物料编码", guidance)
            self.assertIn("请勿填写系统内部 occurrence 编号", guidance)
        finally:
            window.close()

    def test_successful_resolution_refreshes_and_selects_next_item(self) -> None:
        service = _PendingResolutionService()
        window = MainWindow(service)  # type: ignore[arg-type]
        window.current_run_id = "run-1"
        window.pages.setCurrentWidget(window.review_page)

        window._resolution_ready(
            {"status": "NEEDS_REVIEW", "delivery_directory": ""}
        )

        try:
            self.assertIs(window.pages.currentWidget(), window.review_page)
            self.assertEqual(window.candidate_gallery.count(), 1)
            selected = window.candidate_gallery.currentItem().data(Qt.UserRole)
            self.assertEqual(selected["step_id"], "step-2")
            self.assertIn("第 2 步", window.review_instruction_label.text())
            self.assertIn("安装支架", window.review_instruction_label.text())
        finally:
            window.close()

    def test_actionable_diagnostics_include_values_actions_and_rollback(self) -> None:
        details = _actionable_review_details(
            {
                "category": "system_retry",
                "expected": {"subject_span": [0.2, 0.8]},
                "actual": {"foreground_pixels": 0},
                "attempted_actions": ["已重渲染修订视角"],
                "suggested_actions": ["回退相机参数"],
                "retained_image": "rendered/step-1.jpg",
            }
        )

        joined = "；".join(details)
        self.assertIn("系统回退重试", joined)
        self.assertIn("期望值", joined)
        self.assertIn("foreground_pixels", joined)
        self.assertIn("已重渲染修订视角", joined)
        self.assertIn("回退相机参数", joined)
        self.assertIn("已保留上一张有效图片", joined)

    def test_saved_dashscope_key_is_activated_when_application_opens(self) -> None:
        service = _PendingResolutionService()
        with (
            patch(
                "sop_pipeline.desktop.app.load_dashscope_key",
                return_value="sk-saved-once",
            ),
            patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}),
        ):
            window = MainWindow(service)  # type: ignore[arg-type]
            try:
                self.assertEqual(
                    os.environ["DASHSCOPE_API_KEY"],
                    "sk-saved-once",
                )
                self.assertIn("已安全保存", window.dashscope_status.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
