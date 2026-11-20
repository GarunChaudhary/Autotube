#!/usr/bin/env bash
# AutoTube AI - Linux server installer (Ubuntu 22.04/24.04, ARM64 or x86_64; tested target: Oracle Cloud
# Always Free Ampere A1, 4 OCPU / 24 GB). Safe to re-run.
#
#   tar xzf autotube_server_bundle.tar.gz && cd AutoTube && bash deploy/install_server.sh
#
# Installs: FFmpeg, fonts, Ollama + models, Python env, Kokoro/Piper voices, a systemd service that keeps AutoTube
# running 24/7 (restarts on crash/reboot), and Tailscale so ONLY your own devices can open the dashboard.
# The dashboard is never exposed to the internet (it listens on 127.0.0.1 only).
set -euo pipefail

APP="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
cd "$APP"
step() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
envval() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true; }

[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user (e.g. ubuntu), not root. sudo is used where needed."; exit 1; }
[ -f .env ] || cp .env.example .env

step "System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common >/dev/null
sudo add-apt-repository -y multiverse >/dev/null 2>&1 || true
sudo apt-get update -y >/dev/null
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip python3-dev build-essential \
  ffmpeg fontconfig fonts-dejavu-core libsndfile1 espeak-ng sqlite3 curl ca-certificates tzdata
echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ttf-mscorefonts-installer \
  || warn "Microsoft core fonts (Arial/Impact) unavailable - captions will use DejaVu Sans"
python3 -c 'import sys; assert sys.version_info >= (3, 10)' || { echo "Python 3.10+ required (use Ubuntu 24.04)"; exit 1; }

# The 7B writer model needs ~6 GB RAM while generating; add swap on small machines.
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
if [ "$MEM_GB" -lt 12 ] && ! swapon --show | grep -q /swapfile; then
  step "Adding 8 GB swap (only ${MEM_GB} GB RAM)"
  sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "Ollama (local AI)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 2; done
FAST="$(envval OLLAMA_MODEL)"; FAST="${FAST:-qwen2.5:3b}"
WRITER="$(envval OLLAMA_MODEL_WRITER)"; WRITER="${WRITER:-qwen2.5:7b}"
ollama pull "$FAST"
ollama pull "$WRITER"

step "Python environment"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
grep -viE '^(piper-tts|pyttsx3)' requirements.txt > /tmp/autotube-req.txt
.venv/bin/python -m pip install -r /tmp/autotube-req.txt
# Piper is only a fallback voice; skip quietly if no wheel exists for this CPU
.venv/bin/python -m pip install piper-tts >/dev/null 2>&1 || warn "Piper fallback voice not available on this CPU (Kokoro is used)"

step "Narration voices"
mkdir -p data/voices/kokoro
KB="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  [ -s "data/voices/kokoro/$f" ] || curl -fL --retry 3 -o "data/voices/kokoro/$f" "$KB/$f"
done
if .venv/bin/python -c 'import piper' 2>/dev/null && [ ! -s data/voices/en_US-lessac-medium.onnx ]; then
  .venv/bin/python -m piper.download_voices --data-dir data/voices en_US-lessac-medium en_US-ryan-high || true
fi

step "Database"
.venv/bin/python -c "import sys; sys.path.insert(0,'backend'); from app import db; print('migrations applied:', db.migrate())"

step "Self-test"
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
import base64
from app import config, db, secure
assert config.FFMPEG and config.FFPROBE, "ffmpeg missing"
print("ffmpeg:", config.FFMPEG)
print("caption font:", config.FONT_BOLD, "| heavy:", config.FONT_HEAVY)
from app.media import tts
print("kokoro model:", (tts.KOKORO_DIR / "kokoro-v1.0.onnx").exists())
rows = db.query("SELECT key, value FROM secure_store")
bad = []
for r in rows:
    try:
        secure._unprotect(base64.b64decode(r["value"]))
    except Exception:
        bad.append(r["key"])
print(f"secrets: {len(rows) - len(bad)}/{len(rows)} readable")
if bad:
    print("WARNING: unreadable on this server (reconnect YouTube from the dashboard):", ", ".join(bad))
PY

step "Permissions"
chmod 700 data/secrets
chmod 600 .env data/secrets/* 2>/dev/null || true

step "AutoTube service (24/7, starts on boot)"
sudo tee /etc/systemd/system/autotube.service >/dev/null <<UNIT
[Unit]
Description=AutoTube AI
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
User=${RUN_USER}
WorkingDirectory=${APP}
Environment=PYTHONUNBUFFERED=1
Environment=AUTOTUBE_HOST=127.0.0.1
ExecStart=${APP}/.venv/bin/python backend/run.py
Restart=always
RestartSec=10
Nice=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable autotube >/dev/null
sudo systemctl restart autotube
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8000/api/ping >/dev/null && break; sleep 2; done
curl -sf http://127.0.0.1:8000/api/ping >/dev/null && echo "AutoTube is running." \
  || { warn "AutoTube did not start - see: journalctl -u autotube -n 80"; exit 1; }

step "Private access (Tailscale)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! tailscale status >/dev/null 2>&1; then
  echo "Open the link below and log in with the same account you use for Tailscale on your PC/phone:"
  sudo tailscale up --hostname=autotube
fi
sudo tailscale serve --bg --http=80 8000 >/dev/null
TS_IP="$(tailscale ip -4 | head -1)"

cat <<DONE

==========================================================================
 AutoTube is installed and running 24/7 on this server.

 Open the dashboard from any device logged in to your Tailscale account:
     http://autotube      (or http://${TS_IP})
 It is NOT reachable from the public internet.

 Useful commands:
     sudo systemctl status autotube        # is it running?
     journalctl -u autotube -f             # live log
     sudo systemctl restart autotube       # restart

 Reconnect YouTube (only if ever needed): from your PC run
     ssh -L 8000:127.0.0.1:8000 ${RUN_USER}@<server public IP>
 then open http://localhost:8000/youtube/ on that PC and click Reconnect.
==========================================================================
DONE
