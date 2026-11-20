"""Genre topic finder: real, documented stories ranked by real audience demand. Free APIs only, no keys.

1. Candidates come from Wikipedia itself (MediaWiki API): links in curated "List of ..." articles, category
   members and keyword searches defined per genre - so every topic is a real article (never AI-invented).
2. Demand: Wikipedia pageviews for the last 30 days (PageViewInfo, batched 50 titles per request) - a free proxy
   for how many people are actively looking the subject up, which is what makes a video findable.
3. Relevance: the article's intro must contain the genre's keywords (drops generic links like a country name).
4. Fresh: topics already used on the channel are skipped; the pick is varied between the most-viewed candidates.
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from .. import config, logs
from . import research
from .text_utils import jaccard

# (kind, value): links = article links of a list page, cat = category members, search = full-text search
SOURCES: dict[str, list[tuple[str, str]]] = {
    "horror": [("links", "List of reportedly haunted locations"), ("links", "List of urban legends"),
               ("links", "List of cryptids"), ("links", "List of unsolved deaths"),
               ("links", "List of people who disappeared mysteriously (1910–1970)"),
               ("cat", "Ghost ships"), ("cat", "Unexplained disappearances")],
    "comedy": [("links", "List of hoaxes"), ("links", "List of animals awarded human credentials"),
               ("links", "Lists of unusual deaths"), ("links", "List of inventors killed by their own invention"),
               ("links", "List of practical joke topics"), ("cat", "Hoaxes"), ("cat", "Publicity stunts")],
    "mystery": [("links", "List of unexplained sounds"), ("links", "List of missing aircraft"),
                ("links", "List of people who disappeared mysteriously (1910–1970)"),
                ("links", "List of unsolved deaths"), ("links", "List of lost expeditions"),
                ("cat", "Unexplained phenomena"), ("cat", "Undeciphered historical codes and ciphers")],
    "true_crime": [("links", "List of bank robbers and robberies"), ("links", "List of prison escapes"),
                   ("links", "List of con artists"), ("links", "List of unsolved murders (1900–1979)"),
                   ("cat", "Robberies")],
    "history": [("search", 'intitle:"Siege of"'), ("search", 'intitle:"Sinking of"'), ("search", "intitle:mutiny"),
                ("search", 'intitle:"Battle of" famous'), ("search", "intitle:expedition disaster"),
                ("links", "List of lost expeditions"), ("links", "List of maritime disasters in the 20th century")],
    "science": [("search", "intitle:experiment famous"), ("search", "intitle:paradox"),
                ("search", "intitle:effect phenomenon"), ("links", "List of experiments"),
                ("links", "List of scientific misconduct incidents"), ("cat", "Physical phenomena"),
                ("cat", "Unsolved problems in physics")],
    "space": [("links", "List of spaceflight-related accidents and incidents"), ("search", "intitle:Apollo mission"),
              ("search", "intitle:Voyager OR intitle:Pioneer probe"), ("search", "intitle:disaster spaceflight"),
              ("search", "exoplanet extreme discovered"), ("cat", "Meteorite falls"),
              ("cat", "Hypothetical astronomical objects")],
    "nature": [("search", 'intitle:"(dog)"'), ("search", 'intitle:"(gorilla)" OR intitle:"(chimpanzee)"'),
               ("search", 'intitle:"(elephant)" OR intitle:"(whale)"'),
               ("search", 'intitle:"(parrot)" OR intitle:"(horse)"'), ("links", "List of individual dogs"),
               ("cat", "Extremophiles"), ("cat", "Animal intelligence")],
    "motivation": [("links", "List of aviation accidents and incidents with a sole survivor"),
                   ("search", "intitle:rescue survivors trapped"), ("search", "survived alone days wilderness rescued"),
                   ("search", "overcame disability world record"), ("search", "rejected many times later success founder"),
                   ("links", "List of lost expeditions")],
    "tech": [("links", "List of inventors killed by their own invention"), ("cat", "Engineering failures"),
             ("cat", "Technological failures"), ("search", "intitle:disaster engineering design flaw"),
             ("search", "invented by accident discovery"), ("search", "intitle:bug software failure"),
             ("search", "intitle:collapse bridge OR building")],
}

KEYWORDS: dict[str, str] = {
    "horror": r"haunt|ghost|paranormal|legend|curse|spirit|apparition|cryptid|supernatural|eerie|murder|"
              r"disappear|vanish|occult|witch|demon|creature|monster|sighting|folklore",
    "comedy": r"hoax|prank|joke|absurd|humo|satir|bizarre|unusual|stunt|ridicul|parody|comic|fooled|"
              r"accident|eccentric|odd",
    "mystery": r"myster|unexplain|unknown|unsolved|undeciphered|disappear|vanish|anomal|theor|specul|"
               r"never (been )?(found|solved|identified)",
    "true_crime": r"robber|heist|theft|stole|escape|prison|fraud|swindl|con (man|artist)|murder|police|"
                  r"convicted|arrest|crime|criminal",
    "history": r"war|battle|empire|century|king|army|navy|ship|revolution|dynasty|treaty|siege|expedition",
    "science": r"phenomen|physic|scien|experiment|effect|chemi|energy|particle|illusion|biolog|theory",
    "space": r"space|planet|star|orbit|nasa|spacecraft|astronaut|meteor|galax|solar|mission|astronom|comet",
    "nature": r"species|animal|organism|bird|fish|mammal|insect|habitat|surviv|eruption|earthquake|"
              r"volcan|tsunami|wildlife",
    "motivation": r"surviv|overc|champion|record|rescu|despite|against|became|first|founded|success|disab",
    "tech": r"invent|engineer|technolog|device|machine|patent|computer|design|failure|collapse|prototype",
}

# short descriptions of articles that are not a single true story (places, organisations, media works, concepts)
_NOT_A_STORY = re.compile(
    r"\b(country|sovereign state|city|capital|town|village|municipality|county|province|region of|state of|"
    r"district|neighbourhood|neighborhood|island country|river|mountain range|political party|organi[sz]ation|"
    r"separatist|militant group|company|band|album|song|film|television|tv series|video game|novel|book series|"
    r"comic|franchise|genre|website|magazine|newspaper|concept|term|practice|field of|branch of|type of|"
    r"family of|class of|list|president|prime minister|politician|broadcaster|monarch|football club|airliner|"
    r"aircraft family|programming block|religious|denomination|movement|ethnic group|language|university|school|"
    r"software|anthology|series|game|singer|rapper|musician|airline|airport|manufacturer|brand|"
    r"wikimedia|disambiguation)\b", re.I)
# titles that name a single event/story get a demand boost over broad subjects
_STORY_TITLE = re.compile(
    r"^(disappearance|death|deaths|murder|murders|killing|sinking|siege|battle|massacre|assassination|loss|wreck|"
    r"escape|theft|robbery|heist|kidnapping|rescue|trial|crash|explosion|collapse|burning|great)\b|"
    r"\b(incident|disaster|affair|hoax|heist|robbery|mutiny|war|expedition|crisis|scandal|riot|plague|panic|"
    r"mystery|case|murders|explosion|collapse|crash|flood|eruption|tragedy|stampede|outbreak|experiment|escape|"
    r"accident|triangle|legend|curse|haunting|attack|attacks|flight \d+)\b", re.I)
CACHE_TTL = 7 * 86400     # candidate pools change slowly; re-download weekly
MIN_BYTES = 6000          # article must be long enough to script a full story
_SKIP = re.compile(r"^(list|lists|index|outline|timeline|glossary) of |\(disambiguation\)|^\d{4} in |^portal:", re.I)


def _links(title: str, cap: int = 1500) -> list[str]:
    out, cont = [], {}
    while len(out) < cap:
        d = research._wiki({"action": "query", "titles": title, "prop": "links", "plnamespace": 0, "pllimit": 500,
                            "redirects": 1, **cont})
        for p in d.get("query", {}).get("pages", []):
            out += [l["title"] for l in p.get("links", [])]
        if "continue" not in d:
            break
        cont = {"plcontinue": d["continue"]["plcontinue"]}
    return out


def _category(name: str) -> list[str]:
    d = research._wiki({"action": "query", "list": "categorymembers", "cmtitle": f"Category:{name}",
                        "cmlimit": 500, "cmnamespace": 0, "cmtype": "page"})
    return [m["title"] for m in d.get("query", {}).get("categorymembers", [])]


def candidates(genre: str) -> list[str]:
    import hashlib
    sig = hashlib.sha1(json.dumps(SOURCES.get(genre, [])).encode()).hexdigest()[:8]   # sources edited -> refetch
    cache = config.DATA_DIR / "cache" / f"topics_{genre}_{sig}.json"
    try:
        if time.time() - cache.stat().st_mtime < CACHE_TTL:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = _download_candidates(genre)
    if out:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
    return out


def _download_candidates(genre: str) -> list[str]:
    pool: list[str] = []
    for kind, value in SOURCES.get(genre, []):
        try:
            if kind == "links":
                pool += _links(value)
            elif kind == "cat":
                pool += _category(value)
            else:
                pool += research.wiki_search(value, 100)
        except Exception as e:
            logs.warn("topics", f"{genre}: source {kind}:{value} failed: {e}")
    seen, out = set(), []
    for t in pool:
        if t not in seen and not _SKIP.search(t):
            seen.add(t)
            out.append(t)
    return out


def _stats(titles: list[str]) -> dict[str, dict[str, Any]]:
    """30-day pageviews + size + disambiguation flag, 50 titles per request."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(titles), 50):
        d = research._wiki({"action": "query", "titles": "|".join(titles[i:i + 50]), "redirects": 1,
                            "prop": "pageviews|pageprops|info|description", "pvipdays": 30})
        for p in d.get("query", {}).get("pages", []):
            if p.get("missing") or "disambiguation" in (p.get("pageprops") or {}):
                continue
            out[p["title"]] = {"views": sum(v or 0 for v in (p.get("pageviews") or {}).values()),
                               "bytes": p.get("length", 0),
                               "desc": p.get("description") or (p.get("pageprops") or {}).get("wikibase-shortdesc", "")}
    return out


