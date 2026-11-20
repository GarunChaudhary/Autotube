"""Google OAuth 2.0 for YouTube - several accounts: each channel profile links to its own YouTube account.

* Official Google sign-in in your browser; AutoTube never sees your password and never touches browser cookies.
* The OAuth *application* credentials (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) live in the local .env file,
  written once by the setup wizard. Your personal *authorization* (refresh token) is encrypted with Windows DPAPI.
* Server-side Authorization Code flow + PKCE, access_type=offline, include_granted_scopes=true, state validation.
  `state` is persisted in the database (survives restarts), single-use, expires after 15 minutes.
* Access tokens refresh automatically; a working refresh token is never overwritten with null.
* Connection is only reported after a real Google callback + token exchange + channel lookup.
* Tokens and secrets are never written to logs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets as pysecrets
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .. import config, db, envfile, logs, secure

# Loopback (localhost) redirects over http are allowed by Google; oauthlib needs this flag to accept them.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # videos.update (Publish Now / privacy changes) requires this scope; youtube.upload alone is not enough
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def granted_scopes(channel_id: int | None = None) -> set[str]:
    s = _stored(channel_id).get("scopes")
    return set(s if isinstance(s, list) else str(s or "").split())


def can_manage_videos(channel_id: int | None = None) -> bool:
    g = granted_scopes(channel_id)
    return MANAGE_SCOPE in g or "https://www.googleapis.com/auth/youtube" in g


LEGACY_TOKEN_KEY = "youtube_token"          # single-account versions; moved to the first profile on first use
STATE_TTL_MIN = 15
_CID_RE = re.compile(r"^[0-9]+-[A-Za-z0-9_]+\.apps\.googleusercontent\.com$")


class OAuthError(RuntimeError):
    pass


# ------------------------------------------------------------------ application credentials (.env)
def client_id() -> str:
    return envfile.get("GOOGLE_CLIENT_ID")


def _client_secret() -> str:
    return envfile.get("GOOGLE_CLIENT_SECRET")


def client_configured() -> bool:
    return bool(client_id()) and bool(_client_secret())


def redirect_uri() -> str:
    return envfile.get("GOOGLE_REDIRECT_URI") or config.OAUTH_REDIRECT_URI


def save_client(cid: str, secret: str | None) -> None:
    """Setup wizard: validate and write the OAuth app credentials to .env (applies immediately)."""
    cid = (cid or "").strip()
    if not _CID_RE.match(cid):
        raise OAuthError("That is not a Google OAuth Client ID (format: 123456789-abc...apps.googleusercontent.com)")
    secret = (secret or "").strip()
    if not secret:
        if not _client_secret():
            raise OAuthError("The client secret is required")
        secret = _client_secret()
    if len(secret) < 10 or " " in secret:
        raise OAuthError("That client secret does not look right (copy it from Google Cloud > Clients)")
    envfile.set_values({"GOOGLE_CLIENT_ID": cid, "GOOGLE_CLIENT_SECRET": secret})
    logs.info("oauth", "OAuth application credentials saved to .env")


def import_client_json(raw: bytes) -> None:
    """Advanced/legacy: take ID + secret from Google's downloaded JSON and write them to .env."""
    data = json.loads(raw)
    block = data.get("web") or data.get("installed")
    if not block or not block.get("client_id") or not block.get("client_secret"):
        raise OAuthError("Not a Google OAuth client JSON (expected 'web' or 'installed' with client_id/client_secret)")
    save_client(block["client_id"], block["client_secret"])


def _migrate_legacy_file() -> None:
    """One-time: move credentials from older versions (client_secret.json / database) into .env."""
    if client_configured():
        return
    try:
        f = config.GOOGLE_CLIENT_SECRET_FILE
        if f.exists():
            import_client_json(f.read_bytes())
            f.unlink()
            logs.info("oauth", "moved legacy client_secret.json into .env")
            return
        old_id, old_secret = db.get_setting("oauth_client_id"), secure.get("oauth_client_secret")
        if old_id and old_secret:
            save_client(old_id, old_secret)
            secure.delete("oauth_client_secret")
            logs.info("oauth", "moved OAuth app credentials from the database into .env")
    except Exception as e:
        logs.warn("oauth", f"legacy credentials not migrated: {type(e).__name__}")


