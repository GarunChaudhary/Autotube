"""Pack AutoTube for a Linux server (Oracle Cloud Always Free etc.).

Run on THIS Windows PC:   .venv\\Scripts\\python.exe scripts\\export_for_server.py
Creates  autotube_server_bundle.tar.gz  next to this folder's parent, containing:
  * the app (backend, built dashboard, assets, deploy scripts, requirements)
  * your settings (.env, with Windows-only paths removed)
  * your database, topic banks and channel branding
  * your YouTube login and other secrets, re-encrypted for the server: Windows DPAPI only works on this PC, so
    each secret is decrypted here and re-encrypted with a new key that travels in data/secrets/secure.key.
Not included: finished video files (data/videos), media/voice caches - the server downloads models itself.

The bundle contains secrets. Copy it to the server with scp (encrypted), then delete the local copy.
"""
from __future__ import annotations

import base64
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cryptography.fernet import Fernet  # noqa: E402

from app import config, secure  # noqa: E402

WINDOWS_ONLY_ENV = ("FFMPEG_PATH", "FFPROBE_PATH", "OLLAMA_EXE")
APP_PARTS = ["backend", "assets", "deploy", "requirements.txt", ".env.example", "README.md", "INSTALL.md"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def main() -> None:
    if not (ROOT / "frontend" / "out" / "index.html").exists():
        sys.exit("Build the dashboard first: cd frontend && npm run build")
    out = ROOT.parent / "autotube_server_bundle.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "AutoTube"
        stage.mkdir()
        for part in APP_PARTS:
            src = ROOT / part
            if src.is_dir():
                shutil.copytree(src, stage / part, ignore=SKIP)
            elif src.exists():
                shutil.copy2(src, stage / part)
        shutil.copytree(ROOT / "frontend" / "out", stage / "frontend" / "out")

        # .env without Windows-only tool paths (the server finds ffmpeg/ollama on PATH)
        env = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        env = [l for l in env if not re.match(rf"^({'|'.join(WINDOWS_ONLY_ENV)})=", l)]
        (stage / ".env").write_text("\n".join(env) + "\n", encoding="utf-8")

        data = stage / "data"
        (data / "secrets").mkdir(parents=True)
        (data / "cache").mkdir()
        # consistent copy of the live database
        src_db = sqlite3.connect(config.DB_PATH)
        dst_db = sqlite3.connect(data / "autotube.db")
        src_db.backup(dst_db)
        src_db.close()

        # re-encrypt every secret for the server
        key = Fernet.generate_key()
        f = Fernet(key)
        moved = 0
        for k, v in dst_db.execute("SELECT key, value FROM secure_store").fetchall():
            plain = secure._unprotect(base64.b64decode(v))
            dst_db.execute("UPDATE secure_store SET value=? WHERE key=?",
                           (base64.b64encode(b"fernet:" + f.encrypt(plain)).decode(), k))
            moved += 1
        dst_db.commit()
        dst_db.close()
        (data / "secrets" / "secure.key").write_text(key.decode())
        creds = config.SECRETS_DIR / "credentials.json"           # same dashboard login on the server
        if creds.exists():
            shutil.copy2(creds, data / "secrets" / "credentials.json")

        for p in list(config.CACHE_DIR.glob("bank_*.json")) + list(config.CACHE_DIR.glob("topics_*.json")):
            shutil.copy2(p, data / "cache" / p.name)
        if (config.DATA_DIR / "branding").exists():
            shutil.copytree(config.DATA_DIR / "branding", data / "branding", ignore=SKIP)

        with tarfile.open(out, "w:gz") as tar:
            tar.add(stage, arcname="AutoTube")
    mb = out.stat().st_size / 1e6
    print(f"OK: {out}  ({mb:.0f} MB, {moved} secrets re-encrypted for the server)")
    print("It contains your YouTube login - copy it to the server with scp, then delete this file.")


if __name__ == "__main__":
    main()
