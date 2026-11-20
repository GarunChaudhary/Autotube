"""Scene visual sourcing - real, relevant, licensed media only (no placeholder backgrounds).

Subject archives (no key needed) - images OF the actual topic:
  * Wikimedia Commons      PD / CC0 / CC BY / CC BY-SA
  * Openverse              filtered to licences allowing commercial use + modification
  * NASA Image Library     public domain (images + video), space/science topics
Stock b-roll (free keys) - atmosphere footage for story beats:
  * Pexels video, Pixabay video

Every asset: {"type": "video"|"image", "path", "credit", "provider", "id", "title", "w", "h"}
API responses are cached on disk; downloads are polite (rate-limit aware).
"""
from __future__ import annotations

import hashlib
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image

from .. import config, logs, quota
from ..agents.text_utils import content_words

TIMEOUT = 40
# Wikimedia's policy asks for contact info in the User-Agent; set WIKIMEDIA_CONTACT in .env (e.g. an email or URL).
UA = {"User-Agent": "AutoTubeAI/1.0 (personal local video tool; low volume"
                    + (f"; {os.getenv('WIKIMEDIA_CONTACT')}" if os.getenv("WIKIMEDIA_CONTACT") else "") + ")"}
SUBJECT_PROVIDERS = ("wikimedia", "openverse", "nasa")
STOCK_PROVIDERS = ("pexels", "pixabay")


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


_cooldown: dict[str, float] = {}       # host -> unix time until which we don't call it (rate-limit breaker)


class HostCoolingDown(RuntimeError):
    pass


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url


def _download(url: str, dest: Path, polite_delay: float = 0.0) -> Path:
    """Download with a per-host circuit breaker: a 429 pauses that host (Retry-After, min 2 min) and we move on
    to other sources instead of blocking the pipeline."""
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    host = _host(url)
    if _cooldown.get(host, 0) > time.time():
        raise HostCoolingDown(f"{host} is rate-limiting us; skipping for now")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        if polite_delay:
            time.sleep(polite_delay)
        with requests.get(url, stream=True, timeout=TIMEOUT, headers=UA) as r:
            if r.status_code == 429:
                wait = r.headers.get("Retry-After", "")
                secs = float(wait) if wait.replace(".", "", 1).isdigit() else 120.0
                _cooldown[host] = time.time() + max(120.0, min(secs, 1800.0))
                raise HostCoolingDown(f"{host} returned 429; paused for {int(max(120, min(secs, 1800)))}s")
            if r.status_code in (500, 502, 503, 504) and attempt == 0:
                time.sleep(3)
                continue
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)
            return dest
    raise RuntimeError(f"download failed after retries: {url}")


def _cached_json(key: str, fetch: Callable[[], Any]) -> Any:
    p = config.CACHE_DIR / "api" / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    data = fetch()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return data


def relevant(meta_text: str, must: str) -> bool:
    """Candidate metadata must share at least one meaningful word with the subject/query."""
    want = content_words(must)
    return not want or bool(want & content_words(meta_text))


# ======================================================================= subject archives
_OK_LICENSES = ("cc0", "public domain", "pd", "cc by", "cc-by", "cc by-sa", "cc-by-sa")


# drawn art, as Wikimedia files describe themselves (title, description or categories)
def _wikimedia(query: str, n: int) -> list[dict]:
    def fetch():
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
            "action": "query", "format": "json", "formatversion": 2, "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 30,
            # 1920 is one of Wikimedia's standard (pre-cached) thumbnail widths; odd widths get throttled
            "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1920})
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"wikimedia2_{_h(query)}", fetch)
    out = []
    for p in data.get("query", {}).get("pages", []):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        # reject NonCommercial (monetised channel) and NoDerivatives (we crop/zoom = adapt)
        if not lic or not lic.lower().startswith(_OK_LICENSES) or re.search(r"\b(nc|nd)\b", lic.lower()):
            continue
        if info.get("width", 0) < 900 or info.get("height", 0) < 600:
            continue
        url = info.get("thumburl") or info.get("url")
        if not url or not url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
            continue
        artist = _strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown author"
        title = p.get("title", "").replace("File:", "")
        desc = _strip_html(meta.get("ImageDescription", {}).get("value", ""))[:300]
        out.append({"type": "image", "url": url, "id": f"wm_{p['pageid']}", "title": f"{title} {desc}",
                    "w": info.get("width"), "h": info.get("height"), "provider": "wikimedia",
                    "credit": f"\"{title}\" by {artist[:80]}, {lic}, via Wikimedia Commons"})
        if len(out) >= n:
            break
    return out


