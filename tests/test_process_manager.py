from __future__ import annotations

import io
import re
import subprocess
import time

from watchernt.models import ProgramConfig
from watchernt.process_manager import ProcessManager


def test_failure_uses_exponential_backoff(monkeypatch):
    manager = ProcessManager()
    program = ProgramConfig(name="Missing", executable="missing.exe")
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)

    manager.record_failure(program)
    first = manager.state_for(program)
    assert first.failures == 1
    assert first.next_start_at == 102.0

    manager.record_failure(program)
    assert first.failures == 2
    assert first.next_start_at == 104.0


def test_intentional_stop_disables_keepalive(monkeypatch):
    manager = ProcessManager()
    program = ProgramConfig(name="Demo", executable="demo.exe")
    state = manager.state_for(program)
    state.intentionally_stopped = True
    monkeypatch.setattr(manager, "is_running", lambda _: False)

    assert manager.ensure_running(program) is False


def test_stop_all_intentionally_stops_every_program(monkeypatch):
    manager = ProcessManager()
    programs = [
        ProgramConfig(name="First", executable="first.exe"),
        ProgramConfig(name="Second", executable="second.exe"),
    ]
    stopped = []
    monkeypatch.setattr(
        manager,
        "stop",
        lambda program, intentional=True: stopped.append((program.name, intentional)),
    )

    manager.stop_all(programs)

    assert stopped == [("First", True), ("Second", True)]


def test_start_redirects_output_to_program_log(tmp_path, monkeypatch):
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"fake")
    manager = ProcessManager(tmp_path / "logs")
    program = ProgramConfig(name="Demo", executable=str(executable), id="demo-id")
    captured = {}

    class FakeProcess:
        pid = 123
        stdout = None

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(manager, "find_pid", lambda _: None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert manager.start(program) == 123
    assert captured["stderr"] == subprocess.STDOUT
    assert captured["stdout"] == subprocess.PIPE
    content = manager.log_path(program).read_text(encoding="utf-8")
    assert "================ 程序启动 ================" in content


def test_captured_output_lines_have_timestamps(tmp_path):
    manager = ProcessManager(tmp_path / "logs")
    program = ProgramConfig(name="Demo", executable="demo.exe", id="demo-id")

    manager._capture_output(program, io.BytesIO("第一行\nsecond\n".encode()))

    lines = manager.log_path(program).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ", line) for line in lines)
    assert lines[0].endswith("第一行")


def test_read_output_returns_log_tail(tmp_path):
    manager = ProcessManager(tmp_path / "logs")
    program = ProgramConfig(name="Demo", executable="demo.exe", id="demo-id")
    manager.log_dir.mkdir(parents=True)
    manager.log_path(program).write_text("old\nlatest\n", encoding="utf-8")

    output = manager.read_output(program, max_bytes=8).replace("\r\n", "\n")
    assert output == "latest\n"
