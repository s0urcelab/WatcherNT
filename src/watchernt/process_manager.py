from __future__ import annotations

import locale
import logging
import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import BinaryIO

import psutil

from watchernt.models import ProgramConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeState:
    pid: int | None = None
    failures: int = 0
    next_start_at: float = 0
    last_start_at: float = 0
    intentionally_stopped: bool = False


class ProcessManager:
    def __init__(self, log_dir: Path | None = None) -> None:
        self.states: dict[str, RuntimeState] = {}
        self.log_dir = log_dir
        self.log_locks: dict[str, Lock] = {}

    def state_for(self, program: ProgramConfig) -> RuntimeState:
        return self.states.setdefault(program.id, RuntimeState())

    def find_pid(self, program: ProgramConfig) -> int | None:
        state = self.state_for(program)
        if state.pid:
            try:
                process = psutil.Process(state.pid)
                if process.is_running() and self._same_executable(process, program.executable):
                    if state.last_start_at and time.monotonic() - state.last_start_at >= 30:
                        state.failures = 0
                    return state.pid
            except (psutil.Error, OSError):
                pass
            if (
                not state.intentionally_stopped
                and state.last_start_at
                and time.monotonic() - state.last_start_at < 30
            ):
                self.record_failure(program)
            state.pid = None
        for process in psutil.process_iter(["pid", "exe"]):
            try:
                if self._same_path(process.info.get("exe"), program.executable):
                    state.pid = int(process.info["pid"])
                    return state.pid
            except (psutil.Error, OSError, TypeError):
                continue
        return None

    def is_running(self, program: ProgramConfig) -> bool:
        return self.find_pid(program) is not None

    def start(self, program: ProgramConfig, *, force: bool = False) -> int:
        program.validate()
        state = self.state_for(program)
        running_pid = self.find_pid(program)
        if running_pid:
            return running_pid
        now = time.monotonic()
        if not force and now < state.next_start_at:
            raise RuntimeError(f"启动冷却中，还需 {state.next_start_at - now:.0f} 秒")
        executable = Path(program.executable)
        if not executable.is_file():
            self.record_failure(program)
            raise FileNotFoundError(f"可执行文件不存在: {executable}")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [str(executable), *program.arguments],
                cwd=str(program.workdir),
                creationflags=flags,
                stdout=subprocess.PIPE if self.log_dir is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except OSError:
            self.record_failure(program)
            raise
        is_restart = state.last_start_at > 0
        state.pid = process.pid
        state.next_start_at = now + 2
        state.last_start_at = now
        state.intentionally_stopped = False
        self.append_event(program, "程序重启" if is_restart else "程序启动")
        if process.stdout is not None:
            Thread(
                target=self._capture_output,
                args=(program, process.stdout),
                name=f"watchernt-output-{program.id}",
                daemon=True,
            ).start()
        log.info("已启动 %s (PID %s)", program.name, process.pid)
        return process.pid

    def stop(
        self,
        program: ProgramConfig,
        timeout: float = 10,
        *,
        intentional: bool = True,
    ) -> None:
        state = self.state_for(program)
        pid = self.find_pid(program)
        state.intentionally_stopped = intentional
        if pid is None:
            return
        try:
            parent = psutil.Process(pid)
            processes = parent.children(recursive=True)
            processes.append(parent)
            for process in reversed(processes):
                process.terminate()
            _, alive = psutil.wait_procs(processes, timeout=timeout)
            for process in alive:
                process.kill()
            psutil.wait_procs(alive, timeout=3)
            log.info("已停止 %s (PID %s)", program.name, pid)
        except psutil.NoSuchProcess:
            pass
        finally:
            state.pid = None

    def stop_all(self, programs: Iterable[ProgramConfig]) -> None:
        for program in programs:
            try:
                self.append_event(program, "WatcherNT 退出，终止程序")
                self.stop(program, intentional=True)
            except (OSError, psutil.Error):
                log.exception("WatcherNT 退出时无法终止 %s", program.name)

    def ensure_running(self, program: ProgramConfig) -> bool:
        state = self.state_for(program)
        if not program.keep_alive or state.intentionally_stopped:
            return self.is_running(program)
        if self.is_running(program):
            return True
        try:
            self.start(program)
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            log.warning("无法保活 %s: %s", program.name, exc)
            return False

    def record_failure(self, program: ProgramConfig) -> None:
        state = self.state_for(program)
        state.failures += 1
        state.next_start_at = time.monotonic() + min(300, 2 ** min(state.failures, 8))

    def log_path(self, program: ProgramConfig) -> Path:
        if self.log_dir is None:
            raise RuntimeError("未配置受管程序日志目录")
        return self.log_dir / f"{program.id}.log"

    def append_event(self, program: ProgramConfig, message: str) -> None:
        self._append_log_line(program, f"{'=' * 16} {message} {'=' * 16}")

    def read_output(self, program: ProgramConfig, max_bytes: int = 20_000) -> str:
        if self.log_dir is None:
            return ""
        try:
            with self.log_path(program).open("rb") as source:
                source.seek(0, os.SEEK_END)
                size = source.tell()
                source.seek(max(0, size - max_bytes))
                content = source.read()
                try:
                    return content.decode("utf-8")
                except UnicodeDecodeError:
                    return content.decode(locale.getpreferredencoding(False), errors="replace")
        except OSError:
            return ""

    def _capture_output(self, program: ProgramConfig, stream: BinaryIO) -> None:
        try:
            for raw_line in stream:
                raw_line = raw_line.rstrip(b"\r\n")
                try:
                    text = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw_line.decode(
                        locale.getpreferredencoding(False),
                        errors="replace",
                    )
                self._append_log_line(program, text)
        except OSError:
            log.exception("读取 %s 的程序输出失败", program.name)
        finally:
            stream.close()

    def _append_log_line(self, program: ProgramConfig, text: str) -> None:
        if self.log_dir is None:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            lock = self.log_locks.setdefault(program.id, Lock())
            with lock, self.log_path(program).open("a", encoding="utf-8", newline="") as output:
                output.write(f"[{timestamp}] {text}\n")
        except OSError:
            log.exception("写入 %s 的程序输出失败", program.name)

    @staticmethod
    def _same_executable(process: psutil.Process, executable: str) -> bool:
        try:
            return ProcessManager._same_path(process.exe(), executable)
        except (psutil.Error, OSError):
            return False

    @staticmethod
    def _same_path(first: str | None, second: str) -> bool:
        if not first:
            return False
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))
