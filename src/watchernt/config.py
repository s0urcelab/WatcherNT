from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_data_path

from watchernt.models import AppConfig


class ConfigStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or user_data_path("WatcherNT", appauthor=False)
        self.path = self.data_dir / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点必须是对象")
            return AppConfig.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"无法读取配置 {self.path}: {exc}") from exc

    def save(self, config: AppConfig) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
