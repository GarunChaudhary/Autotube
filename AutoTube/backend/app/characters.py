"""AI characters: original AI-made people (podcast hosts, dancers, storytellers...) grouped by category.

This module stores and validates the cast. Making videos with them (character images, lip-sync, dance motion)
needs a GPU, which is not connected yet - `engine_status()` reports that honestly instead of faking output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config, db, logs

STYLES = {"realistic": "Realistic human", "anime": "Anime", "3d_cartoon": "3D cartoon"}
GENDERS = ("female", "male", "non-binary")
AGE_RANGES = ("18-25", "20-30", "30-40", "40-55", "55+")
STATUSES = ("design", "approved")

# Kokoro-82M English voices installed with AutoTube (Apache-2.0, free for commercial use).
VOICES = {
    "af_heart": "Heart - warm US female", "af_bella": "Bella - bright US female", "af_nicole": "Nicole - soft US female",
    "af_sarah": "Sarah - clear US female", "af_sky": "Sky - young US female", "af_nova": "Nova - calm US female",
    "af_jessica": "Jessica - US female", "af_river": "River - US female", "af_alloy": "Alloy - US female",
    "af_aoede": "Aoede - US female", "af_kore": "Kore - US female",
    "am_michael": "Michael - deep US male", "am_adam": "Adam - US male", "am_eric": "Eric - US male",
    "am_fenrir": "Fenrir - strong US male", "am_liam": "Liam - young US male", "am_onyx": "Onyx - low US male",
    "am_puck": "Puck - lively US male", "am_echo": "Echo - US male",
    "bf_emma": "Emma - UK female", "bf_isabella": "Isabella - UK female", "bf_alice": "Alice - UK female",
    "bf_lily": "Lily - UK female",
    "bm_george": "George - UK male", "bm_fable": "Fable - UK male", "bm_lewis": "Lewis - UK male",
    "bm_daniel": "Daniel - UK male",
}


def voice_gender(voice: str) -> str:
    """Kokoro voice ids: 2nd letter f = female, m = male (af_ / bf_ / am_ / bm_)."""
    return "female" if voice[1:2] == "f" else "male"


def default_voice(gender: str, taken: set[str] = frozenset()) -> str:
    """First voice matching the gender that no other character uses yet (so every character sounds different)."""
    pool = [v for v in VOICES if gender == "non-binary" or voice_gender(v) == gender]
    return next((v for v in pool if v not in taken), pool[0])


PREVIEW_TEXT ="Hi, I'm {name}. Welcome back to the channel. Today we have a great story for you."


class CharacterError(ValueError):
    pass


# ------------------------------------------------------------------ categories
def list_categories() -> list[dict]:
    return db.query("SELECT c.*, (SELECT COUNT(*) FROM ai_characters a WHERE a.category_id=c.id) AS characters "
                    "FROM character_categories c ORDER BY c.sort, c.name")


def _clean_category(f: dict) -> dict:
    name = (f.get("name") or "").strip()
    if not 2 <= len(name) <= 40:
        raise CharacterError("category name must be 2-40 characters")
    return {"name": name, "icon": (f.get("icon") or "").strip()[:8], "description": (f.get("description") or "").strip()[:300]}


def create_category(**f: Any) -> dict:
    c = _clean_category(f)
    if db.one("SELECT id FROM character_categories WHERE name=? COLLATE NOCASE", (c["name"],)):
        raise CharacterError(f"a category named '{c['name']}' already exists")
    cid = db.execute("INSERT INTO character_categories(name, icon, description, builtin, sort) VALUES (?,?,?,0,500)",
                     (c["name"], c["icon"], c["description"]))
    logs.info("characters", f"category '{c['name']}' created")
    return db.one("SELECT * FROM character_categories WHERE id=?", (cid,))


def update_category(cat_id: int, **f: Any) -> dict:
    cur = db.one("SELECT * FROM character_categories WHERE id=?", (cat_id,))
    if not cur:
        raise CharacterError("category not found")
    c = _clean_category({**cur, **{k: v for k, v in f.items() if v is not None}})
    if db.one("SELECT id FROM character_categories WHERE name=? COLLATE NOCASE AND id!=?", (c["name"], cat_id)):
        raise CharacterError(f"a category named '{c['name']}' already exists")
    db.execute("UPDATE character_categories SET name=?, icon=?, description=? WHERE id=?",
               (c["name"], c["icon"], c["description"], cat_id))
    return db.one("SELECT * FROM character_categories WHERE id=?", (cat_id,))


def delete_category(cat_id: int) -> None:
    """Characters in the category are kept (moved to 'no category')."""
    cur = db.one("SELECT * FROM character_categories WHERE id=?", (cat_id,))
    if not cur:
        raise CharacterError("category not found")
    db.execute("UPDATE ai_characters SET category_id=NULL WHERE category_id=?", (cat_id,))
    db.execute("DELETE FROM character_categories WHERE id=?", (cat_id,))
    logs.info("characters", f"category '{cur['name']}' deleted")


# ------------------------------------------------------------------ characters
def list_characters() -> list[dict]:
    return db.query("SELECT a.*, c.name AS category_name, c.icon AS category_icon FROM ai_characters a "
                    "LEFT JOIN character_categories c ON c.id=a.category_id ORDER BY a.id DESC")


def get_character(char_id: int) -> dict | None:
    return db.one("SELECT a.*, c.name AS category_name, c.icon AS category_icon FROM ai_characters a "
                  "LEFT JOIN character_categories c ON c.id=a.category_id WHERE a.id=?", (char_id,))


def _clean_character(f: dict) -> dict:
    name = (f.get("name") or "").strip()
    if not 2 <= len(name) <= 40:
        raise CharacterError("name must be 2-40 characters")
    if f.get("style") not in STYLES:
        raise CharacterError(f"style must be one of {list(STYLES)}")
    if f.get("gender") not in GENDERS:
        raise CharacterError(f"gender must be one of {list(GENDERS)}")
    if f.get("age_range") not in AGE_RANGES:
        raise CharacterError(f"age range must be one of {list(AGE_RANGES)}")
    if f.get("voice") not in VOICES:
        raise CharacterError("choose one of the installed voices")
    if f["gender"] != "non-binary" and voice_gender(f["voice"]) != f["gender"]:
        raise CharacterError(f"{(f.get('name') or 'this character').strip()} is {f['gender']} but the voice "
                             f"'{VOICES[f['voice']].split(' - ')[0]}' is {voice_gender(f['voice'])} - choose a "
                             f"{f['gender']} voice")
    if f.get("status") not in STATUSES:
        raise CharacterError(f"status must be one of {list(STATUSES)}")
    cat = f.get("category_id")
    if cat is not None and not db.one("SELECT id FROM character_categories WHERE id=?", (cat,)):
        raise CharacterError("category not found")
    ch = f.get("channel_id")
    if ch is not None and not db.one("SELECT id FROM channels WHERE id=?", (ch,)):
        raise CharacterError("channel profile not found")
    if not f.get("original_confirmed"):
        raise CharacterError("confirm the character is original and does not look like a real person "
                             "(celebrity look-alikes are deepfakes and get removed by YouTube)")
    appearance = (f.get("appearance") or "").strip()
    if len(appearance) < 10:
        raise CharacterError("describe how the character looks (at least a few words)")
    return {"name": name, "category_id": cat, "channel_id": ch, "gender": f["gender"], "age_range": f["age_range"],
            "style": f["style"], "appearance": appearance[:600], "personality": (f.get("personality") or "").strip()[:400],
            "voice": f["voice"], "original_confirmed": 1, "enabled": int(bool(f.get("enabled", True))),
            "status": f["status"]}


DEFAULTS = {"gender": "female", "age_range": "20-30", "style": "realistic", "status": "design",
            "enabled": True, "category_id": None, "channel_id": None}


def create_character(**f: Any) -> dict:
    f = {**DEFAULTS, **{k: v for k, v in f.items() if v is not None}}
    if not f.get("voice"):              # no voice chosen: a free one that matches the gender
        f["voice"] = default_voice(f["gender"], {r["voice"] for r in db.query("SELECT voice FROM ai_characters")})
    c = _clean_character(f)
    if db.one("SELECT id FROM ai_characters WHERE name=? COLLATE NOCASE", (c["name"],)):
        raise CharacterError(f"you already have a character named '{c['name']}'")
    cols = list(c)
    cid = db.execute(f"INSERT INTO ai_characters({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(c[k] for k in cols))
    logs.info("characters", f"character '{c['name']}' created")
    return get_character(cid)


def update_character(char_id: int, fields: dict) -> dict:
    cur = db.one("SELECT * FROM ai_characters WHERE id=?", (char_id,))
    if not cur:
        raise CharacterError("character not found")
    c = _clean_character({**cur, **fields})
    if db.one("SELECT id FROM ai_characters WHERE name=? COLLATE NOCASE AND id!=?", (c["name"], char_id)):
        raise CharacterError(f"you already have a character named '{c['name']}'")
    db.execute(f"UPDATE ai_characters SET {', '.join(f'{k}=?' for k in c)}, updated_at=datetime('now') WHERE id=?",
               (*c.values(), char_id))
    return get_character(char_id)


def delete_character(char_id: int) -> None:
    if not db.one("SELECT id FROM ai_characters WHERE id=?", (char_id,)):
        raise CharacterError("character not found")
    db.execute("DELETE FROM ai_characters WHERE id=?", (char_id,))


# ------------------------------------------------------------------ making videos with characters
# mode -> (label, characters needed, works on this PC without a GPU)
MODES = {
    "narrator": ("Narrator", 1, True),
    "podcast": ("Podcast (2 voices)", 2, True),
    "talking_face": ("Talking face (lip-sync)", 1, False),
    "dance": ("Dance", 1, False),
}


def validate_cast(mode: str, ids: list[int]) -> list[dict]:
    """The characters for a video, in speaking order. Raises CharacterError when they can't be used."""
    if mode not in MODES:
        raise CharacterError(f"mode must be one of {list(MODES)}")
    label, need, cpu_ok = MODES[mode]
    if not cpu_ok:
        raise CharacterError(f"'{label}' needs a GPU, which is not connected yet")
    if len(ids) != need or len(set(ids)) != need:
        raise CharacterError(f"'{label}' needs exactly {need} different character{'s' if need > 1 else ''}")
    cast = []
    for i in ids:
        c = db.one("SELECT * FROM ai_characters WHERE id=?", (i,))
        if not c:
            raise CharacterError(f"character #{i} not found")
        if not c["enabled"]:
            raise CharacterError(f"'{c['name']}' is disabled")
        cast.append(c)
    if mode == "podcast" and cast[0]["voice"] == cast[1]["voice"]:
        raise CharacterError(f"'{cast[0]['name']}' and '{cast[1]['name']}' use the same voice - pick different voices "
                             "so viewers can tell them apart")
    return cast


