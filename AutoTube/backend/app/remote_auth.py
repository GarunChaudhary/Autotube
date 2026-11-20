"""Login for the dashboard. Every visitor (this PC, phone, tunnel, server) must log in first.

Credentials live in data/secrets/credentials.json:
    {"username": "...", "password_hash": "pbkdf2_sha256$<iterations>$<salt>$<hash>"}
To change the password, replace "password_hash" with "password": "<new password>" and restart AutoTube; the
plain password is hashed automatically and removed from the file the next time it is read.

A valid login sets an HMAC-signed session cookie (30 days). Changing the username/password logs every device out.
Without a credentials file the dashboard is locked (only the login page explaining how to set it up is shown).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config, logs

COOKIE = "autotube_session"
SESSION_SECONDS = 30 * 24 * 3600
ITERATIONS = 240_000
# reachable without login: the login itself and a bare liveness probe used by the start scripts
OPEN_PATHS = {"/login", "/api/login", "/api/logout", "/api/ping",
              # app icon + manifest: iOS fetches them for "Add to Home Screen" (no data, safe to serve)
              "/manifest.webmanifest", "/favicon.png", "/icons/apple-touch-icon.png", "/icons/icon-192.png",
              "/icons/icon-512.png", "/icons/icon-maskable-512.png"}
_fails: dict[str, list[float]] = {}
MAX_FAILS, FAIL_WINDOW = 5, 15 * 60
_cache: dict = {"mtime": None, "creds": None}
_HELP = ('To change the login: replace "password_hash" with "password": "YourNewPassword" (and/or edit "username"), '
         'save, then restart AutoTube. The password is hashed automatically.')


def creds_file():
    return config.SECRETS_DIR / "credentials.json"


def _hash(pw: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def _check_hash(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, _ = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(_hash(pw, salt, int(it)), stored)
    except (ValueError, TypeError):
        return False


def _write(username: str, password_hash: str) -> None:
    f = creds_file()
    f.write_text(json.dumps({"_help": _HELP, "username": username, "password_hash": password_hash}, indent=2),
                 encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    _cache["mtime"] = None


def set_credentials(username: str, password: str) -> None:
    if not username.strip() or not password:
        raise ValueError("username and password must not be empty")
    _write(username.strip(), _hash(password))


def credentials() -> dict | None:
    """{'username', 'password_hash'} or None when not set up. Hashes a plain "password" entry in place."""
    f = creds_file()
    if not f.exists():
        # one-time migration from the older .env variables
        u, p = os.getenv("AUTOTUBE_USERNAME", "").strip(), os.getenv("AUTOTUBE_PASSWORD", "")
        if u and p:
            set_credentials(u, p)
            logs.info("auth", "login credentials moved from .env to data/secrets/credentials.json")
        else:
            return None
    st = f.stat()
    mtime = (st.st_mtime_ns, st.st_size)
    if _cache["mtime"] == mtime:
        return _cache["creds"]
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        logs.error("auth", "credentials.json is not valid JSON - dashboard locked until it is fixed")
        return None
    user = str(data.get("username") or "").strip()
    if data.get("password"):
        _write(user, _hash(str(data["password"])))
        return credentials()
    ph = str(data.get("password_hash") or "")
    creds = {"username": user, "password_hash": ph} if user and ph.startswith("pbkdf2_sha256$") else None
    _cache.update(mtime=mtime, creds=creds)
    return creds


def _client_ip(request: Request) -> str:
    h = request.headers
    return (h.get("cf-connecting-ip") or h.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def _secret() -> bytes:
    f = config.SECRETS_DIR / "session.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    c = credentials() or {}
    # tied to the credentials: changing username or password logs every device out
    return hashlib.sha256(f.read_text().strip().encode() + b"|" + c.get("username", "").encode() + b"|"
                          + c.get("password_hash", "").encode()).digest()


def _sign(exp: int) -> str:
    return hmac.new(_secret(), str(exp).encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + SESSION_SECONDS
    return f"{exp}.{_sign(exp)}"


def valid(token: str | None) -> bool:
    if not token or "." not in token or not credentials():
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _sign(exp))


async def middleware(request: Request, call_next):
    path = request.url.path
    if path in OPEN_PATHS or valid(request.cookies.get(COOKIE)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "login required"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def _safe_next(n: str | None) -> str:
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


def login_page(next_path: str = "/", error: str = "", status_code: int = 200) -> HTMLResponse:
    if not credentials():
        error = error or ("Login is not set up yet. Create data/secrets/credentials.json on the AutoTube computer "
                          '({"username": "...", "password": "..."}) and restart AutoTube.')
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>AutoTube login</title><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icons/apple-touch-icon.png"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="AutoTube">
<style>body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e8e8ef;display:flex;min-height:100vh;
align-items:center;justify-content:center;margin:0;padding:16px}}form{{background:#181b24;padding:28px;border-radius:14px;
width:100%;max-width:340px;box-shadow:0 8px 30px #0006}}h1{{font-size:20px;margin:0 0 6px}}p{{color:#9aa0b4;font-size:14px}}
input{{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:1px solid #333a4d;background:#0f1117;
color:#fff;font-size:16px;margin:10px 0}}button{{width:100%;padding:12px;border:0;border-radius:8px;background:#e8be6e;
color:#111;font-weight:700;font-size:16px;cursor:pointer}}.err{{color:#ff8a80}}</style></head><body>
<form method="post" action="/api/login"><h1>AutoTube</h1><p>Log in to your dashboard.</p>{err}
<input type="hidden" name="next" value="{html.escape(_safe_next(next_path))}">
<input type="text" name="username" placeholder="Username" autocomplete="username" autocapitalize="none"
 autocorrect="off" spellcheck="false" autofocus required>
<input type="password" name="password" placeholder="Password" autocomplete="current-password" required>
<button type="submit">Log in</button></form></body></html>""", status_code=status_code,
                        headers={"Cache-Control": "no-store"})


async def login(request: Request):
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = recent
    form = await request.form()
    nxt = _safe_next(str(form.get("next") or "/"))
    if len(recent) >= MAX_FAILS:
        return login_page(nxt, "Too many wrong attempts. Wait 15 minutes.", 429)
    c = credentials()
    user_ok = bool(c) and hmac.compare_digest(str(form.get("username") or "").strip().encode(), c["username"].encode())
    pass_ok = bool(c) and _check_hash(str(form.get("password") or ""), c["password_hash"])
    if not (user_ok and pass_ok):
        _fails[ip].append(now)
        logs.warn("auth", f"failed login from {ip}")
        return login_page(nxt, "Wrong username or password." if c else "", 401)
    _fails.pop(ip, None)
    logs.info("auth", f"login from {ip}")
    https = (request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
             or "cf-ray" in request.headers)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(COOKIE, make_token(), max_age=SESSION_SECONDS, httponly=True, secure=https, samesite="lax")
    return resp


def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp
