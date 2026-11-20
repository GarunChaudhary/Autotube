import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir so tests never touch real data. Must be set before importing app.
_TMP = tempfile.mkdtemp(prefix="autotube_test_")
os.environ["AUTOTUBE_DATA_DIR"] = _TMP
os.environ["AUTOTUBE_ENV_FILE"] = os.path.join(_TMP, "test.env")      # never touch the real .env
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["PEXELS_API_KEY"] = ""
os.environ["PIXABAY_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import config, db  # noqa: E402

# Voices are large: reuse the real ones read-only.
_REAL_VOICES = config.ROOT / "data" / "voices"
if _REAL_VOICES.exists():
    config.VOICES_DIR = _REAL_VOICES

db.migrate()
db.set_setting("llm_provider_order", ["template"])        # deterministic, no AI calls in unit tests

from app import remote_auth  # noqa: E402

TEST_USER, TEST_PASS = "tester", "test-pass-123"
remote_auth.set_credentials(TEST_USER, TEST_PASS)       # the dashboard requires a login for every request


def login(client):
    """Log a TestClient in; its cookie jar keeps the session for later requests."""
    r = client.post("/api/login", data={"username": TEST_USER, "password": TEST_PASS, "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


@pytest.fixture
def channel():
    cid = db.execute("INSERT INTO channels(name, niche) VALUES (?,?)", ("Test", "history mysteries"))
    return db.one("SELECT * FROM channels WHERE id=?", (cid,))


FAKE_RESEARCH = {
    "topic": "Voynich manuscript",
    "sources": [{"title": "Voynich manuscript", "url": "https://en.wikipedia.org/wiki/Voynich_manuscript"}],
    "primary_text": "",
    "all_text": (
        "The Voynich manuscript is an illustrated codex hand-written in an unknown script referred to as Voynichese. "
        "The vellum on which it is written has been carbon-dated to the early 15th century. "
        "The manuscript is named after Wilfrid Voynich, a Polish book dealer who purchased it in 1912. "
        "Many professional and amateur cryptographers have studied the manuscript without success. "
        "It has never been demonstrably deciphered, and its purpose remains unknown. "
        "The pages contain drawings of plants, astronomical diagrams and bathing figures. "
        "Since 1969 it has been held in the Beinecke Rare Book and Manuscript Library of Yale University. "
        "Some researchers have suggested the text is a hoax while others believe it encodes a natural language. "
    ),
}
FAKE_RESEARCH["primary_text"] = FAKE_RESEARCH["all_text"]
