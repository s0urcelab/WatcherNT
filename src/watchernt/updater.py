from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urljoin, urlsplit

import httpx

from watchernt.logging_setup import redact_url
from watchernt.models import ProgramConfig
from watchernt.process_manager import ProcessManager

log = logging.getLogger(__name__)
MAX_PACKAGE_SIZE = 512 * 1024 * 1024
HASH_VERSION_PATTERN = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{7})(?![0-9a-fA-F])")


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    version: str
    url: str
    sha256: str | None = None
    size: int | None = None


class Updater:
    def __init__(
        self,
        data_dir: Path,
        process_manager: ProcessManager,
        client: httpx.Client | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.process_manager = process_manager
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
        )

    def check(self, program: ProgramConfig) -> UpdateManifest | None:
        log.info("检查更新索引: %s", redact_url(program.update_url))
        response = self.client.get(program.update_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        if response.url.scheme not in {"http", "https"}:
            raise ValueError("更新索引重定向到了非 HTTP(S) 地址")
        if len(response.content) > 1024 * 1024:
            raise ValueError("更新索引响应过大")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError("更新索引响应不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("更新索引响应根节点必须是对象")
        manifest = self._manifest_from_index(data, str(response.url))
        if manifest.version.lower() == program.current_version.strip().lower():
            return None
        return manifest

    def install(
        self,
        program: ProgramConfig,
        manifest: UpdateManifest,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        notify = progress or (lambda _: None)
        self.process_manager.append_event(
            program,
            f"开始更新：{program.current_version} -> {manifest.version}",
        )
        update_root = self.data_dir / "updates" / program.id
        update_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=update_root) as temporary:
            temporary_path = Path(temporary)
            package = temporary_path / "package.exe"
            notify("正在下载")
            self._download(manifest, package)
            notify("正在校验")
            executable = Path(program.executable).resolve()
            if not executable.is_file():
                raise FileNotFoundError(f"可执行文件不存在: {executable}")
            backup = executable.with_name(f"{executable.name}.watchernt-backup")
            staged = executable.with_name(f"{executable.name}.watchernt-new")
            notify("正在停止程序")
            was_running = self.process_manager.is_running(program)
            old_version = program.current_version
            self.process_manager.stop(program, intentional=False)
            try:
                notify("正在替换文件")
                backup.unlink(missing_ok=True)
                staged.unlink(missing_ok=True)
                shutil.copy2(package, staged)
                os.replace(executable, backup)
                try:
                    os.replace(staged, executable)
                except Exception:
                    os.replace(backup, executable)
                    raise
                program.current_version = manifest.version
                if program.restart_after_update or was_running:
                    notify("正在启动新版本")
                    self.process_manager.start(program, force=True)
                backup.unlink(missing_ok=True)
            except Exception:
                program.current_version = old_version
                if backup.exists():
                    executable.unlink(missing_ok=True)
                    os.replace(backup, executable)
                if was_running and not self.process_manager.is_running(program):
                    try:
                        self.process_manager.start(program, force=True)
                    except OSError:
                        log.exception("更新回滚后无法重启 %s", program.name)
                self.process_manager.append_event(program, "更新失败，已尝试回滚")
                raise
            finally:
                staged.unlink(missing_ok=True)
            notify("更新完成")
            self.process_manager.append_event(program, f"更新完成：{manifest.version}")

    def _download(self, manifest: UpdateManifest, destination: Path) -> None:
        digest = hashlib.sha256()
        total = 0
        with self.client.stream("GET", manifest.url) as response:
            response.raise_for_status()
            if response.url.scheme not in {"http", "https"}:
                raise ValueError("更新包重定向到了非 HTTP(S) 地址")
            declared = response.headers.get("content-length")
            if declared:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise ValueError("更新包 Content-Length 无效") from exc
                if declared_size > MAX_PACKAGE_SIZE:
                    raise ValueError("更新包大小超出限制")
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_PACKAGE_SIZE:
                        raise ValueError("更新包大小超出限制")
                    digest.update(chunk)
                    output.write(chunk)
        if manifest.size is not None and total != manifest.size:
            raise ValueError("更新包大小与预期不一致")
        if manifest.sha256 is not None and digest.hexdigest() != manifest.sha256:
            raise ValueError("更新包 SHA-256 校验失败")

    @staticmethod
    def _manifest_from_index(data: dict[str, object], base_url: str) -> UpdateManifest:
        href = data.get("href")
        paths = data.get("paths")
        if not isinstance(href, str) or not isinstance(paths, list):
            raise ValueError("更新索引缺少有效的 href 或 paths 字段")
        candidates: list[tuple[int, str, str, int]] = []
        for item in paths:
            if not isinstance(item, dict) or item.get("path_type") != "File":
                continue
            name = item.get("name")
            if not isinstance(name, str) or PurePosixPath(name).name != name or "\\" in name:
                continue
            matches = HASH_VERSION_PATTERN.findall(name)
            if len(matches) != 1:
                continue
            try:
                mtime = int(item["mtime"])
                size = int(item["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 < size <= MAX_PACKAGE_SIZE:
                continue
            candidates.append((mtime, name, matches[0].lower(), size))
        if not candidates:
            raise ValueError("更新索引中没有名称包含 7 位哈希的有效文件")
        _, name, version, size = max(candidates, key=lambda item: (item[0], item[1]))
        directory_url = urljoin(base_url, f"{href.rstrip('/')}/")
        download_url = urljoin(directory_url, quote(name, safe=""))
        if urlsplit(download_url).scheme not in {"http", "https"}:
            raise ValueError("更新产物地址必须使用 HTTP 或 HTTPS")
        return UpdateManifest(version=version, url=download_url, size=size)
