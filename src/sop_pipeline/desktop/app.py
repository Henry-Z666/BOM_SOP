from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QSettings, Signal, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .backend import SubprocessAgentBackend
from .quick_prompts import (
    DEFAULT_QUICK_PROMPT_PROVIDER,
    QuickPromptProvider,
)
from .secret_store import (
    load_dashscope_key,
    save_dashscope_key,
    select_dashscope_key,
)
from .service import DesktopAgentService


class FutureBridge(QObject):
    succeeded = Signal(object)
    failed = Signal(str)

    def watch(self, future: Future) -> None:
        def completed(done: Future) -> None:
            try:
                self.succeeded.emit(done.result())
            except Exception as error:
                self.failed.emit(str(error))

        future.add_done_callback(completed)


_CATEGORY_LABELS = {
    "hard_block": "装配真值阻断",
    "auto_repair": "自动修复后复核",
    "human_review": "人工图片复核",
    "system_retry": "系统回退重试",
}


def _actionable_review_details(entry: dict[str, object]) -> list[str]:
    details: list[str] = []
    category = str(entry.get("category") or "").strip()
    if category:
        details.append(f"处理类别：{_CATEGORY_LABELS.get(category, category)}")
    expected = entry.get("expected")
    if expected:
        details.append(
            "期望值：" + json.dumps(expected, ensure_ascii=False, sort_keys=True)
        )
    actual = entry.get("actual")
    if actual:
        details.append(
            "实际值：" + json.dumps(actual, ensure_ascii=False, sort_keys=True)
        )
    attempted_value = entry.get("attempted_actions", [])
    attempted = (
        [str(value) for value in attempted_value if str(value).strip()]
        if isinstance(attempted_value, (list, tuple))
        else []
    )
    if attempted:
        details.append("系统已尝试：" + "、".join(attempted))
    suggested_value = entry.get("suggested_actions", [])
    suggested = (
        [str(value) for value in suggested_value if str(value).strip()]
        if isinstance(suggested_value, (list, tuple))
        else []
    )
    if suggested:
        details.append("下一步建议：" + "、".join(dict.fromkeys(suggested)))
    if entry.get("retained_image"):
        details.append("已保留上一张有效图片，未被本次失败覆盖")
    return details


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: DesktopAgentService,
        *,
        quick_prompt_provider: QuickPromptProvider | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.quick_prompt_provider = (
            quick_prompt_provider or DEFAULT_QUICK_PROMPT_PROVIDER
        )
        self.settings = QSettings("QwenCreoSopAgent", "QwenCreoSopAgent")
        self.saved_dashscope_key = load_dashscope_key(self.settings)
        if self.saved_dashscope_key:
            # Resume/history actions can launch a worker without visiting the
            # new-analysis path. Activate the persisted key as soon as the
            # application starts so every child Agent process inherits it.
            os.environ["DASHSCOPE_API_KEY"] = self.saved_dashscope_key
        self.current_run_id: str | None = None
        self.progress_started_at = ""
        self.operation_active = False
        self.progress_previous_page: QWidget | None = None
        self.confirmation_boxes: dict[str, QComboBox] = {}
        self.bridge = FutureBridge(self)
        self.bridge.failed.connect(self._show_error)
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(750)
        self.progress_timer.timeout.connect(self._refresh_progress)
        self.setWindowTitle("Qwen Creo SOP Agent")
        self.resize(920, 680)
        self.setAcceptDrops(True)

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.input_page = self._build_input_page()
        self.confirm_page = self._build_confirm_page()
        self.progress_page = self._build_progress_page()
        self.review_page = self._build_review_page()
        for page in (
            self.input_page,
            self.confirm_page,
            self.progress_page,
            self.review_page,
        ):
            self.pages.addWidget(page)

    def _build_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("从 BOM 和 CAD 文件夹开始")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel("拖入 BOM，选择 CAD 文件夹。生成前只需确认一次。"))

        setup = QGroupBox("首次配置")
        self.setup_group = setup
        setup_form = QFormLayout(setup)
        runtime_defaults = _runtime_config_defaults()
        self.creo_path = QLineEdit(
            self.settings.value("creo_path", runtime_defaults["creo_loadpoint"])
        )
        self.license_path = QLineEdit(
            self.settings.value(
                "license_path",
                self.settings.value(
                    "jlink_path", runtime_defaults["license_file"]
                ),
            )
        )
        self.excel_path = QLineEdit(self.settings.value("excel_path", ""))
        self.dashscope_key = QLineEdit()
        self.dashscope_key.setEchoMode(QLineEdit.Password)
        self.dashscope_key.setPlaceholderText(
            "已安全保存，留空继续使用；输入新 Key 可替换"
            if self.saved_dashscope_key
            else "首次输入后安全保存；也可读取 DASHSCOPE_API_KEY"
        )
        self.dashscope_status = QLabel(
            "已安全保存，重启后会自动使用，无需重新输入"
            if self.saved_dashscope_key
            else "尚未保存；首次成功提交后将自动安全保存"
        )
        setup_form.addRow("Creo 安装目录", self.creo_path)
        setup_form.addRow("Creo 许可证文件", self.license_path)
        setup_form.addRow("J-Link", QLabel("随 Agent 提供，通过已安装 Creo 的官方接口运行"))
        setup_form.addRow("Excel 路径", self.excel_path)

        environment_ready = _environment_paths_are_ready(
            self.creo_path.text(), self.license_path.text()
        )
        environment_row = QHBoxLayout()
        self.environment_status = QLabel(
            "运行环境已配置" if environment_ready else "运行环境尚未完整配置"
        )
        self.environment_status.setStyleSheet(
            "color: #26734d; font-weight: 600;"
            if environment_ready
            else "color: #9c4a00; font-weight: 600;"
        )
        environment_row.addWidget(self.environment_status)
        environment_row.addStretch()
        toggle_setup = QPushButton(
            "查看/修改首次配置" if environment_ready else "收起首次配置"
        )
        toggle_setup.clicked.connect(
            lambda: self._toggle_setup_group(toggle_setup)
        )
        environment_row.addWidget(toggle_setup)
        layout.addLayout(environment_row)
        layout.addWidget(setup)
        setup.setVisible(not environment_ready)

        inputs = QGroupBox("本次任务")
        form = QFormLayout(inputs)
        self.bom_path = QLineEdit()
        self.cad_path = QLineEdit()
        bom_row = QHBoxLayout()
        bom_row.addWidget(self.bom_path)
        choose_bom = QPushButton("选择 BOM")
        choose_bom.clicked.connect(self._choose_bom)
        bom_row.addWidget(choose_bom)
        cad_row = QHBoxLayout()
        cad_row.addWidget(self.cad_path)
        choose_cad = QPushButton("选择 CAD 文件夹")
        choose_cad.clicked.connect(self._choose_cad)
        cad_row.addWidget(choose_cad)
        form.addRow("BOM", bom_row)
        form.addRow("CAD 文件夹", cad_row)
        self.dashscope_key_label = QLabel(
            "DashScope Key（已配置）"
            if self.saved_dashscope_key
            else "DashScope Key（首次配置）"
        )
        form.addRow(self.dashscope_key_label, self.dashscope_key)
        form.addRow("Key 状态", self.dashscope_status)
        self.experience_mode = QCheckBox(
            "体验模式：离线语义复核（默认仍生成全部步骤）"
        )
        self.experience_mode.setChecked(
            os.environ.get("QWEN_CREO_EXPERIENCE_MODE") == "1"
        )
        form.addRow("开发验收", self.experience_mode)
        layout.addWidget(inputs)
        start = QPushButton("分析 BOM 与装配方案")
        start.setMinimumHeight(42)
        start.clicked.connect(self._start_analysis)
        layout.addWidget(start)
        history = QGroupBox("历史任务")
        history_layout = QHBoxLayout(history)
        self.history_runs = QComboBox()
        self.history_runs.addItems(self.settings.value("recent_runs", [], type=list))
        history_layout.addWidget(self.history_runs)
        resume_history = QPushButton("继续所选任务")
        resume_history.clicked.connect(self._resume_history)
        history_layout.addWidget(resume_history)
        layout.addWidget(history)
        layout.addStretch()
        return page

    def _toggle_setup_group(self, button: QPushButton) -> None:
        visible = not self.setup_group.isVisible()
        self.setup_group.setVisible(visible)
        button.setText("收起首次配置" if visible else "查看/修改首次配置")

    def _build_confirm_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("生成前确认")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)
        self.confirm_summary = QLabel()
        self.confirm_summary.setWordWrap(True)
        layout.addWidget(self.confirm_summary)
        self.questions_widget = QWidget()
        self.questions_layout = QVBoxLayout(self.questions_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.questions_widget)
        layout.addWidget(scroll)
        actions = QHBoxLayout()
        back = QPushButton("返回上一步")
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.input_page))
        actions.addWidget(back)
        confirm = QPushButton("确认并开始生成")
        confirm.setMinimumHeight(42)
        confirm.clicked.connect(self._confirm_and_generate)
        actions.addWidget(confirm, 1)
        layout.addLayout(actions)
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.progress_title = QLabel("正在生成")
        self.progress_title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.progress_detail = QLabel("任务会在后台独立进程执行，可以安全续跑。")
        self.progress_detail.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress_detail)
        layout.addWidget(self.progress_bar)
        self.progress_back_button = QPushButton("返回上一步")
        self.progress_back_button.clicked.connect(self._back_from_progress)
        layout.addWidget(self.progress_back_button)
        resume = QPushButton("继续当前任务")
        resume.clicked.connect(self._resume)
        layout.addWidget(resume)
        pause = QPushButton("暂停（保留检查点）")
        pause.clicked.connect(self._pause)
        layout.addWidget(pause)
        self.review_button = QPushButton("处理疑惑步骤")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(lambda: self.pages.setCurrentWidget(self.review_page))
        layout.addWidget(self.review_button)
        layout.addStretch()
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("处理待确认步骤")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)
        self.review_guidance = QLabel()
        self.review_guidance.setWordWrap(True)
        layout.addWidget(self.review_guidance)
        body = QHBoxLayout()
        self.candidate_gallery = QListWidget()
        self.candidate_gallery.setMinimumWidth(300)
        self.candidate_gallery.itemClicked.connect(self._candidate_clicked)
        body.addWidget(self.candidate_gallery, 1)
        preview_column = QVBoxLayout()
        self.review_preview = QLabel("请选择左侧步骤或候选图")
        self.review_preview.setAlignment(Qt.AlignCenter)
        self.review_preview.setMinimumSize(480, 300)
        self.review_preview.setStyleSheet("border: 1px solid #666; background: #111;")
        preview_column.addWidget(self.review_preview, 1)
        self.review_reason = QLabel("选中后显示检查结果。")
        self.review_reason.setWordWrap(True)
        preview_column.addWidget(self.review_reason)
        self.review_image_path = QLineEdit()
        self.review_image_path.setReadOnly(True)
        self.review_image_path.setPlaceholderText("选中后显示图片完整路径")
        preview_column.addWidget(self.review_image_path)
        body.addLayout(preview_column, 2)
        layout.addLayout(body, 1)
        self.review_instruction = QTextEdit()
        self.review_instruction.setPlaceholderText(
            "例如：翻转视角；箭头数量改为两个；沿设备 Z 轴正方向装入。"
            "涉及其他部件时请写“部件名称（图号）”。"
        )
        self.review_instruction.setMaximumHeight(100)
        self.review_instruction_label = QLabel(
            "对当前步骤的修正说明（普通语言即可）"
        )
        layout.addWidget(self.review_instruction_label)
        self.review_instruction_help = QLabel(
            "回复规范：当前选中步骤已自动绑定，无需重复步骤序号；"
            "优先写部件名称，重名时补充图号或物料编码；"
            "BOM 行号可用于排查，请勿填写系统内部 occurrence 编号。"
        )
        self.review_instruction_help.setWordWrap(True)
        layout.addWidget(self.review_instruction_help)
        self.quick_prompt_layout = QHBoxLayout()
        self.quick_prompt_buttons: dict[str, QPushButton] = {}
        layout.addLayout(self.quick_prompt_layout)
        self._refresh_quick_prompts()
        layout.addWidget(self.review_instruction)
        actions = QHBoxLayout()
        back = QPushButton("返回上一步")
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.progress_page))
        self.choose_candidate_button = QPushButton("采用选中的候选图")
        self.choose_candidate_button.setEnabled(False)
        self.choose_candidate_button.clicked.connect(self._resolve_candidate)
        self.instruct_button = QPushButton("按说明重新生成当前步骤")
        self.instruct_button.setEnabled(False)
        self.instruct_button.clicked.connect(self._resolve_instruction)
        self.open_delivery_button = QPushButton("打开交付目录")
        self.open_delivery_button.clicked.connect(self._open_delivery_directory)
        actions.addWidget(back)
        actions.addWidget(self.choose_candidate_button)
        actions.addWidget(self.instruct_button)
        actions.addWidget(self.open_delivery_button)
        layout.addLayout(actions)
        self.current_review_item: dict | None = None
        self.review_delivery_directory = ""
        return page

    def _choose_bom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 BOM", "", "Excel BOM (*.xlsx *.xls);;所有文件 (*)"
        )
        if path:
            self.bom_path.setText(path)

    def _choose_cad(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 CAD 文件夹")
        if path:
            self.cad_path.setText(path)

    def _start_analysis(self) -> None:
        bom = Path(self.bom_path.text().strip())
        cad = Path(self.cad_path.text().strip())
        if not bom.is_file() or not cad.is_dir():
            self._show_error("请选择有效的 BOM 文件和 CAD 文件夹。")
            return
        self.current_run_id = None
        self.progress_started_at = datetime.now(timezone.utc).isoformat()
        creo_path = self.creo_path.text().strip()
        license_path = self.license_path.text().strip()
        if not creo_path or not license_path:
            self._show_error("首次使用请填写 Creo 安装目录和许可证文件。")
            return
        dashscope_key, persist_dashscope_key = select_dashscope_key(
            self.dashscope_key.text(),
            self.saved_dashscope_key,
            os.environ.get("DASHSCOPE_API_KEY", ""),
        )
        if not dashscope_key and not self.experience_mode.isChecked():
            self._show_error("首次使用请填写 DashScope Key，用于 Qwen 工艺理解和图片复核。")
            return
        self.settings.setValue("creo_path", self.creo_path.text().strip())
        self.settings.setValue("license_path", self.license_path.text().strip())
        self.settings.setValue("excel_path", self.excel_path.text().strip())
        if dashscope_key and persist_dashscope_key:
            try:
                save_dashscope_key(self.settings, dashscope_key)
                self.settings.sync()
            except OSError as error:
                self._show_error(f"无法安全保存 DashScope Key：{error}")
                return
            self.saved_dashscope_key = dashscope_key
            self.dashscope_key.clear()
            self.dashscope_key.setPlaceholderText(
                "已安全保存，留空继续使用；输入新 Key 可替换"
            )
            self.dashscope_key_label.setText("DashScope Key（已配置）")
            self.dashscope_status.setText(
                "已安全保存，重启后会自动使用，无需重新输入"
            )
        os.environ["QWEN_CREO_LOADPOINT"] = creo_path
        os.environ["QWEN_CREO_LICENSE_FILE"] = license_path
        if dashscope_key:
            os.environ["DASHSCOPE_API_KEY"] = dashscope_key
        if self.experience_mode.isChecked():
            os.environ["QWEN_CREO_EXPERIENCE_MODE"] = "1"
        else:
            os.environ.pop("QWEN_CREO_EXPERIENCE_MODE", None)
        self.progress_title.setText("正在理解 BOM 与 CAD")
        self.progress_bar.setValue(0)
        self.progress_previous_page = self.input_page
        self._set_operation_active(True)
        self.pages.setCurrentWidget(self.progress_page)
        self.review_button.setEnabled(False)
        self.progress_timer.start()
        self.bridge.succeeded.connect(self._analysis_ready, Qt.SingleShotConnection)
        self.bridge.watch(self.service.start_analysis(bom, cad))

    def _analysis_ready(self, result: dict) -> None:
        self._set_operation_active(False)
        self.progress_timer.stop()
        self.current_run_id = result["run_id"]
        recent = [self.current_run_id] + [
            self.history_runs.itemText(index)
            for index in range(self.history_runs.count())
            if self.history_runs.itemText(index) != self.current_run_id
        ]
        recent = recent[:20]
        self.settings.setValue("recent_runs", recent)
        self.history_runs.clear()
        self.history_runs.addItems(recent)
        packet = result["packet"]
        self.confirm_summary.setText(packet.get("summary", "请确认以下方案。"))
        self.confirmation_boxes.clear()
        while self.questions_layout.count():
            child = self.questions_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for item in packet.get("items", []):
            if item.get("category") != "CONFIRMATION":
                continue
            card = QGroupBox(item.get("question", item["item_id"]))
            card_layout = QVBoxLayout(card)
            evidence = QLabel("；".join(item.get("evidence", [])))
            evidence.setWordWrap(True)
            card_layout.addWidget(evidence)
            combo = QComboBox()
            combo.addItems(item.get("options", []))
            recommended = item.get("recommended_option")
            if recommended in item.get("options", []):
                combo.setCurrentText(recommended)
            card_layout.addWidget(combo)
            self.confirmation_boxes[item["item_id"]] = combo
            self.questions_layout.addWidget(card)
        if not self.confirmation_boxes:
            self.questions_layout.addWidget(QLabel("未发现必须人工选择的歧义，可按推荐方案继续。"))
        self.questions_layout.addStretch()
        self.pages.setCurrentWidget(self.confirm_page)

    def _confirm_and_generate(self) -> None:
        if not self.current_run_id:
            return
        answers = {
            item_id: combo.currentText()
            for item_id, combo in self.confirmation_boxes.items()
        }
        self.progress_title.setText("正在生成 SOP")
        self.progress_detail.setText("单个步骤失败不会停止无关步骤。")
        self.progress_previous_page = self.confirm_page
        self._set_operation_active(True)
        self.pages.setCurrentWidget(self.progress_page)
        self.progress_timer.start()
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.bridge.watch(
            self.service.confirm_and_generate(self.current_run_id, answers)
        )

    def _generation_ready(self, outcome: dict) -> None:
        self._set_operation_active(False)
        self.progress_timer.stop()
        status = outcome.get("status", "")
        if status == "COMPLETED":
            self.progress_title.setText("生成完成")
            self.progress_bar.setValue(100)
            self.progress_detail.setText(
                f"交付结果：{outcome.get('delivery_directory', '')}"
            )
            self.review_button.setEnabled(False)
        else:
            self.progress_title.setText("部分步骤需要确认")
            self.progress_detail.setText("其他步骤已继续生成，请处理疑惑步骤。")
            self._load_candidate_gallery(outcome)
            self.review_button.setEnabled(True)
            self.pages.setCurrentWidget(self.review_page)

    def _resolution_ready(self, outcome: dict) -> None:
        """Refresh the review queue in place and advance to the next item."""

        self._set_operation_active(False)
        self.progress_timer.stop()
        self.review_instruction.clear()
        self._load_candidate_gallery(outcome)
        if outcome.get("status") == "COMPLETED":
            self.review_guidance.setText(
                "全部疑惑步骤已处理完成，SOP 与步骤图片已更新。"
            )
        self.pages.setCurrentWidget(self.review_page)

    def _load_candidate_gallery(self, outcome: dict) -> None:
        self.candidate_gallery.clear()
        self.current_review_item = None
        self.choose_candidate_button.setEnabled(False)
        self.instruct_button.setEnabled(False)
        if not self.current_run_id:
            self.review_guidance.setText("当前没有可处理的任务。")
            return
        packet = self.service.review_packet(self.current_run_id)
        self.review_guidance.setText(str(packet.get("message", "")))
        self.review_delivery_directory = str(
            packet.get("delivery_directory", outcome.get("delivery_directory", ""))
        )
        self.open_delivery_button.setEnabled(bool(self.review_delivery_directory))
        for entry in packet.get("items", []):
            item = QListWidgetItem(str(entry.get("label", entry.get("step_id", ""))))
            item.setData(Qt.UserRole, dict(entry))
            self.candidate_gallery.addItem(item)
        if self.candidate_gallery.count():
            self.candidate_gallery.setCurrentRow(0)
            self._candidate_clicked(self.candidate_gallery.item(0))
        else:
            self.review_preview.setText("没有待处理步骤")
            self.review_image_path.clear()
            self.review_reason.setText("本任务没有可供选择或重新生成的步骤。")

    def _refresh_quick_prompts(
        self, context: dict[str, object] | None = None
    ) -> None:
        while self.quick_prompt_layout.count():
            child = self.quick_prompt_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.quick_prompt_buttons.clear()
        self.quick_prompt_layout.addWidget(QLabel("快捷输入："))
        for prompt in self.quick_prompt_provider.prompts(context):
            button = QPushButton(prompt.label)
            button.setToolTip(prompt.text)
            button.clicked.connect(
                lambda _checked=False, text=prompt.text: self._insert_quick_prompt(text)
            )
            self.quick_prompt_layout.addWidget(button)
            self.quick_prompt_buttons[prompt.prompt_id] = button
        self.quick_prompt_layout.addStretch()

    def _insert_quick_prompt(self, text: str) -> None:
        prompt = str(text).strip()
        if not prompt:
            return
        existing = self.review_instruction.toPlainText().rstrip()
        if prompt not in {line.strip() for line in existing.splitlines()}:
            self.review_instruction.setPlainText(
                f"{existing}\n{prompt}" if existing else prompt
            )
        self.review_instruction.moveCursor(QTextCursor.End)
        self.review_instruction.setFocus()

    def _candidate_clicked(self, item: QListWidgetItem) -> None:
        entry = dict(item.data(Qt.UserRole) or {})
        self.current_review_item = entry
        self._refresh_quick_prompts(entry)
        step_number = entry.get("step_number")
        step_title = str(entry.get("step_title", "")).strip()
        if step_number:
            title_suffix = f"“{step_title}”" if step_title else ""
            self.review_instruction_label.setText(
                f"对第 {step_number} 步{title_suffix}的修正说明（普通语言即可）"
            )
        else:
            self.review_instruction_label.setText(
                "对当前步骤的修正说明（普通语言即可）"
            )
        image_path = str(entry.get("image_path", ""))
        self.review_image_path.setText(image_path)
        issues = entry.get("issues", [])
        error_code = str(entry.get("error_code", "")).strip()
        error_message = str(entry.get("error_message", "")).strip()
        reason_parts: list[str] = []
        if step_number:
            reason_parts.append(f"第 {step_number} 步")
        if error_code:
            reason_parts.append(f"错误代码：{error_code}")
        if error_message:
            reason_parts.append(error_message)
        reason_parts.extend(str(issue) for issue in issues)
        reason_parts.extend(_actionable_review_details(entry))
        self.review_reason.setText(
            "检查说明："
            + ("；".join(reason_parts) if reason_parts else "需要人工确认图片表现。")
        )
        pixmap = QPixmap(image_path) if image_path else QPixmap()
        if pixmap.isNull():
            self.review_preview.setPixmap(QPixmap())
            self.review_preview.setText("图片文件不存在，请按说明重新生成此步骤。")
        else:
            self.review_preview.setText("")
            self.review_preview.setPixmap(
                pixmap.scaled(
                    self.review_preview.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        is_candidate = entry.get("kind") in {"candidate", "current"}
        self.choose_candidate_button.setText(
            "采用当前图片"
            if entry.get("kind") == "current"
            else "采用选中的候选图"
        )
        self.choose_candidate_button.setEnabled(is_candidate)
        self.instruct_button.setEnabled(bool(entry.get("step_id")))

    def _resume(self) -> None:
        if not self.current_run_id:
            self._show_error("没有可继续的任务。")
            return
        if self.progress_previous_page is None:
            self.progress_previous_page = self.input_page
        self._set_operation_active(True)
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.progress_timer.start()
        self.bridge.watch(self.service.resume(self.current_run_id))

    def _resume_history(self) -> None:
        run_id = self.history_runs.currentText().strip()
        if not run_id:
            self._show_error("没有历史任务。")
            return
        self.current_run_id = run_id
        self.progress_previous_page = self.input_page
        self.pages.setCurrentWidget(self.progress_page)
        self._resume()

    def _pause(self) -> None:
        if self.service.pause():
            self._set_operation_active(False)
            self.progress_title.setText("正在安全暂停")
            self.progress_detail.setText("已请求停止后台进程；下次继续将从检查点恢复。")
        else:
            self.progress_detail.setText("当前没有正在执行的后台任务。")

    def _resolve_candidate(self) -> None:
        entry = self.current_review_item or {}
        if not self.current_run_id or entry.get("kind") not in {"candidate", "current"}:
            self._show_error("请先从左侧选择一张可采用的图片。")
            return
        self.progress_previous_page = self.review_page
        self._set_operation_active(True)
        self.choose_candidate_button.setEnabled(False)
        self.instruct_button.setEnabled(False)
        self.review_guidance.setText("正在采用当前选择，完成后将自动显示下一项……")
        self.bridge.succeeded.connect(self._resolution_ready, Qt.SingleShotConnection)
        self.progress_timer.start()
        self.bridge.watch(
            self.service.resolve_candidate(
                self.current_run_id,
                str(entry.get("step_id", "")),
                str(entry.get("candidate_id", "")),
            )
        )

    def _resolve_instruction(self) -> None:
        entry = self.current_review_item or {}
        instruction = self.review_instruction.toPlainText().strip()
        if not self.current_run_id or not entry.get("step_id"):
            self._show_error("请先从左侧选择要重新生成的步骤。")
            return
        if not instruction:
            self._show_error("请先输入对当前步骤的修正说明。")
            return
        self.progress_previous_page = self.review_page
        self._set_operation_active(True)
        self.choose_candidate_button.setEnabled(False)
        self.instruct_button.setEnabled(False)
        self.review_guidance.setText("正在重新生成当前步骤，其他待处理项会保留……")
        self.bridge.succeeded.connect(self._resolution_ready, Qt.SingleShotConnection)
        self.progress_timer.start()
        self.bridge.watch(
            self.service.resolve_instruction(
                self.current_run_id,
                str(entry.get("step_id", "")),
                instruction,
            )
        )

    def _open_delivery_directory(self) -> None:
        if self.review_delivery_directory:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(self.review_delivery_directory)
            )

    def _show_error(self, message: str) -> None:
        self._set_operation_active(False)
        self.progress_timer.stop()
        if self.pages.currentWidget() is self.review_page:
            entry = self.current_review_item or {}
            self.choose_candidate_button.setEnabled(
                entry.get("kind") in {"candidate", "current"}
            )
            self.instruct_button.setEnabled(bool(entry.get("step_id")))
            self.review_guidance.setText("当前步骤处理失败，可修改选择或说明后重试。")
        QMessageBox.critical(self, "Qwen Creo SOP Agent", message)

    def _set_operation_active(self, active: bool) -> None:
        self.operation_active = active
        if hasattr(self, "progress_back_button"):
            self.progress_back_button.setEnabled(not active)
            self.progress_back_button.setToolTip(
                "" if not active else "后台任务运行中；请先暂停，再返回上一步。"
            )

    def _back_from_progress(self) -> None:
        if self.operation_active:
            return
        self.pages.setCurrentWidget(self.progress_previous_page or self.input_page)

    def _refresh_progress(self) -> None:
        snapshot = self.service.progress_snapshot(self.current_run_id)
        if (
            self.current_run_id is None
            and self.progress_started_at
            and str(snapshot.get("updated_at", "")) < self.progress_started_at
        ):
            return
        percent = max(0, min(100, int(snapshot.get("percent", 0))))
        self.progress_bar.setValue(percent)
        stage = str(snapshot.get("stage", "正在处理"))
        detail = str(snapshot.get("detail", ""))
        self.progress_title.setText(stage)
        self.progress_detail.setText(detail)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in {".xlsx", ".xls"}:
                self.bom_path.setText(str(path))
                break

    def closeEvent(self, event) -> None:
        self.service.close()
        super().closeEvent(event)


def main() -> int:
    if "--agent-worker" in sys.argv:
        from sop_pipeline.agent.worker_cli import main as worker_main

        worker_args = [argument for argument in sys.argv[1:] if argument != "--agent-worker"]
        return worker_main(worker_args)
    app = QApplication(sys.argv)
    app.setApplicationName("Qwen Creo SOP Agent")
    workspace_override = os.environ.get("QWEN_CREO_AGENT_WORKSPACE", "").strip()
    if workspace_override:
        workspace = Path(workspace_override).expanduser().resolve()
    else:
        local_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        workspace = local_data / "QwenCreoSopAgent"
    service = DesktopAgentService(SubprocessAgentBackend(workspace))
    window = MainWindow(service)
    window.show()
    return app.exec()


def _runtime_config_defaults() -> dict[str, str]:
    defaults = {"creo_loadpoint": "", "license_file": ""}
    config_path = os.environ.get("QWEN_CREO_RUNTIME_CONFIG", "").strip()
    if not config_path:
        return defaults
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    for key in defaults:
        value = payload.get(key)
        if isinstance(value, str):
            defaults[key] = value.strip()
    return defaults


def _environment_paths_are_ready(creo_path: str, license_path: str) -> bool:
    return Path(creo_path.strip()).is_dir() and Path(license_path.strip()).is_file()


if __name__ == "__main__":
    raise SystemExit(main())