_NOT_FOOTAGE = re.compile(r"audio extracted|interview|lecture|podcast|slideshow|animat|cartoon|production|museum|"
                          r"\btour\b|explained|facts|review|trailer|video game|gameplay|vlog|reaction|tutorial|"
                          r"presentation|wikimedia foundation|\bremix|music video|render|\b3d\b|simulation|"
                          r"experiencia|parody|comic", re.I)
_GENERIC_TOPIC = {"disaster", "incident", "death", "deaths", "disappearance", "mystery", "affair", "case", "story",
                  "legend", "event", "accident", "crash", "sinking", "explosion", "attack", "battle", "siege"}
_VIDEO_DERIVS = ("720p.vp9.webm", "480p.vp9.webm", "1080p.vp9.webm", "360p.vp9.webm")  # .mov transcodes are throttled


def _wikimedia_video(query: str, n: int) -> list[dict]:
    """Real archive footage (newsreels, mission film, historic recordings) from Wikimedia Commons. No key.
    Marked strict: the file's own title must name the subject (search alone matches loosely).
    Wikimedia refuses video downloads (429) from apps without contact info in the User-Agent, so this source is
    only used once the owner has set WIKIMEDIA_CONTACT (Providers & setup page)."""
    if not os.getenv("WIKIMEDIA_CONTACT", "").strip():
        return []

    def fetch():
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
            "action": "query", "format": "json", "formatversion": 2, "generator": "search",
            "gsrsearch": f"{query} filetype:video", "gsrnamespace": 6, "gsrlimit": 20,
            "prop": "videoinfo|categories", "viprop": "url|size|extmetadata|derivatives", "cllimit": "max"})
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"wmvideo2_{_h(query)}", fetch)
    out = []
    for p in data.get("query", {}).get("pages", []):
        info = (p.get("videoinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        # genuine archive film (newsreels, NASA/US-government footage) is public domain; modern CC uploads are mostly
        # animations, explainers and tours - not real footage of the event
        if not re.match(r"(public domain|pd\b|pd-|cc0)", lic.lower()):
            continue
        dur = float(info.get("duration") or 0)
        if (info.get("height") or 0) < 300 or not 4 <= dur <= 1800:
            continue
        title = p.get("title", "").replace("File:", "")
        cats = " ".join(c.get("title", "") for c in p.get("categories", []))
        if _NOT_FOOTAGE.search(title) or _NOT_FOOTAGE.search(cats):
            continue
        derivs = {d.get("transcodekey"): d.get("src") for d in info.get("derivatives", [])}
        url = next((derivs[k] for k in _VIDEO_DERIVS if derivs.get(k)), None)
        if not url and (info.get("size") or 0) < 250_000_000 and \
                str(info.get("url", "")).lower().split("?")[0].endswith((".webm", ".ogv", ".mp4")):
            url = info["url"].split("?")[0]   # original file (old archive films often have no large transcode)
        if not url:
            continue
        artist = _strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown author"
        out.append({"type": "video", "url": url, "id": f"wmv_{p['pageid']}", "title": title, "strict": True,
                    "w": info.get("width"), "h": info.get("height"), "dur": dur, "provider": "wikimedia",
                    "credit": f"Video: \"{title[:70]}\" by {artist[:60]}, {lic}, via Wikimedia Commons"})
        if len(out) >= n:
            break
    return out


def names_subject(title: str, topic: str) -> bool:
    """Strict relevance for loosely-searched footage: most of the topic's words appear in the file title."""
    want = content_words(topic) - _GENERIC_TOPIC or content_words(topic)
    if not want:
        return False
    have = content_words(title)
    return len(want & have) >= max(1, -(-len(want) * 2 // 3))


def _openverse(query: str, n: int) -> list[dict]:
    def fetch():
        r = requests.get("https://api.openverse.org/v1/images/", headers=UA, timeout=TIMEOUT, params={
            "q": query, "license_type": "commercial,modification", "page_size": 20, "mature": "false"})
        if r.status_code == 429:
            raise RuntimeError("Openverse anonymous rate limit reached")
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"openverse_{_h(query)}", fetch)
    out = []
    for it in data.get("results", []):
        if (it.get("width") or 0) < 900 or (it.get("height") or 0) < 600:
            continue
        lic = f"CC {str(it.get('license', '')).upper()} {it.get('license_version') or ''}".strip()
        if it.get("license") in ("pdm", "cc0"):
            lic = "Public domain" if it["license"] == "pdm" else "CC0"
        tags = " ".join(t.get("name", "") for t in (it.get("tags") or []))
        out.append({"type": "image", "url": it["url"], "id": f"ov_{it['id']}", "title": f"{it.get('title', '')} {tags}",
                    "w": it.get("width"), "h": it.get("height"), "provider": "openverse",
                    "credit": f"\"{(it.get('title') or 'Untitled')[:70]}\" by {(it.get('creator') or 'unknown')[:60]}, "
                              f"{lic} ({it.get('foreign_landing_url', '')})"})
        if len(out) >= n:
            break
    return out


def _nasa(query: str, n: int) -> list[dict]:
    def fetch():
        r = requests.get("https://images-api.nasa.gov/search", headers=UA, timeout=TIMEOUT,
                         params={"q": query, "media_type": "image,video", "page_size": 25})
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"nasa_{_h(query)}", fetch)
    out = []
    for it in data.get("collection", {}).get("items", []):
        d = (it.get("data") or [{}])[0]
        if d.get("media_type") not in ("image", "video"):
            continue
        out.append({"type": d["media_type"], "collection": it.get("href"), "id": f"nasa_{d.get('nasa_id')}",
                    "title": f"{d.get('title', '')} {' '.join(d.get('keywords', []) or [])}", "w": None, "h": None,
                    "provider": "nasa", "credit": f"\"{d.get('title', '')[:70]}\", NASA (public domain)"})
        if len(out) >= n:
            break
    return out


def _nasa_file(collection_url: str, kind: str) -> str | None:
    files = _cached_json(f"nasa_col_{_h(collection_url)}", lambda: requests.get(collection_url, headers=UA, timeout=TIMEOUT).json())
    prefs = ("~large.jpg", "~orig.jpg", "~medium.jpg") if kind == "image" else ("~medium.mp4", "~mobile.mp4", "~orig.mp4")
    for suf in prefs:
        for f in files:
            if f.endswith(suf):
                return f.replace("http://", "https://").replace(" ", "%20")
    return None


# ======================================================================= stock b-roll
def _pexels(query: str, portrait: bool, n: int) -> list[dict]:
    if not config.PEXELS_API_KEY or not quota.allow("pexels", 1):
        return []
    orient = "portrait" if portrait else "landscape"

    def fetch():
        quota.record("pexels", "videos/search", 1)
        r = requests.get("https://api.pexels.com/videos/search",
                         params={"query": query, "orientation": orient, "per_page": 15},
                         headers={"Authorization": config.PEXELS_API_KEY}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"pexels_v_{orient}_{_h(query)}", fetch)
    out = []
    want_w = 1080 if portrait else 1920
    for v in data.get("videos", []):
        if v.get("duration", 0) < 4:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        if not files:
            continue
        files.sort(key=lambda f: abs(min(f["width"], f["height"]) - min(want_w, 1080)))
        f = files[0]
        slug = (v.get("url") or "").rstrip("/").split("/")[-1].replace("-", " ")
        out.append({"type": "video", "url": f["link"], "id": f"pexels_{v['id']}", "title": slug,
                    "w": f.get("width"), "h": f.get("height"), "provider": "pexels",
                    "credit": f"Video by {v.get('user', {}).get('name', 'Pexels creator')} on Pexels"})
        if len(out) >= n:
            break
    return out


def _pixabay(query: str, portrait: bool, n: int) -> list[dict]:
    if not config.PIXABAY_API_KEY or not quota.allow("pixabay", 1):
        return []

    def fetch():
        quota.record("pixabay", "videos", 1)
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": config.PIXABAY_API_KEY, "q": query[:100], "per_page": 20, "safesearch": "true"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    data = _cached_json(f"pixabay_v_{_h(query)}", fetch)
    out = []
    for h in data.get("hits", []):
        vids = h.get("videos", {})
        f = vids.get("large") if (vids.get("large") or {}).get("url") else vids.get("medium")
        if not f or not f.get("url"):
            continue
        out.append({"type": "video", "url": f["url"], "id": f"pixabay_{h['id']}", "title": h.get("tags", ""),
                    "w": f.get("width"), "h": f.get("height"), "provider": "pixabay",
                    "credit": f"Video by {h.get('user', 'Pixabay creator')} on Pixabay"})
        if len(out) >= n:
            break
    return out


# ======================================================================= public API
def search(provider: str, query: str, portrait: bool, n: int) -> list[dict]:
    if provider == "wikimedia":
        # real moving footage first, then photos/art of the subject
        try:
            vids = _wikimedia_video(query, n)
        except Exception as e:
            logs.warn("visuals", f"wikimedia video search failed for '{query}': {e}")
            vids = []
        return vids + _wikimedia(query, n)
    if provider == "openverse":
        return _openverse(query, n)
    if provider == "nasa":
        return _nasa(query, n)
    if provider == "pexels":
        return _pexels(query, portrait, n)
    if provider == "pixabay":
        return _pixabay(query, portrait, n)
    return []


def materialize(c: dict, video_id: int | None = None) -> dict | None:
    """Download a candidate and verify it is a usable image/video. Returns the asset or None."""
    try:
        url = c.get("url")
        if c["provider"] == "nasa":
            url = _nasa_file(c["collection"], c["type"])
            if not url:
                return None
        ext = ".jpg"
        if c["type"] == "video":
            m = re.search(r"\.(webm|ogv|mov|mp4)(?:$|\?)", url.lower())
            ext = f".{m.group(1)}" if m else ".mp4"
        dest = config.CACHE_DIR / "media" / f"{c['id']}{ext}"
        _download(url, dest, polite_delay=1.0 if c["provider"] in SUBJECT_PROVIDERS else 0.0)
        if c["type"] == "video":
            from . import ffmpeg
            info = ffmpeg.probe(dest)
            if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
                return None
        if c["type"] == "image":
            im = Image.open(dest)
            im.load()
            if im.width < 700 or im.height < 450:
                return None
            if im.mode != "RGB" or im.format != "JPEG":
                im.convert("RGB").save(dest, "JPEG", quality=93)
            c = {**c, "w": im.width, "h": im.height}
        return {k: v for k, v in c.items() if k not in ("url", "collection")} | {"path": str(dest)}
    except HostCoolingDown as e:
        logs.info("visuals", f"skipped {c.get('id')}: {e}", video_id)
        return None
    except Exception as e:
        logs.warn("visuals", f"could not use {c.get('id')}: {e}", video_id)
        return None


def subject_candidates(topic: str, beat: str, providers: list[str], n: int, video_id: int | None = None) -> list[dict]:
    """Archive images/video of the actual subject, relevance-filtered."""
    out: list[dict] = []
    for prov in [p for p in providers if p in SUBJECT_PROVIDERS]:
        for q in ([f"{topic} {beat}"] if beat else []) + [topic]:
            try:
                for c in search(prov, q, False, n * 2):
                    ok = names_subject(c["title"], topic) if c.get("strict") else relevant(c["title"], topic)
                    if ok and c["id"] not in {o["id"] for o in out}:
                        out.append(c)
            except Exception as e:
                logs.warn("visuals", f"{prov} search failed for '{q}': {e}", video_id)
    return out


def stock_candidates(beat: str, portrait: bool, providers: list[str], n: int, video_id: int | None = None) -> list[dict]:
    out: list[dict] = []
    for prov in [p for p in providers if p in STOCK_PROVIDERS]:
        try:
            for c in search(prov, beat, portrait, n * 2):
                if relevant(c["title"], beat) or prov == "pexels" and not c["title"]:
                    out.append(c)
        except Exception as e:
            logs.warn("visuals", f"{prov} search failed for '{beat}': {e}", video_id)
    return out
