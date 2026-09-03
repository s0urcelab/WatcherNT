from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

UPDATE_INTERVAL_SECONDS = {
    "minutes": 60,
    "hours": 60 * 60,
    "days": 24 * 60 * 60,
}


@dataclass(slots=True)
class ProgramConfig:
    name: str
    executable: str
    id: str = field(default_factory=lambda: str(uuid4()))
    arguments: list[str] = field(default_factory=list)
    working_directory: str = ""
    keep_alive: bool = True
    check_interval: int = 10
    auto_update: bool = False
    update_interval: int = 1
    update_interval_unit: str = "hours"
    update_url: str = ""
    current_version: str = "N/A"
    restart_after_update: bool = True

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("程序名称不能为空")
        if not self.executable.strip():
            raise ValueError("可执行文件不能为空")
        if self.check_interval < 2 or self.check_interval > 3600:
            raise ValueError("检测间隔必须在 2 到 3600 秒之间")
        if self.update_interval < 1 or self.update_interval > 9999:
            raise ValueError("自动更新检查间隔必须在 1 到 9999 之间")
        if self.update_interval_unit not in UPDATE_INTERVAL_SECONDS:
            raise ValueError("自动更新检查间隔单位无效")
        if self.auto_update and not self.update_url.strip():
            raise ValueError("启用自动更新时必须填写更新索引 URL")
        if self.update_url:
            parts = urlsplit(self.update_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("更新索引 URL 必须是有效的 HTTP 或 HTTPS 地址")

    @property
    def workdir(self) -> Path:
        if self.working_directory:
            return Path(self.working_directory)
        return Path(self.executable).parent

    @property
    def update_interval_seconds(self) -> int:
        return self.update_interval * UPDATE_INTERVAL_SECONDS[self.update_interval_unit]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProgramConfig:
        value = dict(value)
        if "update_url" not in value:
            value["update_url"] = value.get(
                "download_url",
                value.get("github_release_url", value.get("manifest_url", "")),
            )
        known = {item.name for item in cls.__dataclass_fields__.values()}
        program = cls(**{key: val for key, val in value.items() if key in known})
        program.arguments = [str(item) for item in program.arguments]
        program.validate()
        return program

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AppConfig:
    schema_version: int = 1
    start_with_windows: bool = False
    programs: list[ProgramConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AppConfig:
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            start_with_windows=bool(value.get("start_with_windows", False)),
            programs=[ProgramConfig.from_dict(item) for item in value.get("programs", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "start_with_windows": self.start_with_windows,
            "programs": [program.to_dict() for program in self.programs],
        }