def _intros(titles: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(titles), 20):
        d = research._wiki({"action": "query", "titles": "|".join(titles[i:i + 20]), "prop": "extracts",
                            "exintro": 1, "explaintext": 1, "exlimit": 20, "redirects": 1})
        for p in d.get("query", {}).get("pages", []):
            out[p["title"]] = p.get("extract", "")
    return out


def _first_sentence(text: str) -> str:
    m = re.match(r"(.{20,260}?[.!?])(\s|$)", " ".join(text.split()))
    return m.group(1) if m else text[:200]


def _judge(genre: str, titles: list[str], stats: dict, intros: dict) -> list[str] | None:
    """Local AI as a classifier (not a generator): scores each REAL candidate 0-10 for being one specific,
    gripping true story in the genre and keeps 7+. Returns approved titles, or None if the AI is unavailable."""
    from .. import llm
    from . import genres
    g = genres.get(genre)
    lines = "\n".join(f"{i + 1}. {t} | {stats[t]['desc'] or '-'} | {_first_sentence(intros.get(t, ''))}"
                      for i, t in enumerate(titles))
    prompt = (f"GENRE: {g['label']} - {g['pitch']}.\n\nCANDIDATE WIKIPEDIA ARTICLES (number. title | description | "
              f"first sentence):\n{lines}\n\n"
              "Score EVERY candidate from 0 to 10: would this article make a gripping "
              f"{g['label']} YouTube video telling ONE specific true story?\n"
              "10 = a single specific event, case, legend, mission, disaster, experiment, creature, individual animal "
              "or one person's remarkable story that clearly fits the genre.\n"
              "0-3 = a country, region, city, organisation, rank, broad subject, field of study, a species or breed "
              "in general, an ordinary biography, anything that does not fit the genre, or a GENERAL TYPE / "
              "practice / technique / method / concept (e.g. 'Bait-and-switch', 'Ghost hunting', 'Habitable zone', "
              "'Extremophile', 'Fraud' all score 0 - they are not one story).\n"
              'Return JSON: {"scores": {"1": 0, "2": 0}} with a score for every number.')
    try:
        data, _ = llm.generate_json(prompt, "You are a strict, picky YouTube content producer. You only score.",
                                    0.1, role="writer")
    except Exception as e:
        logs.warn("topics", f"AI judge unavailable ({e}); using keyword rules")
        return None
    scores = data.get("scores", {}) if isinstance(data, dict) else {}
    if isinstance(scores, list):
        scores = {str(i + 1): v for i, v in enumerate(scores)}
    out = []
    for k, v in (scores.items() if isinstance(scores, dict) else []):
        try:
            i, sc = int(str(k).strip(". ")) - 1, float(v)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(titles) and sc >= 7 and titles[i] not in out:
            out.append(titles[i])
    return out


