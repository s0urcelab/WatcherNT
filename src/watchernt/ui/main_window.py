from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from watchernt.controller import Controller
from watchernt.models import ProgramConfig
from watchernt.startup import set_start_with_windows
from watchernt.ui.program_dialog import ProgramDialog


class NoHoverDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        option.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, option, index)


class MainWindow(QMainWindow):
    hidden_to_tray = Signal()

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.allow_close = False
        self.setWindowTitle("WatcherNT")
        self.resize(900, 600)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["名称", "状态", "当前版本", "保活", "自动更新"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.setMouseTracking(False)
        self.table.viewport().setMouseTracking(False)
        self.table.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, False)
        self.table.setItemDelegate(NoHoverDelegate(self.table))
        self.table.setStyleSheet(
            "QTableWidget::item:selected {"
            " background-color: palette(highlight);"
            " color: palette(highlighted-text);"
            " outline: none;"
            "}"
            "QTableWidget::item:focus { outline: none; }"
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.edit_program)
        self.table.itemSelectionChanged.connect(self.change_log_program)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(180)
        self._refreshing_log = False
        self._log_refresh_paused = False
        self._log_interaction_active = False
        self.log_resume_timer = QTimer(self)
        self.log_resume_timer.setSingleShot(True)
        self.log_resume_timer.setInterval(3000)
        self.log_resume_timer.timeout.connect(self.resume_log_refresh)
        self.log_view.selectionChanged.connect(self.pause_log_refresh)
        self.log_view.verticalScrollBar().valueChanged.connect(self.pause_log_refresh)
        self.log_view.viewport().installEventFilter(self)
        self.log_view.verticalScrollBar().installEventFilter(self)

        controls = QHBoxLayout()
        for text, callback in (
            ("添加", self.add_program),
            ("编辑", self.edit_program),
            ("删除", self.remove_program),
            ("启动", self.start_program),
            ("停止", self.stop_program),
            ("检查更新", self.check_update),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch()

        start_windows = QCheckBox("登录时启动")
        start_windows.setChecked(controller.config.start_with_windows)
        start_windows.toggled.connect(self.toggle_startup)
        controls.addWidget(start_windows)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("日志"))
        layout.addWidget(self.log_view)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        controller.status_changed.connect(self.update_status)
        controller.config_changed.connect(self.reload)
        controller.notification.connect(self.show_notification)
        self.reload()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh_view)
        self.refresh_timer.start()

    def reload(self) -> None:
        selected = self.selected_program()
        selected_id = selected.id if selected else None
        previous_signal_state = self.table.blockSignals(True)
        selected_row = -1
        try:
            self.table.setRowCount(0)
            for program in self.controller.config.programs:
                row = self.table.rowCount()
                self.table.insertRow(row)
                name = QTableWidgetItem(program.name)
                name.setData(Qt.ItemDataRole.UserRole, program.id)
                self.table.setItem(row, 0, name)
                self.table.setItem(row, 1, QTableWidgetItem("🔄 检查中…"))
                self.table.setItem(row, 2, QTableWidgetItem(program.current_version))
                self.table.setItem(row, 3, QTableWidgetItem("✅" if program.keep_alive else "❌"))
                self.table.setItem(row, 4, QTableWidgetItem("✅" if program.auto_update else "❌"))
                if program.id == selected_id:
                    selected_row = row
            if selected_row < 0 and self.table.rowCount():
                selected_row = 0
            if selected_row >= 0:
                self.table.selectRow(selected_row)
                self.table.setCurrentCell(selected_row, 0)
        finally:
            self.table.blockSignals(previous_signal_state)
        self.controller.refresh_statuses()
        self.refresh_log()

    def selected_program(self) -> ProgramConfig | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        program_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return next(
            (item for item in self.controller.config.programs if item.id == program_id),
            None,
        )

    def add_program(self) -> None:
        dialog = ProgramDialog(self)
        if dialog.exec():
            self.controller.config.programs.append(dialog.value())
            self.controller.save()

    def edit_program(self) -> None:
        program = self.selected_program()
        if program is None:
            return
        dialog = ProgramDialog(self, program)
        if dialog.exec():
            replacement = dialog.value()
            index = self.controller.config.programs.index(program)
            self.controller.config.programs[index] = replacement
            self.controller.save()

    def remove_program(self) -> None:
        program = self.selected_program()
        if program is None:
            return
        answer = QMessageBox.question(self, "删除程序", f"确定删除“{program.name}”的配置吗？")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.config.programs.remove(program)
            self.controller.save()

    def start_program(self) -> None:
        program = self.selected_program()
        if program:
            self.controller.start_program(program)

    def stop_program(self) -> None:
        program = self.selected_program()
        if program:
            self.controller.stop_program(program)

    def check_update(self) -> None:
        program = self.selected_program()
        if program:
            if not program.update_url:
                QMessageBox.information(self, "检查更新", "请先配置更新索引 URL")
                return
            self.controller.check_update(program)

    def update_status(self, program_id: str, running: bool, detail: str) -> None:
        del running
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == program_id:
                self.table.item(row, 1).setText(detail)
                break

    def show_notification(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 10000)

    def refresh_view(self) -> None:
        self.controller.refresh_statuses()
        self.refresh_log()

    def change_log_program(self) -> None:
        self.log_resume_timer.stop()
        self._log_interaction_active = False
        self._log_refresh_paused = False
        self.refresh_log()

    def refresh_log(self) -> None:
        if self._log_refresh_paused:
            return
        program = self.selected_program()
        self._refreshing_log = True
        try:
            if program is None:
                self.log_view.setPlainText("请选择一个程序以查看输出。")
                return
            content = self.controller.program_log(program)
            if not content:
                content = "暂无输出。仅能捕获由 WatcherNT 启动后的 stdout 和 stderr。"
            self.log_view.setPlainText(content)
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
        finally:
            self._refreshing_log = False

    def pause_log_refresh(self, *_args: object) -> None:
        if self._refreshing_log:
            return
        self._log_refresh_paused = True
        self.log_resume_timer.start()

    def resume_log_refresh(self) -> None:
        if self._log_interaction_active:
            self.log_resume_timer.start()
            return
        self._log_refresh_paused = False
        self.refresh_log()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in (
            self.log_view.viewport(),
            self.log_view.verticalScrollBar(),
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                self._log_interaction_active = True
                self.pause_log_refresh()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._log_interaction_active = False
                self.pause_log_refresh()
            elif event.type() == QEvent.Type.Wheel or (
                event.type() == QEvent.Type.MouseMove
                and self._log_interaction_active
            ):
                self.pause_log_refresh()
        return super().eventFilter(watched, event)

    def toggle_startup(self, enabled: bool) -> None:
        try:
            set_start_with_windows(enabled)
            self.controller.config.start_with_windows = enabled
            self.controller.save()
        except OSError as exc:
            QMessageBox.warning(self, "自启动设置失败", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
