"""Thin FFmpeg / FFprobe wrappers."""
from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

from .. import config

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # Windows-only flag; 0 elsewhere


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None, timeout: int = 3600) -> None:
    if not config.FFMPEG:
        raise FFmpegError("FFmpeg not found. Run scripts\\setup.ps1 or set FFMPEG_PATH in .env")
    cmd = [config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({p.returncode}): {p.stderr[-1500:]}")


def probe(path: Path | str) -> dict:
    if not config.FFPROBE:
        raise FFmpegError("ffprobe not found")
    p = subprocess.run([config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {p.stderr[-500:]}")
    return json.loads(p.stdout)


def duration(path: Path | str) -> float:
    return float(probe(path)["format"]["duration"])