def find(genre: str, channel_id: int, count: int = 5, sample: int = 200, rounds: int = 3) -> list[dict[str, Any]]:
    """Fresh, real topics for the genre, highest audience demand first: [{"topic", "views_30d"}]."""
    pool = [t for t in candidates(genre) if not research.is_duplicate(channel_id, t)]
    if not pool:
        return []
    random.shuffle(pool)                      # different sample each time -> variety, not the same top list
    kw = re.compile(KEYWORDS.get(genre, "."), re.I)
    picked: list[dict[str, Any]] = []
    for rnd in range(rounds):                 # a weak random sample gets another chance with fresh candidates
        chunk = pool[rnd * sample:(rnd + 1) * sample]
        if not chunk:
            break
        stats = _stats(chunk)
        ranked = sorted((t for t, s in stats.items() if s["bytes"] >= MIN_BYTES and s["views"] > 0
                         and not _NOT_A_STORY.search(s["desc"])),
                        key=lambda t: -stats[t]["views"] * (2 if _STORY_TITLE.search(t) else 1))[:max(25, count * 5)]
        intros = _intros(ranked)
        hits = {t: {m.group(0).lower()[:5] for m in kw.finditer(intros.get(t, ""))} for t in ranked}
        shortlist = [t for t in ranked if hits[t]]                      # mentions the genre at all
        approved = _judge(genre, shortlist, stats, intros) if shortlist else []
        if approved is None:                                            # no AI: stricter keyword rule
            approved = [t for t in shortlist if len(hits[t]) >= 2]
        ok = set(approved)
        for t in shortlist:                                             # keep demand order
            if t not in ok or research.is_duplicate(channel_id, t) or \
                    any(jaccard(t, p["topic"]) >= 0.6 for p in picked):
                continue
            picked.append({"topic": t, "views_30d": stats[t]["views"]})
            if len(picked) >= count:
                break
        logs.info("topics", f"{genre} round {rnd + 1}: {len(chunk)} sampled, {len(shortlist)} shortlisted, "
                            f"{len(ok)} approved; total {len(picked)}/{count}")
        if len(picked) >= count:
            break
    picked.sort(key=lambda p: -p["views_30d"])
    logs.info("topics", f"{genre}: picked {[(p['topic'], p['views_30d']) for p in picked]}")
    return picked


