from __future__ import annotations

from concurrent.futures import Future
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QSettings, QSize, Signal, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
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
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .backend import SubprocessAgentBackend
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


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopAgentService) -> None:
        super().__init__()
        self.service = service
        self.settings = QSettings("QwenCreoSopAgent", "QwenCreoSopAgent")
        self.current_run_id: str | None = None
        self.confirmation_boxes: dict[str, QComboBox] = {}
        self.bridge = FutureBridge(self)
        self.bridge.failed.connect(self._show_error)
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
        setup_form = QFormLayout(setup)
        self.creo_path = QLineEdit(self.settings.value("creo_path", ""))
        self.license_path = QLineEdit(
            self.settings.value("license_path", self.settings.value("jlink_path", ""))
        )
        self.excel_path = QLineEdit(self.settings.value("excel_path", ""))
        self.dashscope_key = QLineEdit()
        self.dashscope_key.setEchoMode(QLineEdit.Password)
        self.dashscope_key.setPlaceholderText("仅保存在本次进程；也可使用 DASHSCOPE_API_KEY")
        setup_form.addRow("Creo 安装目录", self.creo_path)
        setup_form.addRow("Creo 许可证文件", self.license_path)
        setup_form.addRow("J-Link", QLabel("随 Agent 提供，通过已安装 Creo 的官方接口运行"))
        setup_form.addRow("Excel 路径", self.excel_path)
        setup_form.addRow("DashScope Key", self.dashscope_key)
        layout.addWidget(setup)

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
        confirm = QPushButton("确认并开始生成")
        confirm.setMinimumHeight(42)
        confirm.clicked.connect(self._confirm_and_generate)
        layout.addWidget(confirm)
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.progress_title = QLabel("正在生成")
        self.progress_title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.progress_detail = QLabel("任务会在后台独立进程执行，可以安全续跑。")
        self.progress_detail.setWordWrap(True)
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress_detail)
        resume = QPushButton("继续当前任务")
        resume.clicked.connect(self._resume)
        layout.addWidget(resume)
        pause = QPushButton("暂停（保留检查点）")
        pause.clicked.connect(self._pause)
        layout.addWidget(pause)
        review = QPushButton("处理疑惑步骤")
        review.clicked.connect(lambda: self.pages.setCurrentWidget(self.review_page))
        layout.addWidget(review)
        layout.addStretch()
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)
        self.review_step = QLineEdit()
        self.review_candidate = QLineEdit()
        self.candidate_gallery = QListWidget()
        self.candidate_gallery.setIconSize(QSize(220, 160))
        self.candidate_gallery.itemClicked.connect(self._candidate_clicked)
        self.review_instruction = QTextEdit()
        layout.addRow("步骤 ID", self.review_step)
        layout.addRow("候选图片", self.candidate_gallery)
        layout.addRow("候选图 ID", self.review_candidate)
        layout.addRow("或输入修正", self.review_instruction)
        choose = QPushButton("按候选图重新生成")
        choose.clicked.connect(self._resolve_candidate)
        instruct = QPushButton("按文字说明重新生成")
        instruct.clicked.connect(self._resolve_instruction)
        layout.addRow(choose)
        layout.addRow(instruct)
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
        creo_path = self.creo_path.text().strip()
        license_path = self.license_path.text().strip()
        if not creo_path or not license_path:
            self._show_error("首次使用请填写 Creo 安装目录和许可证文件。")
            return
        self.settings.setValue("creo_path", self.creo_path.text().strip())
        self.settings.setValue("license_path", self.license_path.text().strip())
        self.settings.setValue("excel_path", self.excel_path.text().strip())
        os.environ["QWEN_CREO_LOADPOINT"] = creo_path
        os.environ["QWEN_CREO_LICENSE_FILE"] = license_path
        if self.dashscope_key.text().strip():
            os.environ["DASHSCOPE_API_KEY"] = self.dashscope_key.text().strip()
        self.progress_title.setText("正在理解 BOM 与 CAD")
        self.pages.setCurrentWidget(self.progress_page)
        self.bridge.succeeded.connect(self._analysis_ready, Qt.SingleShotConnection)
        self.bridge.watch(self.service.start_analysis(bom, cad))

    def _analysis_ready(self, result: dict) -> None:
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
        self.pages.setCurrentWidget(self.progress_page)
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.bridge.watch(
            self.service.confirm_and_generate(self.current_run_id, answers)
        )

    def _generation_ready(self, outcome: dict) -> None:
        status = outcome.get("status", "")
        if status == "COMPLETED":
            self.progress_title.setText("生成完成")
            self.progress_detail.setText(
                f"交付结果：{outcome.get('delivery_directory', '')}"
            )
        else:
            self.progress_title.setText("部分步骤需要确认")
            self.progress_detail.setText("其他步骤已继续生成，请处理疑惑步骤。")
            self._load_candidate_gallery(outcome)

    def _load_candidate_gallery(self, outcome: dict) -> None:
        self.candidate_gallery.clear()
        delivery = outcome.get("delivery_directory")
        if not delivery:
            return
        image_directory = Path(delivery) / "步骤图片"
        questioned = {
            str(step.get("step_id"))
            for step in outcome.get("steps", [])
            if step.get("status") in {"QUESTIONED", "FAILED"}
        }
        if not image_directory.is_dir():
            return
        for image_path in sorted(image_directory.iterdir()):
            if not image_path.is_file():
                continue
            for step_id in questioned:
                marker = f"-{step_id}-"
                if marker not in image_path.stem:
                    continue
                candidate_id = image_path.stem.split(marker, 1)[1]
                item = QListWidgetItem(QIcon(str(image_path)), f"{step_id} · {candidate_id}")
                item.setData(Qt.UserRole, (step_id, candidate_id))
                self.candidate_gallery.addItem(item)
                break

    def _candidate_clicked(self, item: QListWidgetItem) -> None:
        step_id, candidate_id = item.data(Qt.UserRole)
        self.review_step.setText(step_id)
        self.review_candidate.setText(candidate_id)

    def _resume(self) -> None:
        if not self.current_run_id:
            self._show_error("没有可继续的任务。")
            return
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.bridge.watch(self.service.resume(self.current_run_id))

    def _resume_history(self) -> None:
        run_id = self.history_runs.currentText().strip()
        if not run_id:
            self._show_error("没有历史任务。")
            return
        self.current_run_id = run_id
        self.pages.setCurrentWidget(self.progress_page)
        self._resume()

    def _pause(self) -> None:
        if self.service.pause():
            self.progress_title.setText("正在安全暂停")
            self.progress_detail.setText("已请求停止后台进程；下次继续将从检查点恢复。")
        else:
            self.progress_detail.setText("当前没有正在执行的后台任务。")

    def _resolve_candidate(self) -> None:
        if not self.current_run_id:
            return
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.bridge.watch(
            self.service.resolve_candidate(
                self.current_run_id,
                self.review_step.text().strip(),
                self.review_candidate.text().strip(),
            )
        )
        self.pages.setCurrentWidget(self.progress_page)

    def _resolve_instruction(self) -> None:
        if not self.current_run_id:
            return
        self.bridge.succeeded.connect(self._generation_ready, Qt.SingleShotConnection)
        self.bridge.watch(
            self.service.resolve_instruction(
                self.current_run_id,
                self.review_step.text().strip(),
                self.review_instruction.toPlainText().strip(),
            )
        )
        self.pages.setCurrentWidget(self.progress_page)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Qwen Creo SOP Agent", message)

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
    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    workspace = local_data / "QwenCreoSopAgent"
    service = DesktopAgentService(SubprocessAgentBackend(workspace))
    window = MainWindow(service)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