def _client_config() -> dict:
    if not client_configured():
        raise OAuthError("Google OAuth app credentials are not set up yet. Complete the one-time setup on the YouTube page.")
    return {"web": {"client_id": client_id(), "client_secret": _client_secret(),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri()]}}


def _flow(state: str | None = None) -> Flow:
    return Flow.from_client_config(_client_config(), scopes=SCOPES, state=state, redirect_uri=redirect_uri())


# ------------------------------------------------------------------ profiles <-> accounts
def default_channel_id() -> int:
    r = db.one("SELECT MIN(id) id FROM channels")
    return int(r["id"]) if r and r["id"] is not None else 1


def _cid(channel_id: int | None) -> int:
    return int(channel_id) if channel_id is not None else default_channel_id()


def token_key(channel_id: int | None = None) -> str:
    return f"youtube_token:{_cid(channel_id)}"


# database column -> public key (the YouTube channel; "profile_id" is the AutoTube channel profile)
_PUBLIC = {"yt_channel_id": "channel_id", "yt_channel_title": "channel_title", "yt_channel_thumbnail": "channel_thumbnail"}
_COLUMN = {v: k for k, v in _PUBLIC.items()}


def connection(channel_id: int | None = None) -> dict:
    """Connection of one profile: status, channel_id/channel_title/channel_thumbnail (the YouTube channel), ..."""
    cid = _cid(channel_id)
    row = db.one("SELECT * FROM youtube_accounts WHERE channel_id=?", (cid,)) or {"status": "disconnected"}
    row.pop("channel_id", None)
    out = {_PUBLIC.get(k, k): v for k, v in row.items()}
    out["profile_id"] = cid
    return out


def connections() -> list[dict]:
    """Every channel profile with its YouTube account (connected or not)."""
    out = []
    for ch in db.query("SELECT id, name, niche FROM channels ORDER BY id"):
        c = connection(ch["id"])
        c.update(profile_name=ch["name"], profile_niche=ch["niche"], can_manage_videos=can_manage_videos(ch["id"]),
                 connected=is_connected(ch["id"]), signin_unfinished=signin_unfinished(ch["id"]))
        out.append(c)
    return out


def signin_unfinished(channel_id: int) -> str | None:
    """When a Google sign-in for this profile was started (>1 min ago, within a day) but Google never sent the
    user back. That happens when Google stops the sign-in on its own page (app in Testing mode and the account
    is not a test user, the 'unverified app' warning was closed, ...). The successful callback clears it."""
    r = db.one("SELECT MAX(created_at) t FROM oauth_states WHERE channel_id=? AND created_at < datetime('now','-1 minute') "
               "AND created_at > datetime('now','-1 day')", (channel_id,))
    return r["t"] if r and r["t"] else None


def connected_profiles() -> list[int]:
    return [c["profile_id"] for c in connections() if c["connected"]]


def overall_status() -> str:
    """For the sidebar: reauth_required if any linked account needs it, else connected if any is connected."""
    st = [c["status"] for c in connections()]
    if "reauth_required" in st:
        return "reauth_required"
    return "connected" if "connected" in st else "disconnected"


def _set_connection(profile_id: int | None = None, /, **fields: Any) -> None:
    """Update one profile's account row; fields use the public names (channel_id = the YouTube channel)."""
    cid = _cid(profile_id)
    db.execute("INSERT OR IGNORE INTO youtube_accounts(channel_id, status) VALUES (?, 'disconnected')", (cid,))
    cols = ", ".join(f"{_COLUMN.get(k, k)}=?" for k in fields)
    db.execute(f"UPDATE youtube_accounts SET {cols} WHERE channel_id=?", (*fields.values(), cid))


# ------------------------------------------------------------------ authorization flow
def authorization_url(channel_id: int | None = None, force_consent: bool = True) -> str:
    if channel_id is not None and not db.one("SELECT id FROM channels WHERE id=?", (channel_id,)):
        raise OAuthError("That channel profile does not exist.")
    cid = _cid(channel_id)
    flow = _flow()
    verifier = pysecrets.token_urlsafe(64)
    flow.code_verifier = verifier
    # "select_account consent": Google always asks which account / brand channel to link
    url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true",
                                        prompt="select_account consent" if force_consent else "select_account")
    db.execute("DELETE FROM oauth_states WHERE created_at < datetime('now', ?)", (f"-{STATE_TTL_MIN} minutes",))
    db.execute("INSERT INTO oauth_states(state, verifier, channel_id) VALUES (?,?,?)", (state, _enc(verifier), cid))
    return url


