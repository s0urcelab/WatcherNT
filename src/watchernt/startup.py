from __future__ import annotations

import os
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WatcherNT"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--background"])
    return subprocess.list2cmdline([sys.executable, "-m", "watchernt.app", "--background"])


def set_start_with_windows(enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("登录自启动仅支持 Windows")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
