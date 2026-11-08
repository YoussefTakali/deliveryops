"""Which endpoint, key and model each agent calls — resolved at runtime from the admin UI
(LLMProviderConfig rows) on top of the .env settings.

Resolution for an agent: its own row, else the "default" row, else LLM_BASE_URL / LLM_API_KEY.
Each field inherits independently, with one safety rule: an API key is only inherited by a
configuration that points at the SAME base URL, so overriding an agent's endpoint can never send
another provider's key to it.

Rows are cached a few seconds (every LLM call resolves), and `invalidate()` is called on each admin
change. Loading fails open to the .env settings: a database hiccup must not take the AI down.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

import openai

from app import secret_box
from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

DEFAULT = "default"
_TTL = 5.0
_lock = threading.Lock()
_rows_cache: tuple[float, dict[str, dict]] | None = None
_clients: dict[tuple[str, str], openai.OpenAI] = {}


@dataclass(frozen=True)
class Resolved:
    base_url: str
    api_key: str | None
    model: str | None  # replaces the tier's model for this agent; None = keep it
    source: str        # "agent" | "default" | "env"


def _load_rows() -> dict[str, dict]:
    from app.db import SessionLocal
    from app.models import LLMProviderConfig

    with SessionLocal() as db:
        return {
            r.key: {
                "base_url": (r.base_url or "").strip(),
                "api_key": secret_box.decrypt(r.api_key_enc) if r.api_key_enc else None,
                "model": (r.model or "").strip(),
            }
            for r in db.query(LLMProviderConfig).all()
        }


def rows() -> dict[str, dict]:
    """Saved settings per key, API keys decrypted. Treat as read-only (shared cache)."""
    global _rows_cache
    now = time.monotonic()
    with _lock:
        if _rows_cache and _rows_cache[0] > now:
            return _rows_cache[1]
    try:
        loaded = _load_rows()
    except Exception:  # noqa: BLE001 — table missing / DB down: fall back to .env
        log.exception("could not load LLM provider settings; using .env values")
        loaded = {}
    with _lock:
        _rows_cache = (now + _TTL, loaded)
    return loaded


def invalidate() -> None:
    global _rows_cache
    with _lock:
        _rows_cache = None


def _layer(parent: Resolved, row: dict | None, source: str) -> Resolved:
    if not row:
        return parent
    base_url = row["base_url"] or parent.base_url
    same_endpoint = base_url.rstrip("/") == parent.base_url.rstrip("/")
    api_key = row["api_key"] or (parent.api_key if same_endpoint else None)
    return Resolved(base_url, api_key, row["model"] or parent.model, source)


def resolve(agent: str | None = None, saved: dict[str, dict] | None = None) -> Resolved:
    saved = rows() if saved is None else saved
    env = Resolved(settings.llm_base_url, settings.llm_api_key or None, None, "env")
    default = _layer(env, saved.get(DEFAULT), "default")
    default = Resolved(default.base_url, default.api_key, None, default.source)  # no org-wide model override
    if not agent or agent == DEFAULT:
        return default
    return _layer(default, saved.get(agent), "agent")


def any_key() -> bool:
    return bool(settings.llm_api_key) or any(r["api_key"] for r in rows().values())


def client_for(cfg: Resolved) -> openai.OpenAI:
    """One OpenAI client per (endpoint, key), reused across calls."""
    fingerprint = hashlib.sha256((cfg.api_key or "").encode()).hexdigest()
    cache_key = (cfg.base_url, fingerprint)
    with _lock:
        client = _clients.get(cache_key)
        if client is None:
            client = openai.OpenAI(
                base_url=cfg.base_url, api_key=cfg.api_key or "not-set",
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,  # SDK retries 408/409/429/5xx with exponential backoff
            )
            _clients[cache_key] = client
    return client
