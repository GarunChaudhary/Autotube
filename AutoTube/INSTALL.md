# Installing AutoTube AI

No administrator rights are needed. Everything installs per-user.

## Requirements

- Windows 10/11 or Windows Server 2019+, 64-bit
- 16 GB RAM recommended (8 GB minimum for the 3B model)
- About 8 GB of free disk space: tools ~1 GB, AI model ~2 GB, voices 0.2 GB, plus rendered videos (Shorts ~3 MB, long videos ~50–150 MB)
- **Node.js LTS** (for building the dashboard). Already present on this PC.
- An internet connection for the first setup, research, stock footage and uploads

## Automatic install

Double-click `START_AUTOTUBE.bat`. On first run it calls `scripts\setup.ps1`, which installs:

| Component | Source | Location |
|---|---|---|
| Python 3.12 | official python.org NuGet package (works where MSI installers are blocked) | `%LOCALAPPDATA%\AutoTube\python_pkg` |
| FFmpeg (GPL, with libass) | github.com/BtbN/FFmpeg-Builds | `%LOCALAPPDATA%\AutoTube\ffmpeg` |
| Ollama | github.com/ollama/ollama releases (GPU libraries removed when no GPU) | `%LOCALAPPDATA%\AutoTube\ollama` |
| AI model `qwen2.5:3b` | Ollama registry | `%USERPROFILE%\.ollama\models` |
| Python packages | PyPI (`requirements.txt`) | `AutoTube\.venv` |
| Piper voices (lessac, ryan) | Hugging Face rhasspy/piper-voices | `AutoTube\data\voices` |
| Dashboard | npm + `next build` | `AutoTube\frontend\out` |

You can also run it manually: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`. It is safe to re-run.

## Optional free API keys

Edit `.env` (created from `.env.example`), then restart:

- `PEXELS_API_KEY`: https://www.pexels.com/api/ (free; real stock video instead of generated artwork)
- `PIXABAY_API_KEY`: https://pixabay.com/api/docs/ (free)
- `GEMINI_API_KEY`: https://aistudio.google.com/apikey (free tier). **Don't enable billing** on that Google project.

## Connecting YouTube (free, one time)

1. https://console.cloud.google.com/projectcreate: create a project. No billing is needed.
2. *APIs & Services → Library*: enable **YouTube Data API v3** and **YouTube Analytics API**.
3. *OAuth consent screen / Google Auth Platform*: choose **External**, enter the app name and your email, and add
   your Google account as a **Test user**.
4. *Credentials → Create credentials → OAuth client ID → Web application*.
   Authorised redirect URI: `http://localhost:8000/api/youtube/oauth/callback`
5. Download the JSON. In the dashboard, open **YouTube connection**, upload the JSON, click **Connect YouTube**,
   and approve on Google's own page.

Uploads stay **private** until you approve public publishing on the Automation page. Google also keeps API uploads
from unaudited projects private. To lift that, request the free audit:
https://support.google.com/youtube/contact/yt_api_form

## Upgrading the AI model (better scripts, slower)

```
%LOCALAPPDATA%\AutoTube\ollama\ollama.exe pull qwen2.5:7b
```
Then set `OLLAMA_MODEL=qwen2.5:7b` in `.env` and restart.

## Uninstall

Run `STOP_AUTOTUBE.bat`, then delete the `AutoTube` folder, `%LOCALAPPDATA%\AutoTube` and `%USERPROFILE%\.ollama`.
Remove the "AutoTubeAI" entry from Windows Credential Manager, and revoke access at https://myaccount.google.com/permissions.
