"""Small text helpers shared by agents."""
from __future__ import annotations

import re
import unicodedata

STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that
these those there their they them he she his her we our you your i me my not no so than then too very can could would
should will just also about into over after before more most such only other some any each which who whom what when
where why how all both few many much own same up down out off again further once here do does did done has have had""".split())


def normalize_key(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP]
    return " ".join(sorted(set(words)))


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def jaccard(a: str, b: str) -> float:
    sa, sb = content_words(a), content_words(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def copies_source(text: str, source: str, n: int = 10) -> bool:
    """True if `text` contains a run of >= n consecutive words copied verbatim from `source`."""
    t = _tokens(text)
    if len(t) < n:
        return False
    src = _tokens(source)
    grams = {tuple(src[i:i + n]) for i in range(len(src) - n + 1)}
    return any(tuple(t[i:i + n]) in grams for i in range(len(t) - n + 1))


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if len(p.strip()) > 1]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text)       # drop citations / parentheticals
    text = re.sub(r"[*_#`>]", "", text)
    text = re.sub(r"\.{2,}|…", " ", text).strip(" ,;-")          # no trailing-off fragments
    if text and text[-1] not in ".!?\"'":
        text += "."
    if text:
        text = text[0].upper() + text[1:]
    text = text.replace("&", " and ").replace("%", " percent")
    return re.sub(r"\s+", " ", text).strip()
