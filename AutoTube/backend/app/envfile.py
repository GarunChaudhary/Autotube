"""Read/update the local .env file (single-user configuration).

Writes are atomic, keep comments and unrelated keys, and also update os.environ so changes apply
immediately without restarting AutoTube. Values are never logged.
"""
from __future__ import annotations

import os
import re
import shutil

from . import config

_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def path():
    return config.ENV_FILE


def get(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def set_values(values: dict[str, str]) -> None:
    p = path()
    if not p.exists():
        example = config.ROOT / ".env.example"
        if example.exists() and p == config.ROOT / ".env":
            shutil.copy(example, p)
        else:
            p.write_text("", encoding="utf-8")
    lines = p.read_text(encoding="utf-8").splitlines()
    pending = dict(values)
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if m and m.group(1) in pending:
            k = m.group(1)
            lines[i] = f"{k}={_quote(pending.pop(k))}"
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        for k, v in pending.items():
            lines.append(f"{k}={_quote(v)}")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(p)
    for k, v in values.items():
        os.environ[k] = v


def _quote(v: str) -> str:
    v = (v or "").replace("\n", "").strip()
    return f'"{v}"' if re.search(r"[\s#'\"]", v) else v
