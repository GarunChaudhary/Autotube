"""Secret storage encrypted at rest.

Windows: DPAPI (CryptProtectData, current-user scope) - only this Windows user on this PC can decrypt.
Elsewhere: Fernet with a key held in the OS keyring.
Encrypted blobs live in the `secure_store` table. Values are never logged.
"""
from __future__ import annotations

import base64
import json
import sys
from typing import Any

from . import db

_ENTROPY = b"AutoTubeAI/secure-store/v1"


def _protect(data: bytes) -> bytes:
    if sys.platform == "win32" and not key_file().exists():
        import win32crypt
        return b"dpapi:" + win32crypt.CryptProtectData(data, "AutoTubeAI", _ENTROPY, None, None, 0)
    from cryptography.fernet import Fernet
    return b"fernet:" + Fernet(_fernet_key()).encrypt(data)


def _unprotect(blob: bytes) -> bytes:
    if blob.startswith(b"dpapi:"):
        import win32crypt
        return win32crypt.CryptUnprotectData(blob[6:], _ENTROPY, None, None, 0)[1]
    if blob.startswith(b"fernet:"):
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).decrypt(blob[7:])
    raise ValueError("unknown secret format")


def key_file():
    from . import config
    return config.SECRETS_DIR / "secure.key"


def _fernet_key() -> bytes:
    """Linux server: key in data/secrets/secure.key (mode 0600, readable only by the AutoTube user) - headless
    servers have no desktop keyring. Other desktops: OS keyring."""
    import os
    from cryptography.fernet import Fernet
    f = key_file()
    if f.exists():
        return f.read_text().strip().encode()
    if sys.platform.startswith("linux"):
        k = Fernet.generate_key().decode()
        f.write_text(k)
        os.chmod(f, 0o600)
        return k.encode()
    import keyring
    k = keyring.get_password("AutoTubeAI", "secure-store")
    if not k:
        k = Fernet.generate_key().decode()
        keyring.set_password("AutoTubeAI", "secure-store", k)
    return k.encode()


def put(key: str, value: Any) -> None:
    blob = _protect(json.dumps(value).encode())
    db.execute("INSERT INTO secure_store(key, value, updated_at) VALUES (?,?,datetime('now')) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
               (key, base64.b64encode(blob).decode()))


def get(key: str, default: Any = None) -> Any:
    row = db.one("SELECT value FROM secure_store WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(_unprotect(base64.b64decode(row["value"])))
    except Exception:
        return default


def delete(key: str) -> None:
    db.execute("DELETE FROM secure_store WHERE key=?", (key,))


def exists(key: str) -> bool:
    return db.one("SELECT 1 AS x FROM secure_store WHERE key=?", (key,)) is not None


def raw_blob(key: str) -> str | None:
    """For tests: the stored (encrypted) representation."""
    row = db.one("SELECT value FROM secure_store WHERE key=?", (key,))
    return row["value"] if row else None
