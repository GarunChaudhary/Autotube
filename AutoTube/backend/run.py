"""Entry point: python backend/run.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app import config  # noqa: E402

if __name__ == "__main__":
    pid_file = config.DATA_DIR / "autotube.pid"
    import os
    pid_file.write_text(str(os.getpid()))
    try:
        uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="info")
    finally:
        pid_file.unlink(missing_ok=True)
