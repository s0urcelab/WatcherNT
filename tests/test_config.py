from __future__ import annotations

import json

import pytest

from watchernt.config import ConfigStore
from watchernt.models import AppConfig, ProgramConfig


def test_config_round_trip(tmp_path):
    store = ConfigStore(tmp_path)
    original = AppConfig(
        start_with_windows=True,
        programs=[
            ProgramConfig(
                name="Demo",
                executable=r"C:\Demo\demo.exe",
                arguments=["--port", "8080"],
                current_version="1.2.3",
                update_interval=30,
                update_interval_unit="minutes",
            )
        ],
    )

    store.save(original)
    loaded = store.load()

    assert loaded.to_dict() == original.to_dict()
    assert not store.path.with_suffix(".json.tmp").exists()


def test_invalid_config_is_reported(tmp_path):
    store = ConfigStore(tmp_path)
    store.path.write_text(json.dumps({"programs": [{"name": "", "executable": ""}]}))

    with pytest.raises(ValueError, match="程序名称不能为空"):
        store.load()


def test_program_validation_requires_update_url():
    program = ProgramConfig(name="Demo", executable="demo.exe", auto_update=True)

    with pytest.raises(ValueError, match="更新索引"):
        program.validate()


def test_legacy_download_url_is_migrated():
    program = ProgramConfig.from_dict(
        {
            "name": "Demo",
            "executable": "demo.exe",
            "download_url": "https://example.com/ams",
        }
    )

    assert program.update_url == "https://example.com/ams"
    assert "download_url" not in program.to_dict()


@pytest.mark.parametrize(
    ("value", "unit", "seconds"),
    [
        (5, "minutes", 300),
        (2, "hours", 7200),
        (3, "days", 259200),
    ],
)
def test_update_interval_conversion(value, unit, seconds):
    program = ProgramConfig(
        name="Demo",
        executable="demo.exe",
        update_interval=value,
        update_interval_unit=unit,
    )

    assert program.update_interval_seconds == seconds
