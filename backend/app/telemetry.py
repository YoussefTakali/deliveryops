"""LLM call attribution and cost tracking."""

import contextvars
import logging
import queue
import re
import threading
from contextlib import contextmanager

from app.db import SessionLocal
from app.governance import agent_of
from app.models import LLMCall, UsageLedger

log = logging.getLogger(__name__)

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("llm_ctx", default={})
# Per-request holder for the signed-in user. The request middleware installs a fresh dict; auth writes
# the username into it. Being a shared (mutable) object, it is visible from the endpoint's threadpool
# thread and from a streaming response body alike — a plain ContextVar set inside the sync auth
# dependency would not propagate back to the endpoint.
_request_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("request_user", default=None)


def start_request() -> None:
    _request_user.set({})


def bind_user(username: str) -> None:
    holder = _request_user.get()
    if holder is not None:
        holder["username"] = username


_LANG_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_LANG_NAME = re.compile(r"^[A-Za-z][A-Za-z ()',.-]{0,60}$")


def bind_language(code: str, name: str = "") -> None:
    """The interface language the SPA sends (X-UI-Language), so conversational answers match it."""
    holder = _request_user.get()
    code = (code or "").strip()
    if holder is not None and _LANG_CODE.match(code):
        holder["ui_lang"] = code
        holder["ui_lang_name"] = name.strip() if _LANG_NAME.match(name.strip()) else ""


def add_policy_notes(notes: list[str]) -> None:
    """Rules this request's message may breach (warn level): every answer in the request opens with a warning."""
    holder = _request_user.get()
    if holder is not None:
        holder.setdefault("policy_notes", []).extend(notes)


def policy_notes() -> list[str]:
    return list((_request_user.get() or {}).get("policy_notes", []))


def ui_language() -> tuple[str, str]:
    """(code, English name) of the current request's interface language; ("", "") outside a request."""
    holder = _request_user.get() or {}
    return holder.get("ui_lang", ""), holder.get("ui_lang_name", "")


@contextmanager
def attribute(**fields):
    """Tag every LLM call made inside the block (agent, run_id, perimeter_id)."""
    token = _ctx.set({**_ctx.get(), **fields})
    try:
        yield
    finally:
        _ctx.reset(token)


def current() -> dict:
    ctx = dict(_ctx.get())
    if not ctx.get("username"):
        holder = _request_user.get()
        if holder and holder.get("username"):
            ctx["username"] = holder["username"]
    return ctx


def record(purpose: str, model: str, latency_ms: int, status: str, usage=None, cost_usd: float = 0.0) -> None:
    """`usage` is an OpenAI-compatible usage object (prompt_tokens, completion_tokens, optional cached tokens)."""
    ctx = current()
    row = LLMCall(
        perimeter_id=ctx.get("perimeter_id"), run_id=ctx.get("run_id"), agent=ctx.get("agent", ""), purpose=purpose[:200],
        model=model[:60], latency_ms=latency_ms, status=status, cost_usd=cost_usd,
    )
    if usage is not None:
        row.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        row.output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        row.cache_read_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    _queue.put(row)
    # Governance ledger: who consumed how much, through which agent (quotas + per-user views).
    _queue.put(UsageLedger(username=ctx.get("username", "") or "", agent=agent_of(ctx.get("agent", ""), purpose)[:60],
                           purpose=purpose[:200], model=model[:60], status=status[:20],
                           tokens=(row.input_tokens or 0) + (row.output_tokens or 0)))


# Rows are written by a background thread so telemetry never waits on (or breaks) an agent's
# open transaction — SQLite allows a single writer.
_queue: "queue.Queue" = queue.Queue()


def _writer() -> None:
    while True:
        rows = [_queue.get()]
        while not _queue.empty():
            rows.append(_queue.get_nowait())
        for attempt in range(5):
            try:
                with SessionLocal() as db:
                    db.add_all(rows)
                    db.commit()
                break
            except Exception:  # noqa: BLE001
                if attempt == 4:
                    log.exception("dropping %d LLM call records", len(rows))
                threading.Event().wait(1 + attempt)


def flush(timeout: float = 5.0) -> None:
    """Wait until queued rows are written (tests, shutdown)."""
    deadline = threading.Event()
    for _ in range(int(timeout * 20)):
        if _queue.empty():
            deadline.wait(0.1)
            return
        deadline.wait(0.05)


threading.Thread(target=_writer, daemon=True, name="telemetry-writer").start()
