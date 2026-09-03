from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from watchernt.controller import Controller
from watchernt.ui.main_window import MainWindow


class TrayIcon(QSystemTrayIcon):
    def __init__(self, app: QApplication, window: MainWindow, controller: Controller) -> None:
        super().__init__(app.windowIcon(), window)
        self.app = app
        self.window = window
        self.controller = controller
        self.setToolTip("WatcherNT")
        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)
        update_action = QAction("全部检查更新", self)
        update_action.triggered.connect(self.check_all_updates)
        menu.addAction(update_action)
        menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.exit_application)
        menu.addAction(exit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        controller.notification.connect(self.notify)
        window.hidden_to_tray.connect(self._hidden)
        self._shown_hint = False

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def check_all_updates(self) -> None:
        for program in self.controller.config.programs:
            if program.update_url:
                self.controller.check_update(program)

    def notify(self, title: str, message: str) -> None:
        self.showMessage(title, message, self.icon(), 5000)

    def _hidden(self) -> None:
        if not self._shown_hint:
            self.showMessage("WatcherNT", "程序仍在后台运行，可从系统托盘重新打开。")
            self._shown_hint = True

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def exit_application(self) -> None:
        self.window.allow_close = True
        self.controller.shutdown()
        self.hide()
        self.app.quit()
