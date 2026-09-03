from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from PySide6.QtCore import QObject, QTimer, Signal

from watchernt.config import ConfigStore
from watchernt.models import AppConfig, ProgramConfig
from watchernt.process_manager import ProcessManager
from watchernt.updater import Updater

log = logging.getLogger(__name__)


class Controller(QObject):
    status_changed = Signal(str, bool, str)
    notification = Signal(str, str)
    config_changed = Signal()

    def __init__(self, config: AppConfig, store: ConfigStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.process_manager = ProcessManager(store.data_dir / "logs" / "programs")
        self.updater = Updater(store.data_dir, self.process_manager)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="watchernt")
        self.last_checks: dict[str, float] = {}
        self.last_update_checks: dict[str, float] = {}
        self.active_programs: set[str] = set()
        self.job_lock = Lock()
        self.shutting_down = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)

    def start(self) -> None:
        self.timer.start()
        self.tick()

    def tick(self) -> None:
        now = time.monotonic()
        for program in list(self.config.programs):
            last = self.last_checks.get(program.id, 0)
            if now - last >= program.check_interval:
                self.last_checks[program.id] = now
                self._submit(program.id, "keepalive", self._ensure_program, program)
            update_last = self.last_update_checks.get(program.id, 0)
            if program.auto_update and now - update_last >= program.update_interval_seconds:
                submitted = self._submit(
                    program.id,
                    "update",
                    self._check_and_install,
                    program,
                )
                if submitted:
                    self.last_update_checks[program.id] = now

    def refresh_statuses(self) -> None:
        for program in self.config.programs:
            running = self.process_manager.is_running(program)
            detail = "🟢 运行中" if running else "⚪ 已停止"
            self.status_changed.emit(program.id, running, detail)

    def start_program(self, program: ProgramConfig) -> None:
        self.process_manager.state_for(program).intentionally_stopped = False
        self._submit(program.id, "start", self._start_program, program)

    def stop_program(self, program: ProgramConfig) -> None:
        self._submit(program.id, "stop", self._stop_program, program)

    def check_update(self, program: ProgramConfig) -> None:
        self._submit(program.id, "update", self._check_and_install, program)

    def save(self) -> None:
        self.store.save(self.config)
        self.config_changed.emit()

    def program_log(self, program: ProgramConfig) -> str:
        return self.process_manager.read_output(program)

    def _ensure_program(self, program: ProgramConfig) -> None:
        running = self.process_manager.ensure_running(program)
        detail = "🟢 运行中" if running else "⚪ 已停止"
        self.status_changed.emit(program.id, running, detail)

    def _start_program(self, program: ProgramConfig) -> None:
        self.process_manager.start(program, force=True)
        self.status_changed.emit(program.id, True, "🟢 运行中")

    def _stop_program(self, program: ProgramConfig) -> None:
        self.process_manager.stop(program)
        self.status_changed.emit(program.id, False, "⚪ 已停止")

    def _check_and_install(self, program: ProgramConfig) -> None:
        manifest = self.updater.check(program)
        if manifest is None:
            self.notification.emit(program.name, "当前已是最新版本")
            return
        self.notification.emit(program.name, f"发现版本 {manifest.version}，开始更新")
        self.updater.install(
            program,
            manifest,
            lambda stage: self.status_changed.emit(program.id, True, f"🔄 {stage}"),
        )
        self.store.save(self.config)
        self.config_changed.emit()
        self.notification.emit(program.name, f"已更新到 {manifest.version}")

    def _submit(
        self,
        program_id: str,
        kind: str,
        function: Callable[..., object],
        *args: object,
    ) -> bool:
        key = f"{program_id}:{kind}"
        with self.job_lock:
            if program_id in self.active_programs:
                return False
            self.active_programs.add(program_id)
        future = self.executor.submit(function, *args)
        future.add_done_callback(lambda item: self._job_done(program_id, key, item))
        return True

    def _job_done(self, program_id: str, key: str, future: Future[object]) -> None:
        with self.job_lock:
            self.active_programs.discard(program_id)
        try:
            future.result()
        except Exception as exc:
            log.exception("后台任务失败: %s", key)
            self.notification.emit("WatcherNT", str(exc))

    def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        self.timer.stop()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.process_manager.stop_all(list(self.config.programs))
        self.updater.client.close()
