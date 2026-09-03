from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from watchernt.config import ConfigStore
from watchernt.controller import Controller
from watchernt.logging_setup import configure_logging
from watchernt.resources import application_icon
from watchernt.tray import TrayIcon
from watchernt.ui.main_window import MainWindow

log = logging.getLogger(__name__)
SERVER_NAME = "WatcherNT-SingleInstance-v1"


class SingleInstance(QObject):
    def __init__(self, show_window: object) -> None:
        super().__init__()
        self.show_window = show_window
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._receive)
        QLocalServer.removeServer(SERVER_NAME)
        if not self.server.listen(SERVER_NAME):
            raise RuntimeError(self.server.errorString())

    def _receive(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket:
                socket.waitForReadyRead(500)
                if bytes(socket.readAll()) == b"show":
                    self.show_window()  # type: ignore[operator]
                socket.disconnectFromServer()

    @staticmethod
    def notify_existing() -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if not socket.waitForConnected(300):
            return False
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WatcherNT")
    parser.add_argument("--background", action="store_true", help="启动后仅显示托盘图标")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("WatcherNT")
    app.setOrganizationName("WatcherNT")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    if SingleInstance.notify_existing():
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "WatcherNT", "当前系统不支持系统托盘。")
        return 1

    store = ConfigStore()
    configure_logging(store.data_dir)
    try:
        config = store.load()
    except ValueError as exc:
        log.exception("配置加载失败")
        QMessageBox.critical(None, "配置加载失败", str(exc))
        return 1
    controller = Controller(config, store)
    window = MainWindow(controller)
    tray = TrayIcon(app, window, controller)
    _instance = SingleInstance(window.show)
    app.aboutToQuit.connect(controller.shutdown)
    tray.show()
    controller.start()
    if not args.background:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
