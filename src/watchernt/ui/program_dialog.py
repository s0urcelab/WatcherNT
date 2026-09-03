from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from watchernt.models import ProgramConfig


class ProgramDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, program: ProgramConfig | None = None) -> None:
        super().__init__(parent)
        self.program = program
        self.setWindowTitle("编辑程序" if program else "添加程序")
        self.setMinimumWidth(560)
        self.name_edit = QLineEdit()
        self.executable_edit = QLineEdit()
        self.arguments_edit = QLineEdit()
        self.workdir_edit = QLineEdit()
        self.keep_alive = QCheckBox("程序退出后自动重新启动")
        self.interval = QSpinBox()
        self.interval.setRange(2, 3600)
        self.interval.setSuffix(" 秒")
        self.auto_update = QCheckBox("自动检查更新")
        self.update_interval = QSpinBox()
        self.update_interval.setRange(1, 9999)
        self.update_interval_unit = QComboBox()
        self.update_interval_unit.addItem("分钟", "minutes")
        self.update_interval_unit.addItem("小时", "hours")
        self.update_interval_unit.addItem("天", "days")
        self.update_url_edit = QLineEdit()
        self.update_url_edit.setPlaceholderText("https://example.com/ams")
        self.version_edit = QLineEdit()
        self.restart_update = QCheckBox("更新后重新启动程序")

        form = QFormLayout()
        form.addRow("名称", self.name_edit)
        form.addRow("可执行文件", self._path_row(self.executable_edit, self._choose_executable))
        form.addRow("命令行参数", self.arguments_edit)
        form.addRow("工作目录", self._path_row(self.workdir_edit, self._choose_workdir))
        form.addRow("保活", self.keep_alive)
        form.addRow("检测间隔", self.interval)
        form.addRow("自动更新", self.auto_update)
        form.addRow("更新检查间隔", self._update_interval_row())
        form.addRow("更新索引 URL", self.update_url_edit)
        form.addRow("当前版本", self.version_edit)
        form.addRow("更新行为", self.restart_update)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._load()

    def _path_row(self, edit: QLineEdit, callback: object) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("浏览…")
        button.clicked.connect(callback)  # type: ignore[arg-type]
        layout.addWidget(edit)
        layout.addWidget(button)
        return container

    def _update_interval_row(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.update_interval)
        layout.addWidget(self.update_interval_unit)
        return container

    def _load(self) -> None:
        program = self.program
        if program is None:
            self.keep_alive.setChecked(True)
            self.interval.setValue(10)
            self.update_interval.setValue(1)
            self.update_interval_unit.setCurrentIndex(
                self.update_interval_unit.findData("hours")
            )
            self.version_edit.setText("N/A")
            self.restart_update.setChecked(True)
            return
        self.name_edit.setText(program.name)
        self.executable_edit.setText(program.executable)
        self.arguments_edit.setText(subprocess.list2cmdline(program.arguments))
        self.workdir_edit.setText(program.working_directory)
        self.keep_alive.setChecked(program.keep_alive)
        self.interval.setValue(program.check_interval)
        self.auto_update.setChecked(program.auto_update)
        self.update_interval.setValue(program.update_interval)
        self.update_interval_unit.setCurrentIndex(
            self.update_interval_unit.findData(program.update_interval_unit)
        )
        self.update_url_edit.setText(program.update_url)
        self.version_edit.setText(program.current_version)
        self.restart_update.setChecked(program.restart_after_update)

    def _choose_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择程序",
            "",
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if path:
            self.executable_edit.setText(path)
            if not self.name_edit.text():
                self.name_edit.setText(Path(path).stem)
            if not self.workdir_edit.text():
                self.workdir_edit.setText(str(Path(path).parent))

    def _choose_workdir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if path:
            self.workdir_edit.setText(path)

    def _accept(self) -> None:
        try:
            candidate = self.value()
            candidate.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "配置无效", str(exc))
            return
        self.accept()

    def value(self) -> ProgramConfig:
        arguments = shlex.split(self.arguments_edit.text(), posix=False)
        existing_id = self.program.id if self.program else None
        values = {
            "name": self.name_edit.text().strip(),
            "executable": self.executable_edit.text().strip(),
            "arguments": arguments,
            "working_directory": self.workdir_edit.text().strip(),
            "keep_alive": self.keep_alive.isChecked(),
            "check_interval": self.interval.value(),
            "auto_update": self.auto_update.isChecked(),
            "update_interval": self.update_interval.value(),
            "update_interval_unit": self.update_interval_unit.currentData(),
            "update_url": self.update_url_edit.text().strip(),
            "current_version": self.version_edit.text().strip() or "N/A",
            "restart_after_update": self.restart_update.isChecked(),
        }
        if existing_id:
            values["id"] = existing_id
        return ProgramConfig(**values)  # type: ignore[arg-type]
