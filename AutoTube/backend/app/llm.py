"""LLM provider abstraction with automatic fallback: Ollama (local) -> Gemini (free tier, optional) -> template.

The 'template' provider is not an LLM; callers detect it via LLMUnavailable and use their own
deterministic, source-grounded fallback so the pipeline never hard-fails without AI.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from . import config, db, logs, quota


class LLMUnavailable(RuntimeError):
    pass


def _ollama(prompt: str, system: str, want_json: bool, temperature: float, role: str = "fast") -> str:
    model = config.OLLAMA_MODEL_WRITER if role == "writer" else config.OLLAMA_MODEL
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    if want_json:
        body["format"] = "json"
    r = requests.post(f"{config.OLLAMA_URL}/api/generate", json=body, timeout=900)
    r.raise_for_status()
    return r.json()["response"]


def _gemini(prompt: str, system: str, want_json: bool, temperature: float, role: str = "fast") -> str:
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    if not quota.allow("gemini", 1):
        raise LLMUnavailable("Gemini daily request budget reached")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"temperature": temperature},
    }
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=180)
    quota.record("gemini", "generateContent", 1)
    if r.status_code == 429:
        raise LLMUnavailable("Gemini free-tier rate limit hit (429)")
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


PROVIDERS = {"ollama": _ollama, "gemini": _gemini}


def ollama_status() -> dict[str, Any]:
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        ready = all(any(m.startswith(x) for m in models) for x in {config.OLLAMA_MODEL, config.OLLAMA_MODEL_WRITER})
        return {"running": True, "models": models, "model_ready": ready}
    except Exception:
        return {"running": False, "models": [], "model_ready": False}


def generate(prompt: str, system: str = "You are a helpful assistant.", want_json: bool = False,
             temperature: float = 0.7, video_id: int | None = None, role: str = "fast") -> tuple[str, str]:
    """Returns (text, provider_name). Raises LLMUnavailable if every real provider fails."""
    order = db.get_setting("llm_provider_order") or ["ollama", "gemini", "template"]
    errors = []
    for name in order:
        fn = PROVIDERS.get(name)
        if fn is None:
            continue
        for attempt in range(2):
            try:
                t0 = time.time()
                out = fn(prompt, system, want_json, temperature, role)
                logs.info("llm", f"{name}/{role} ok in {time.time() - t0:.1f}s", video_id)
                return out, name
            except LLMUnavailable as e:
                errors.append(f"{name}: {e}")
                break
            except Exception as e:  # network / server error -> retry once then next provider
                errors.append(f"{name}: {e}")
                time.sleep(2)
    raise LLMUnavailable("; ".join(errors) or "no providers configured")


def parse_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def generate_json(prompt: str, system: str, temperature: float = 0.6, video_id: int | None = None,
                  role: str = "fast") -> tuple[Any, str]:
    last_err: Exception | None = None
    for _ in range(2):
        text, provider = generate(prompt, system, want_json=True, temperature=temperature, video_id=video_id, role=role)
        try:
            return parse_json(text), provider
        except Exception as e:
            last_err = e
            prompt = prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON, no commentary."
    raise LLMUnavailable(f"model returned invalid JSON: {last_err}")
