from __future__ import annotations

from concurrent.futures import Future
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

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
                    "custom-flip-view",
                    "切换视角",
                    "翻转到另一台固定视角",
                ),
            )
        )
        window = MainWindow(  # type: ignore[arg-type]
            service,
            quick_prompt_provider=provider,
        )
        window.review_instruction.setPlainText("保留这条人工说明")

        window.quick_prompt_buttons["custom-flip-view"].click()
        window.quick_prompt_buttons["custom-flip-view"].click()

        try:
            self.assertEqual(
                window.review_instruction.toPlainText(),
                "保留这条人工说明\n翻转到另一台固定视角",
            )
        finally:
            window.close()

    def test_default_quick_prompts_do_not_offer_camera_override(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]

        try:
            self.assertEqual(window.quick_prompt_buttons, {})
            self.assertTrue(window.quick_prompt_container.isHidden())
        finally:
            window.close()

    def test_direction_gate_uses_structured_fields_without_flip_shortcut(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]
        item = QListWidgetItem("方向待确认")
        item.setData(
            Qt.UserRole,
            {
                "kind": "failed_image",
                "step_id": "step-1",
                "error_code": "DIRECTION_SIGN_WEAK",
                "image_path": "",
                "guided_form": {
                    "title": "确认安装方向",
                    "instruction": "只填写关键信息",
                    "sentence_template": "该零件沿设备总装{axis}轴{sign}方向装入",
                    "submit_label": "按所选方向重新生成",
                    "fields": [
                        {
                            "name": "axis",
                            "label": "安装轴",
                            "type": "choice",
                            "options": ["Z"],
                            "default": "Z",
                        },
                        {
                            "name": "sign",
                            "label": "装入方向",
                            "type": "choice",
                            "options": ["正", "负"],
                            "default": "负",
                        },
                    ],
                },
            },
        )

        window._candidate_clicked(item)

        try:
            self.assertFalse(window.guided_group.isHidden())
            self.assertEqual(
                window.guided_sentence.text(),
                "该零件沿设备总装Z轴负方向装入",
            )
            self.assertNotIn("flip-view", window.quick_prompt_buttons)
            self.assertEqual(
                window.choose_candidate_button.text(),
                "知情采用原图（保留机器失败）",
            )
            self.assertTrue(window.instruct_button.isEnabled())
        finally:
            window.close()

    def test_review_instruction_is_audit_only_and_cannot_override_cad(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]

        try:
            guidance = window.review_instruction_help.text()
            self.assertIn("结构化正负号表单", guidance)
            self.assertIn("必须修复 BOM/Creo 数据", guidance)
            self.assertIn("不能用备注补造", guidance)
            self.assertIn("仅记录", window.review_instruction_label.text())
        finally:
            window.close()

    def test_missing_guided_geometry_disables_ineffective_rerender(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]
        item = QListWidgetItem("缺少接收面")
        item.setData(
            Qt.UserRole,
            {
                "kind": "placeholder",
                "step_id": "step-3",
                "step_number": 3,
                "error_code": "NO_NATIVE_RECEIVER_GEOMETRY",
                "image_path": "",
                "guided_form": None,
                "deterministic_facts": [],
            },
        )

        window._candidate_clicked(item)

        try:
            self.assertFalse(window.instruct_button.isEnabled())
            self.assertEqual(
                window.instruct_button.text(),
                "需修复 BOM/Creo 事实后重试",
            )
            self.assertIn("尚未形成完整", window.review_contract.text())
        finally:
            window.close()

    def test_review_displays_locked_camera_and_explosion_facts(self) -> None:
        window = MainWindow(_PendingResolutionService())  # type: ignore[arg-type]
        item = QListWidgetItem("已锁定结果")
        item.setData(
            Qt.UserRole,
            {
                "kind": "current",
                "step_id": "step-4",
                "step_number": 4,
                "image_path": "",
                "deterministic_facts": [
                    "正式相机 fixed_456（固定双视角中唯一锁定结果）",
                    "爆炸向量 +Y（长度 120.000）（纯平移）",
                    "展示模式：接收面内侧向爆开，接口法向仍保留为装配真值",
                ],
            },
        )

        window._candidate_clicked(item)

        try:
            summary = window.review_contract.text()
            self.assertIn("fixed_456", summary)
            self.assertIn("+Y", summary)
            self.assertIn("侧向爆开", summary)
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

if __name__ == "__main__":
    unittest.main()