def _enc(s: str) -> str:
    import base64
    return base64.b64encode(secure._protect(s.encode())).decode()


def _dec(s: str) -> str:
    import base64
    return secure._unprotect(base64.b64decode(s)).decode()


def _exchange_code(flow: Flow, authorization_response: str) -> Credentials:
    """Network call to Google's token endpoint (mocked in tests)."""
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials


def _fetch_channel(creds: Credentials) -> dict | None:
    """channels.list(mine=true) - mocked in tests."""
    from googleapiclient.discovery import build
    from .. import quota
    res = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute()
    quota.record("youtube", "channels.list", config.YOUTUBE_COST_LIST)
    items = res.get("items", [])
    if not items:
        return None
    c = items[0]
    return {"id": c["id"], "title": c["snippet"]["title"],
            "thumbnail": c["snippet"]["thumbnails"].get("default", {}).get("url")}


def handle_callback(full_url: str, state: str) -> dict:
    row = db.one("SELECT verifier, channel_id, created_at FROM oauth_states WHERE state=? AND created_at >= datetime('now', ?)",
                 (state, f"-{STATE_TTL_MIN} minutes"))
    db.execute("DELETE FROM oauth_states WHERE state=?", (state,))      # single use
    if not row:
        raise OAuthError("Invalid or expired authorization state. Please click Connect YouTube again.")
    cid = _cid(row.get("channel_id"))
    flow = _flow(state)
    flow.code_verifier = _dec(row["verifier"])
    creds = _exchange_code(flow, full_url.replace("http://127.0.0.1", "http://localhost"))
    granted = set(creds.scopes or SCOPES)
    if SCOPES[0] not in granted and MANAGE_SCOPE not in granted:
        raise OAuthError("YouTube upload permission was not granted. Reconnect and tick all requested permissions.")
    ch = _fetch_channel(creds)
    if ch:
        other = db.one("SELECT a.channel_id, c.name FROM youtube_accounts a JOIN channels c ON c.id=a.channel_id "
                       "WHERE a.yt_channel_id=? AND a.channel_id!=?", (ch["id"], cid))
        if other:
            raise OAuthError(f"The YouTube channel '{ch['title']}' is already linked to the profile '{other['name']}'. "
                             "Each YouTube channel can be linked to one profile only (prevents double uploads). "
                             "Sign in with a different Google account or pick a different brand channel.")
    if MANAGE_SCOPE not in granted:
        logs.notify("WARNING", "Limited YouTube permission",
                    "'Manage your YouTube videos' was not granted, so Publish Now / privacy changes are unavailable. "
                    "Reconnect and tick every permission to enable them.")
    same_account = bool(ch) and connection(cid).get("channel_id") == ch["id"]
    store(creds, cid, keep_refresh=same_account)
    if not _stored(cid).get("refresh_token"):
        raise OAuthError("Google did not return a refresh token. Click Reconnect (it forces the consent screen).")
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    _set_connection(cid, status="connected", channel_id=ch and ch["id"], channel_title=ch and ch["title"],
                    channel_thumbnail=ch and ch["thumbnail"], scopes=" ".join(sorted(granted)), connected_at=now,
                    last_refresh_at=now, token_expiry=creds.expiry.isoformat() if creds.expiry else None, last_error=None)
    db.execute("DELETE FROM oauth_states WHERE channel_id=?", (cid,))     # earlier abandoned sign-ins are moot now
    if ch:
        db.execute("UPDATE channels SET youtube_channel_id=?, youtube_title=? WHERE id=?", (ch["id"], ch["title"], cid))
    prof = db.one("SELECT name FROM channels WHERE id=?", (cid,))
    logs.notify("INFO", "YouTube connected",
                f"Profile '{prof['name'] if prof else cid}' -> channel: {ch['title'] if ch else 'no channel on this account'}")
    return connection(cid)