# ------------------------------------------------------------------ ready-made topic bank (instant suggestions)
BANK_SIZE = 8


def _bank_path(genre: str, channel_id: int):
    return config.DATA_DIR / "cache" / f"bank_{channel_id}_{genre}.json"


def _bank_load(genre: str, channel_id: int) -> list[dict[str, Any]]:
    try:
        return json.loads(_bank_path(genre, channel_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _bank_save(genre: str, channel_id: int, items: list[dict[str, Any]]) -> None:
    p = _bank_path(genre, channel_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def banked(genre: str, channel_id: int) -> list[dict[str, Any]]:
    """Approved topics not yet used on the channel, highest demand first."""
    fresh = [t for t in _bank_load(genre, channel_id) if not research.is_duplicate(channel_id, t["topic"])]
    return sorted(fresh, key=lambda t: -t.get("views_30d", 0))


def refill(genre: str, channel_id: int) -> int:
    """Top the bank up to BANK_SIZE fresh approved topics. Returns how many were added."""
    have = banked(genre, channel_id)
    if len(have) >= BANK_SIZE:
        return 0
    new = [t for t in find(genre, channel_id, BANK_SIZE) if t["topic"] not in {h["topic"] for h in have}]
    _bank_save(genre, channel_id, have + new)
    return len(new)


def ready(genre: str, channel_id: int, count: int) -> list[dict[str, Any]]:
    """Instant when the bank has enough; otherwise searches now (slow on CPU) and banks the extras."""
    have = banked(genre, channel_id)
    if len(have) < count:
        refill(genre, channel_id)
        have = banked(genre, channel_id)
    return have[:count]


def take(genre: str, channel_id: int, topic: str) -> None:
    """Remove a topic from the bank once a video is made from it."""
    _bank_save(genre, channel_id, [t for t in _bank_load(genre, channel_id) if t["topic"] != topic])
