"""Central configuration. Values come from .env (see .env.example) with safe defaults."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]          # AutoTube/
ENV_FILE = Path(os.getenv("AUTOTUBE_ENV_FILE", ROOT / ".env"))
load_dotenv(ENV_FILE)

DATA_DIR = Path(os.getenv("AUTOTUBE_DATA_DIR", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "autotube.db"
OUTPUT_DIR = DATA_DIR / "videos"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
VOICES_DIR = DATA_DIR / "voices"
SECRETS_DIR = DATA_DIR / "secrets"
BACKUP_DIR = DATA_DIR / "backups"
ASSETS_DIR = ROOT / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
FRONTEND_OUT = ROOT / "frontend" / "out"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

for _d in (DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR, VOICES_DIR, SECRETS_DIR, BACKUP_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("AUTOTUBE_HOST", "127.0.0.1")
PORT = int(os.getenv("AUTOTUBE_PORT", "8000"))
BASE_URL = f"http://localhost:{PORT}"

# --- AI providers --------------------------------------------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")                 # fast model: checks, ideas
OLLAMA_MODEL_WRITER = os.getenv("OLLAMA_MODEL_WRITER", "qwen2.5:7b")    # quality model: scripts, SEO, titles
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Stock media (optional; free keys) -----------------------------------
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

# --- TTS ------------------------------------------------------------------
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_US-lessac-medium")

# --- YouTube --------------------------------------------------------------
# OAuth app credentials live in .env (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET), read live by youtube/oauth.py.
# The redirect URI must exactly match the "Authorised redirect URI" of your Google OAuth client.
GOOGLE_CLIENT_SECRET_FILE = SECRETS_DIR / "client_secret.json"      # legacy, imported once into .env if present
OAUTH_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"http://localhost:{PORT}/api/youtube/oauth/callback")
YOUTUBE_DAILY_QUOTA = int(os.getenv("YOUTUBE_DAILY_QUOTA", "10000"))
# Quota costs changed several times in 2025-2026; keep them configurable.
YOUTUBE_COST_INSERT = int(os.getenv("YOUTUBE_COST_INSERT", "100"))
YOUTUBE_COST_THUMBNAIL = int(os.getenv("YOUTUBE_COST_THUMBNAIL", "50"))
YOUTUBE_COST_LIST = int(os.getenv("YOUTUBE_COST_LIST", "1"))


def _find_exe(name: str, extra_globs: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    local = Path(os.getenv("LOCALAPPDATA", "")) / "AutoTube"
    for pattern in extra_globs:
        for p in local.glob(pattern):
            return str(p)
    return None


FFMPEG = os.getenv("FFMPEG_PATH") or _find_exe("ffmpeg", ["ffmpeg/*/bin/ffmpeg.exe", "ffmpeg/bin/ffmpeg.exe"])
FFPROBE = os.getenv("FFPROBE_PATH") or _find_exe("ffprobe", ["ffmpeg/*/bin/ffprobe.exe", "ffmpeg/bin/ffprobe.exe"])
OLLAMA_EXE = os.getenv("OLLAMA_EXE") or _find_exe("ollama", ["ollama/ollama.exe"])

# Font for captions/thumbnails (Windows ships Arial Black / Segoe UI)
# Linux server: the same faces come from Ubuntu's ttf-mscorefonts-installer, DejaVu as last resort.
MS_FONTS = Path("/usr/share/fonts/truetype/msttcorefonts")
SYSTEM_FONT_DIRS = [Path("C:/Windows/Fonts"), MS_FONTS]
_FONT_CANDIDATES = [
    ASSETS_DIR / "fonts" / "Main-Bold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/seguisb.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    MS_FONTS / "Arial_Bold.ttf",
    MS_FONTS / "arialbd.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
FONT_BOLD = next((str(p) for p in _FONT_CANDIDATES if p.exists()), None)
_HEAVY = [Path("C:/Windows/Fonts/ariblk.ttf"), Path("C:/Windows/Fonts/impact.ttf"),
          MS_FONTS / "Arial_Black.ttf", MS_FONTS / "ariblk.ttf", MS_FONTS / "Impact.ttf", MS_FONTS / "impact.ttf"]
FONT_HEAVY = next((str(p) for p in _HEAVY if p.exists()), FONT_BOLD)
