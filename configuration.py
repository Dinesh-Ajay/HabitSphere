"""Centralized loading of local, ignored environment configuration."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_POWERSHELL_ENV_LINE = re.compile(r"^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", re.IGNORECASE)
_load_lock = threading.Lock()
_loaded = False


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_environment() -> bool:
    """Loads the ignored .env file once without logging or overwriting secrets."""
    global _loaded
    with _load_lock:
        if _loaded:
            return ENV_PATH.exists()
        _loaded = True

        if not ENV_PATH.exists():
            return False

        for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ENV_LINE.fullmatch(line) or _POWERSHELL_ENV_LINE.fullmatch(line)
            if not match:
                continue
            key, value = match.groups()
            os.environ.setdefault(key, _clean_value(value))
        return True
