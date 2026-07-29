"""Minimal `.env` loader — stdlib only, no python-dotenv dependency.

Deliberately conservative:

* values already present in the real environment always win, so
  `ANTHROPIC_API_KEY=... python main.py` still overrides the file;
* nothing from the file is ever logged or echoed;
* a missing `.env` is not an error — the pipeline is meant to run without one.

Supported syntax: `KEY=value`, `# comment`, blank lines, optional `export `
prefix, and single- or double-quoted values.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_FILENAME = ".env"


def load_dotenv(path: str | Path | None = None, override: bool = False) -> list[str]:
    """Load `path` (default `./.env`) into `os.environ`.

    Returns the list of variable NAMES that were set, so a caller can report
    what was loaded without ever touching the values.
    """
    env_path = Path(path) if path else Path.cwd() / DEFAULT_FILENAME
    if not env_path.is_file():
        return []

    applied: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in os.environ and not override:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied
