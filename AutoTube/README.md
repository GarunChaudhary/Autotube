# AutoTube AI

A local, zero-budget YouTube production studio for Windows. It researches topics, writes and fact-checks
original scripts, narrates them with a neural voice, edits Shorts (9:16) and long videos (16:9) with captions
and music, makes thumbnails and SEO metadata, and uploads to your channel through the official YouTube API.

Everything runs on your PC with free and open-source tools. No paid API is required.

## Quick start

1. Double-click **`START_AUTOTUBE.bat`**. The first run installs everything (see [INSTALL.md](INSTALL.md)).
2. The dashboard opens at **http://localhost:8000**.
3. **Channel & niche**: create your channel profile.
4. **Create video**: produce a Short, then review it in the **Video library**.
5. **YouTube connection**: connect your channel (one-time Google Cloud setup, free).
6. **Automation**: turn on the daily workflow once you are happy with the results.

Stop it with **`STOP_AUTOTUBE.bat`**.

## The pipeline

```
Research → Script → Fact check → Storyboard → Visuals → Voiceover → Editing (captions + render)
→ Thumbnail → SEO → Quality check → Upload (private)
```

| Stage | What it does | Free provider (fallback order) |
|---|---|---|
| Research | Picks fresh topics for your niche and fetches grounded source text | Ollama for ideas, Wikipedia API for sources |
| Script | Original narration with a hook, story beats and a CTA. Long videos are outlined and then written chapter by chapter | Ollama → Gemini (optional) → offline extractive template |
| Fact check | Checks every segment against the sources. Unsupported claims are rewritten or removed | Same LLM chain, then a lexical source-overlap check |
| Visuals | Stock video per beat, never reusing a clip within a video | Pexels → Pixabay → original generated artwork |
| Voiceover | Neural TTS, sentence by sentence, for exact timings | Piper (local) → Windows SAPI |
| Editing | Cover-crop, motion on stills, burned-in animated captions, ducked music, −14 LUFS loudness | FFmpeg |
| Music | Licence-safe background score | Your tracks in `assets/music` → an original synthesised ambient score |
| Thumbnail | 1280×720 with a bold headline, under 2 MB | Pillow |
| SEO | Title, description (with sources, media credits and AI-voice disclosure), tags, hashtags, chapters | LLM chain, then heuristics |
| Quality gate | Scores fact support, length, hook, originality and render validity. Hard blockers send the video to human review | Built in |
| Upload | Resumable upload, retries, thumbnail, then confirmation via `videos.list` | YouTube Data API v3 |

## YouTube connection (permanent)

This is a single-user setup that runs only on this PC.

- **Once:** the YouTube page shows a setup wizard *only* if `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are missing from `.env`.
  Paste the two values from your Google OAuth client and they are saved to the local `.env`. No JSON upload is needed.
- **After that:** click **Connect YouTube**, pick your account on Google's own sign-in page, and approve.
  The page then shows your channel name and picture, with **Reconnect** and **Disconnect** buttons. The client ID and redirect URI sit under *Advanced*.
- AutoTube never asks for your Google password or reads browser cookies. "Connected" is shown only after a real Google callback and token exchange.

Technical details: AutoTube uses the server-side
Authorization Code flow with PKCE (`access_type=offline`, `prompt=consent`, `include_granted_scopes=true`) and validates `state`.
`state` is stored in the database, so a restart mid-login still works.

- The refresh token and client secret are encrypted with **Windows DPAPI** (`data/autotube.db`, table `secure_store`) and never logged.
- Access tokens refresh automatically, and a background health check runs every 45 minutes.
- A working refresh token is never overwritten with null.
- If Google revokes access, the dashboard shows **Reconnect**.
- **Important:** set your Google Auth Platform app to **In production**. Apps left in *Testing* get refresh tokens that expire after 7 days.

## Scheduler

- **Time slots** (default 09:00, 13:00 and 18:00 Shorts, plus a 20:30 long video, IST). Each slot can have its own name, type, time, weekdays and enabled/paused state. Two slots at the same minute are rejected unless you allow it.
- **Daily limits**: Shorts 0–10, long videos 0–5, plus a daily total cap. You can also set the number of concurrent render workers and the upload retry count.
- **Modes**:
  - **Automatic**: produce, quality-check and upload on schedule. A video with any quality blocker still waits for you.
  - **Approval**: every video waits for your approval.
  - **Manual**: you upload.
- **Planner** (runs every 15 minutes): creates exactly one video per upcoming slot. A unique database index makes duplicates impossible.
- **Dispatcher** (runs every minute): uploads at the slot time. Approved public videos are uploaded early with `publishAt`.
- **Upload safety**:
  - An atomic READY→UPLOADING claim prevents double publishing.
  - Failed uploads retry with backoff (5, 20, 60, 180 minutes). Quota errors wait for the daily reset.
  - An upload interrupted by a crash is flagged for you to check rather than blindly retried.
- **Persistence**: the whole queue lives in SQLite, so on restart interrupted production resumes and missed slots are caught up.
- **Start with Windows**: a toggle on the Scheduler page adds a hidden launcher to your Startup folder.
- **Pages**: the Calendar supports drag-and-drop rescheduling, and the Upload queue shows the status, retries and YouTube URL for each video.
- **Statuses**: DRAFT, GENERATING, RENDERING, QUALITY_CHECK, READY, WAITING_FOR_APPROVAL, SCHEDULED, UPLOADING, PUBLISHED, FAILED and PAUSED.

## Publishing

- **Default privacy is Public.** Immediate uploads use `privacyStatus=public`.
- **Scheduled publishing** (on by default): a public video for a future slot is uploaded early as `private` with `publishAt`. YouTube publishes it at the slot time, and a job running every 10 minutes **verifies** that YouTube really shows it as public before marking it PUBLISHED.
- **Private and unlisted uploads** are off unless you enable them, so nothing is left private by accident.
- **Publish now** makes an already-uploaded video public with `videos.update`, without re-uploading. It needs the "Manage your YouTube videos" permission (`youtube.force-ssl`): reconnect once after upgrading.
- **Confirmed privacy:** the actual privacy is read back from YouTube after every upload or update and shown as **YouTube: public/private**. If an unaudited Google project forces uploads to private, the dashboard says so; it is never shown as public.
- **Custom thumbnails:** before `thumbnails.set` the image is validated (JPEG/PNG, ≤ 2 MB, ≥ 640 px wide, 16:9). If it fails, Google's exact error (for example `HTTP 403 forbidden: …`) is stored and shown as **Custom thumbnail unavailable**. Publishing continues, the thumbnail stays on disk, and **Retry thumbnail** works after you verify the channel at https://www.youtube.com/verify.

## Shorts vs long videos

There are two separate render pipelines (`render_short` and `render_long`). Every output is validated with ffprobe, and validated again right before upload:

| | Short | Long |
|---|---|---|
| Resolution | 1080×1920, 9:16 portrait | 1920×1080, 16:9 landscape |
| Codecs | H.264 yuv420p, 30 fps, AAC 48 kHz, MP4 with faststart | same |
| Duration | 30–60 s target, 180 s maximum | 6–12 min target |
| Style | fast cuts, hook card, big captions, progress bar, `#Shorts` | documentary pacing, title and chapter cards, chapters in the description |

