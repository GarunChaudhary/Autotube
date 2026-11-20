"""Story genres (content types) for faster audience matching and SEO.

Every video stays grounded in real sources (Wikipedia research + fact-check), so a genre changes HOW a true
story is told and packaged - angle, tone, pacing, voice, music, title style, tags - never the facts.
"Horror" means true creepy stories, "Comedy" means absurd/funny true stories, and so on.
"""
from __future__ import annotations

from typing import Any

GENRES: dict[str, dict[str, Any]] = {
    "horror": {
        "label": "Horror", "emoji": "👻",
        "pitch": "True creepy stories: haunted places, eerie disappearances, dark legends",
        "topic_hint": "real creepy, eerie or disturbing events, haunted places, unexplained disappearances, "
                      "dark folklore with documented history",
        "style": "Dark, slow-burn suspense. Build dread with sensory detail (silence, cold, darkness). Short, chilling "
                 "sentences. Withhold the scariest true detail until late. Never invent supernatural claims - "
                 "present legends as legends and say what is actually documented.",
        "tone": "ominous, hushed, suspenseful",
        "voice": "am_onyx", "mood": "mysterious",
        "title_style": "dread and unanswered questions (e.g. 'Nobody Came Back From This Village')",
        "tags": ["true horror stories", "scary stories", "creepy facts", "horror shorts", "unexplained",
                 "haunted places", "scary true stories", "dark history"],
        "hashtags": ["horror", "scary", "creepy"],
    },
    "comedy": {
        "label": "Comedy", "emoji": "😂",
        "pitch": "Absurd, ridiculous and hilarious true stories",
        "topic_hint": "absurd, ridiculous or hilarious real events, weird laws, bizarre historical blunders, "
                      "funny animal behaviour, ironic accidents",
        "style": "Playful and witty. Deadpan delivery, comic timing (setup, beat, punchline), ironic understatement "
                 "and callbacks. Highlight the absurdity of real facts - jokes come from the true details, never "
                 "from mocking real victims or inventing events.",
        "tone": "witty, playful, deadpan",
        "voice": "am_puck", "mood": "uplifting",
        "title_style": "absurd contradiction (e.g. 'This War Was Lost to a Bucket')",
        "tags": ["funny history", "weird facts", "funny stories", "absurd history", "comedy shorts",
                 "funny facts", "you won't believe", "hilarious true stories"],
        "hashtags": ["funny", "comedy", "weirdfacts"],
    },
    "mystery": {
        "label": "Mystery", "emoji": "🕵️",
        "pitch": "Unsolved mysteries, strange signals, lost treasures",
        "topic_hint": "unsolved mysteries, unexplained phenomena, undeciphered texts, lost treasures, strange signals",
        "style": "Investigative. Present clues one at a time, raise competing theories, keep the viewer guessing. "
                 "Clearly separate what is known from what is speculated.",
        "tone": "curious, intriguing, investigative",
        "voice": "bm_george", "mood": "mysterious",
        "title_style": "an unsolved question (e.g. 'Why Can Nobody Read This Book?')",
        "tags": ["unsolved mysteries", "mystery", "unexplained", "mysteries of the world", "strange facts",
                 "conspiracy explained", "unsolved"],
        "hashtags": ["mystery", "unsolved", "unexplained"],
    },
    "true_crime": {
        "label": "True crime", "emoji": "🔍",
        "pitch": "Famous heists, cons, escapes and cold cases",
        "topic_hint": "famous heists, con artists, prison escapes, art thefts, historical cold cases (no recent "
                      "cases involving private individuals)",
        "style": "Documentary true-crime. Tight chronology, the plan, the mistake, the investigation, the twist. "
                 "Respectful to victims, no gore, no glorifying criminals, only documented facts.",
        "tone": "gripping, serious, documentary",
        "voice": "am_michael", "mood": "mysterious",
        "title_style": "the audacious act (e.g. 'He Stole the Mona Lisa With a Coat')",
        "tags": ["true crime", "heist", "true crime stories", "con artist", "famous heists", "crime documentary",
                 "cold case"],
        "hashtags": ["truecrime", "heist", "crime"],
    },
    "history": {
        "label": "History", "emoji": "🏛️",
        "pitch": "Forgotten moments that changed the world",
        "topic_hint": "surprising, little-known historical events, forgotten turning points, strange historical figures",
        "style": "Cinematic history storytelling. Drop into a vivid moment, then zoom out to why it mattered. "
                 "Concrete names, dates and places.",
        "tone": "cinematic, authoritative, vivid",
        "voice": "bm_george", "mood": "epic",
        "title_style": "a forgotten turning point (e.g. 'The Day 20,000 Soldiers Vanished')",
        "tags": ["history", "history facts", "forgotten history", "history shorts", "untold history",
                 "history documentary", "world history"],
        "hashtags": ["history", "historyfacts", "didyouknow"],
    },
    "science": {
        "label": "Science & facts", "emoji": "🧪",
        "pitch": "Mind-blowing science and 'wait, really?' facts",
        "topic_hint": "mind-blowing scientific phenomena, strange experiments, counter-intuitive physics, "
                      "biology or chemistry facts",
        "style": "Explainer with wonder. Start with the counter-intuitive fact, explain the 'why' simply with an "
                 "everyday analogy, end with an even bigger implication.",
        "tone": "energetic, curious, clear",
        "voice": "am_michael", "mood": "uplifting",
        "title_style": "a counter-intuitive fact (e.g. 'Glass Is Not What You Think')",
        "tags": ["science", "science facts", "mind blowing facts", "amazing facts", "science explained",
                 "fun facts", "did you know"],
        "hashtags": ["science", "facts", "didyouknow"],
    },
    "space": {
        "label": "Space", "emoji": "🚀",
        "pitch": "Cosmic scale, strange planets, space missions",
        "topic_hint": "astronomy, strange planets and stars, space missions, cosmic phenomena, space disasters",
        "style": "Awe and scale. Make huge numbers feel physical with comparisons. Calm wonder that builds to an epic "
                 "reveal.",
        "tone": "awe-struck, epic, calm",
        "voice": "bm_george", "mood": "epic",
        "title_style": "cosmic scale or danger (e.g. 'This Planet Rains Glass Sideways')",
        "tags": ["space", "space facts", "universe", "astronomy", "nasa", "space documentary", "cosmos"],
        "hashtags": ["space", "universe", "astronomy"],
    },
    "nature": {
        "label": "Animals & nature", "emoji": "🐾",
        "pitch": "Bizarre animals and extreme places on Earth",
        "topic_hint": "bizarre animals, extreme survival abilities, strange natural places and phenomena",
        "style": "Nature-documentary wonder. Vivid visual descriptions, surprising abilities, a 'nature is wild' payoff.",
        "tone": "warm, amazed, vivid",
        "voice": "bm_fable", "mood": "calm",
        "title_style": "an impossible ability (e.g. 'This Animal Can Survive in Space')",
        "tags": ["animals", "animal facts", "nature", "wildlife", "weird animals", "nature documentary",
                 "amazing animals"],
        "hashtags": ["animals", "nature", "wildlife"],
    },
    "motivation": {
        "label": "Motivation", "emoji": "🔥",
        "pitch": "Real comeback and underdog stories",
        "topic_hint": "real underdog and comeback stories, famous failures before success, extraordinary perseverance",
        "style": "Inspirational. The low point, the struggle, the turning point, the triumph, one memorable lesson. "
                 "Earnest, never preachy, every claim documented.",
        "tone": "inspiring, emotional, powerful",
        "voice": "am_fenrir", "mood": "uplifting",
        "title_style": "from failure to triumph (e.g. 'Rejected 1,009 Times. Then This.')",
        "tags": ["motivation", "inspiring stories", "success story", "motivational", "never give up",
                 "comeback story", "self improvement"],
        "hashtags": ["motivation", "inspiration", "success"],
    },
    "tech": {
        "label": "Tech & inventions", "emoji": "💡",
        "pitch": "Accidental inventions and tech that changed everything",
        "topic_hint": "accidental inventions, technology history, engineering marvels, famous tech failures",
        "style": "Smart and fast. The problem, the unlikely inventor, the accident or breakthrough, the world it "
                 "changed.",
        "tone": "smart, upbeat, fast",
        "voice": "am_eric", "mood": "uplifting",
        "title_style": "an accidental breakthrough (e.g. 'A Melted Chocolate Bar Invented This')",
        "tags": ["technology", "inventions", "tech history", "engineering", "how it was invented", "innovation",
                 "tech facts"],
        "hashtags": ["tech", "inventions", "engineering"],
    },
}


def get(key: str | None) -> dict[str, Any] | None:
    return GENRES.get(key or "")


def public_list() -> list[dict[str, Any]]:
    return [{"key": k, "label": g["label"], "emoji": g["emoji"], "pitch": g["pitch"]} for k, g in GENRES.items()]


def channel_with_genre(channel: dict, key: str | None) -> dict:
    """Channel dict with the genre's tone, so writer prompts pick up the style without other changes."""
    g = get(key)
    if not g:
        return channel
    return {**channel, "tone": g["tone"], "genre": key}


def style_block(key: str | None) -> str:
    g = get(key)
    if not g:
        return ""
    return (f"\nGENRE: {g['label']} - {g['pitch']}.\nGENRE STYLE: {g['style']}\n"
            "The genre changes only the storytelling style; every fact must still come from the SOURCE.\n")