def video_cast(v: dict) -> list[dict]:
    """Characters saved on a video (missing / deleted ones are skipped)."""
    import json
    try:
        ids = json.loads(v.get("character_ids") or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for i in ids:
        c = db.one("SELECT * FROM ai_characters WHERE id=?", (i,))
        if c:
            out.append(c)
    return out


def _who(c: dict) -> str:
    extra = f"; personality: {c['personality']}" if c.get("personality") else ""
    return f"{c['name']} ({c['gender']}, {c['age_range']}{extra})"


def persona_text(mode: str, cast: list[dict]) -> str:
    """Instruction added to the script writer's TONE line. Facts still come only from the sources."""
    if mode == "podcast" and len(cast) == 2:
        a, b = cast
        return (f"\nFORMAT: a two-host podcast conversation between {_who(a)} and {_who(b)}. Beats ALTERNATE speakers: "
                f"beat 1 is said by {a['name']}, beat 2 by {b['name']}, beat 3 by {a['name']}, and so on. Write every beat "
                f"in first person as that host speaking naturally to the other (react, ask, add the next fact). Use each "
                f"name at most once or twice in the whole script. Do NOT write speaker labels like '{a['name']}:' - only "
                "the spoken words. Every fact must still come from the SOURCE.")
    if cast:
        c = cast[0]
        return (f"\nNARRATOR: the script is spoken by {_who(c)}, the channel's host. Write in their voice and "
                "personality, in first person where natural. Do not invent facts about the host.")
    return ""


def segment_voices(mode: str, cast: list[dict], n_segments: int) -> list[str]:
    if not cast:
        return []
    if mode == "podcast" and len(cast) == 2:
        return [cast[i % 2]["voice"] for i in range(n_segments)]
    return [cast[0]["voice"]] * n_segments


# ------------------------------------------------------------------ voices & engine
def voice_preview(voice: str, name: str = "") -> Path:
    """Short WAV sample of a voice (cached). Uses the local Kokoro engine - no GPU needed."""
    import wave
    from .media import tts
    if voice not in VOICES:
        raise CharacterError("unknown voice")
    import re
    tag = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower())[:30].strip("_")
    out = config.CACHE_DIR / "voice_previews" / f"{voice}{'-' + tag if tag else ''}.wav"   # sample says this name
    if out.exists() and out.stat().st_size > 1000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = tts._kokoro_pcm(PREVIEW_TEXT.format(name=(name or VOICES[voice].split(" ")[0])[:30]), voice, 1.0)
    tmp = out.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.RATE)
        w.writeframes(pcm)
    tmp.replace(out)
    return out


def engine_status() -> dict:
    """What can be made with characters right now. Generation needs a rented GPU, which is not connected."""
    gpu_key = bool(db.get_setting("gpu_provider_key_set"))
    return {
        "gpu_connected": gpu_key,
        "can_design": True,
        "can_preview_voice": (config.VOICES_DIR / "kokoro" / "kokoro-v1.0.onnx").exists(),
        "can_generate_video": False,
        "message": ("Character images, lip-synced podcasts and dance videos need a GPU. Connect a Vast.ai or RunPod "
                    "account (about $10 credit) and this switches on. Until then you can design your cast, pick "
                    "voices and organise categories - nothing fake is generated."),
    }