A file that fails the specification for its type is never uploaded: a landscape file can't go out as a Short, and a vertical file can't go out as a long video.

## Safety and controls

- **Private by default.** Every upload is private. Scheduled public publishing (`publishAt`) is only used after you
  type `I APPROVE PUBLIC PUBLISHING` on the Automation page. You can revoke it at any time.
- **Emergency stop** in the sidebar halts the queue, the scheduler and uploads immediately.
- **Spending and quota controls**: daily video cap, per-provider daily budgets, and YouTube quota tracking that
  resets at Pacific midnight. Everything is free; the budgets keep you inside free-tier limits.
- **Duplicate prevention**: normalised topic keys plus similarity checks on topics and scripts.
- **Retries**: each stage persists its output. Failed videos retry from the failed stage (default 3 attempts).
  Work interrupted by a shutdown resumes on the next start.
- **Secrets**: the OAuth token is encrypted with Fernet, and the key is stored in Windows Credential Manager.
  AutoTube never sees your Google password. The server only listens on 127.0.0.1.
- **Uploads disclose synthetic media** (`containsSyntheticMedia: true`), and descriptions credit sources, footage and music.

## Important limits (read this)

- **Your PC must be on.** The scheduler runs inside AutoTube. If the PC is off, asleep or AutoTube is closed,
  automation pauses. Missed daily runs are caught up within 6 hours of the next start.
- **Unverified Google API projects can only upload private videos.** Videos uploaded through the API by a project
  that hasn't passed Google's (free) API compliance audit are locked to private. You can still publish them
  manually in YouTube Studio.
- **Testing-mode OAuth** refresh tokens can expire after 7 days. Reconnect on the YouTube page, or publish the
  consent screen to production.
- **Custom thumbnails** require a phone-verified YouTube channel.
- **Content quality**: a 3B model on CPU writes decent, not brilliant, scripts. Review videos before publishing.
  YouTube's policies demonetise "mass-produced or repetitive" content, so use the review step and add your own
  perspective. Better results come from a larger model (`OLLAMA_MODEL=qwen2.5:7b`, slower) or a Gemini key.

## Project layout

```
AutoTube/
  START_AUTOTUBE.bat / STOP_AUTOTUBE.bat   one-click start / stop
  .env.example                             configuration template (copy to .env)
  requirements.txt                         Python dependencies
  backend/app/                             FastAPI app
    main.py            REST API + serves the dashboard
    pipeline.py        stage orchestrator, job worker, daily planning, reports
    scheduler.py       APScheduler jobs (daily batch, retries, stats, report)
    agents/            research, script, factcheck, seo
    media/             tts, visuals, music, subtitles, thumbnail, render (FFmpeg)
    youtube/           oauth (encrypted tokens), api (upload, stats, analytics)
    migrations/        SQL migrations (applied automatically)
  backend/tests/       pytest suite (unit, API, real rendering)
  frontend/            Next.js dashboard (static export served by the backend)
  scripts/             setup.ps1, backup.ps1
  assets/music/        optional licensed music (+ .txt attribution)
  data/                database, rendered videos, logs, voices, cache, secrets, backups
```

## Operations

- **Logs**: `data/logs/autotube.log` (rotating), `data/logs/server.log`, and the *Logs & reports* page.
- **Backups**: `powershell -ExecutionPolicy Bypass -File scripts\backup.ps1` (add `-IncludeVideos` to include rendered files).
  The backup holds a consistent SQLite snapshot, `.env`, the encrypted token and the logs. To restore, stop AutoTube and copy
  `autotube.db` back into `data\`. On a new PC, reconnect YouTube, because the token key is stored per machine.
- **Tests**: `.venv\Scripts\python.exe -m pytest backend\tests -q`
- **Developing the UI**: `cd frontend`, then `set NEXT_PUBLIC_API=http://localhost:8000` and `npm run dev`
  (port 3000). Run `npm run build` to update the bundled dashboard.
- **Database migrations**: add `backend/app/migrations/NNN_name.sql`. Migrations are applied on startup and tracked in `schema_migrations`.
