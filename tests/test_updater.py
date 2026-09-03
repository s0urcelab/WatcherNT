from __future__ import annotations

import hashlib

import httpx
import pytest

from watchernt.models import ProgramConfig
from watchernt.updater import UpdateManifest, Updater


class FakeProcessManager:
    def __init__(self, running: bool = False, fail_start: bool = False) -> None:
        self.running = running
        self.fail_start = fail_start
        self.events = []

    def is_running(self, program: ProgramConfig) -> bool:
        return self.running

    def stop(self, program: ProgramConfig, *, intentional: bool = True) -> None:
        self.running = False

    def start(self, program: ProgramConfig, *, force: bool = False) -> int:
        if self.fail_start:
            raise OSError("start failed")
        self.running = True
        return 123

    def append_event(self, program: ProgramConfig, message: str) -> None:
        self.events.append(message)


def test_check_selects_latest_mtime_and_reads_hash_version(tmp_path):
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://files.example.com/index"
        return httpx.Response(
            200,
            json={
                "href": "/ams",
                "paths": [
                    {
                        "path_type": "File",
                        "name": "ams-server-123abcd.exe",
                        "mtime": 100,
                        "size": 1024,
                    },
                    {
                        "path_type": "File",
                        "name": "ams-server-32eda5f.exe",
                        "mtime": 200,
                        "size": 2048,
                    },
                ],
            },
        )

    updater = Updater(
        tmp_path / "data",
        FakeProcessManager(),
        httpx.Client(transport=httpx.MockTransport(handle)),
    )
    program = ProgramConfig(
        name="App",
        executable="app.exe",
        update_url="http://files.example.com/index",
        current_version="123abcd",
    )

    manifest = updater.check(program)

    assert manifest is not None
    assert manifest.version == "32eda5f"
    assert manifest.url == "http://files.example.com/ams/ams-server-32eda5f.exe"
    assert manifest.size == 2048
    program.current_version = "32EDA5F"
    assert updater.check(program) is None


def test_check_rejects_index_without_hash_artifact(tmp_path):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "href": "/ams",
                "paths": [
                    {
                        "path_type": "File",
                        "name": "ams-server.exe",
                        "mtime": 100,
                        "size": 1024,
                    }
                ],
            },
        )
    )
    updater = Updater(tmp_path / "data", FakeProcessManager(), httpx.Client(transport=transport))
    program = ProgramConfig(
        name="App",
        executable="app.exe",
        update_url="https://files.example.com/index",
    )

    with pytest.raises(ValueError, match="7 位哈希"):
        updater.check(program)


def test_install_replaces_executable(tmp_path):
    package = b"new executable"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=package))
    manager = FakeProcessManager(running=True)
    updater = Updater(tmp_path / "data", manager, httpx.Client(transport=transport))
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    executable = install_dir / "app.exe"
    executable.write_bytes(b"old")
    program = ProgramConfig(name="App", executable=str(executable), current_version="1.0.0")
    manifest = UpdateManifest(
        version="2.0.0",
        url="https://example.com/app.exe",
        sha256=hashlib.sha256(package).hexdigest(),
        size=len(package),
    )

    updater.install(program, manifest)

    assert executable.read_bytes() == package
    assert program.current_version == "2.0.0"
    assert manager.running is True
    assert manager.events == ["开始更新：1.0.0 -> 2.0.0", "更新完成：2.0.0"]


def test_install_rolls_back_if_new_version_cannot_start(tmp_path):
    package = b"new executable"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=package))
    manager = FakeProcessManager(fail_start=True)
    updater = Updater(tmp_path / "data", manager, httpx.Client(transport=transport))
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    executable = install_dir / "app.exe"
    executable.write_bytes(b"old")
    program = ProgramConfig(name="App", executable=str(executable), current_version="1.0.0")
    manifest = UpdateManifest(
        version="2.0.0",
        url="https://example.com/app.exe",
        sha256=hashlib.sha256(package).hexdigest(),
        size=len(package),
    )

    with pytest.raises(OSError, match="start failed"):
        updater.install(program, manifest)

    assert executable.read_bytes() == b"old"
    assert program.current_version == "1.0.0"
    assert manager.events[-1] == "更新失败，已尝试回滚"
