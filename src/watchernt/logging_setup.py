from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {"token", "key", "secret", "password", "signature", "sig"}


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            return value
        query = [
            (key, "***" if key.lower() in SENSITIVE_KEYS else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        netloc = re.sub(r"^[^/@]+@", "***@", parts.netloc)
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return value


def configure_logging(data_dir: Path) -> Path:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "watchernt.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    return log_path