# ------------------------------------------------------------------ token storage & refresh
def _stored(channel_id: int | None = None) -> dict:
    cid = _cid(channel_id)
    info = secure.get(token_key(cid))
    if info is None and cid == default_channel_id():
        legacy = secure.get(LEGACY_TOKEN_KEY)          # one-time move from the single-account version
        if legacy:
            secure.put(token_key(cid), legacy)
            secure.delete(LEGACY_TOKEN_KEY)
            info = legacy
    return info or {}


def store(creds: Credentials, channel_id: int | None = None, keep_refresh: bool = True) -> None:
    """Persist encrypted; keep the existing refresh token if Google didn't send a new one (same account only)."""
    info = json.loads(creds.to_json())
    old = _stored(channel_id)
    if keep_refresh and not info.get("refresh_token") and old.get("refresh_token"):
        info["refresh_token"] = old["refresh_token"]
    secure.put(token_key(channel_id), info)


def _refresh(creds: Credentials) -> None:
    """Network call to refresh the access token (mocked in tests)."""
    creds.refresh(Request())


def load(channel_id: int | None = None) -> Credentials | None:
    """Valid credentials of one profile's account, refreshing automatically. None if not connected / re-auth needed."""
    cid = _cid(channel_id)
    info = _stored(cid)
    if not info.get("refresh_token") and not info.get("token"):
        return None
    try:
        # use the scopes actually GRANTED; asking the token endpoint for ungranted scopes would fail the refresh
        creds = Credentials.from_authorized_user_info(info, info.get("scopes") or None)
    except Exception as e:
        logs.error("oauth", f"stored token unreadable (profile {cid}): {type(e).__name__}")
        return None
    needs = not creds.valid or (creds.expiry and creds.expiry - dt.datetime.utcnow() < dt.timedelta(minutes=5))
    if needs:
        if not creds.refresh_token:
            _set_connection(cid, status="reauth_required", last_error="no refresh token stored")
            return None
        try:
            _refresh(creds)
        except RefreshError as e:
            msg = str(e)
            reason = "invalid_grant" if "invalid_grant" in msg else type(e).__name__
            _set_connection(cid, status="reauth_required", last_error=f"refresh failed: {reason}")
            title = connection(cid).get("channel_title") or f"profile {cid}"
            logs.notify("ERROR", f"YouTube needs to be reconnected: {title}",
                        "Google rejected the saved authorization (revoked, password change, or 7-day expiry of an app "
                        "in Testing mode). Click Reconnect on the YouTube page.")
            return None
        except Exception as e:     # network: keep status, try later
            _set_connection(cid, last_error=f"refresh temporarily failed: {type(e).__name__}")
            logs.warn("oauth", f"token refresh failed temporarily (profile {cid}): {type(e).__name__}")
            return None
        store(creds, cid)
        _set_connection(cid, status="connected", last_refresh_at=dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        token_expiry=creds.expiry.isoformat() if creds.expiry else None, last_error=None)
    return creds


def health_check() -> list[dict]:
    """Scheduled hourly: keeps every linked account's token fresh and its status accurate."""
    out = []
    for ch in db.query("SELECT id FROM channels ORDER BY id"):
        if _stored(ch["id"]):
            load(ch["id"])
        out.append(connection(ch["id"]))
    return out


def disconnect(channel_id: int | None = None) -> None:
    cid = _cid(channel_id)
    info = _stored(cid)
    tok = info.get("refresh_token") or info.get("token")
    if tok:
        try:
            import requests
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": tok}, timeout=15)
        except Exception:
            pass
    secure.delete(token_key(cid))
    _set_connection(cid, status="disconnected", channel_id=None, channel_title=None, channel_thumbnail=None, scopes=None,
                    last_error=None, token_expiry=None)
    db.execute("UPDATE channels SET youtube_channel_id=NULL, youtube_title=NULL WHERE id=?", (cid,))
    logs.info("oauth", f"YouTube disconnected from profile {cid} and authorization revoked")


def is_connected(channel_id: int | None = None) -> bool:
    return bool(_stored(channel_id).get("refresh_token")) and connection(channel_id).get("status") == "connected"


def any_connected() -> bool:
    return bool(connected_profiles())
