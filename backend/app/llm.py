"""Single entry point for LLM calls, over any OpenAI-compatible API (NVIDIA API catalog / NIM,
vLLM, Ollama, company gateways...).

Agents compute facts deterministically (KPIs, amounts, loads) and use the LLM for extraction,
reasoning and narrative. Every call takes a `fallback` so the platform keeps working when the API
is unavailable, rate limited or returns unusable output. Every call is recorded by app.telemetry.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Literal, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from app import llm_config, telemetry
from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)
Tier = Literal["reasoning", "agent", "fast"]

BASE_SYSTEM = (
    "You are part of DeliveryOps, a multi-agent platform assisting AMS/TMA delivery teams "
    "(application maintenance). Answer in French unless the source material is in another language. "
    "Ground every statement in the provided context; when the context does not support a claim, "
    "say so instead of guessing. Cite sources using the identifiers given in the context."
)

_slots = threading.BoundedSemaphore(max(1, settings.llm_max_concurrency))

API_ERRORS = (openai.APIError, openai.APIConnectionError, openai.APITimeoutError)


class LLMUnavailable(RuntimeError):
    blocked = False  # True when refused by governance (quota, AI blocked) rather than an outage


class LLMBlocked(openai.APIError):
    """Refused by AI governance (app.governance): AI disabled/blocked, daily quota or agent cap reached.
    Subclasses openai.APIError on purpose, so every existing caller that already copes with API errors
    (structured/text fallbacks, the copilot, automations) degrades gracefully without any change."""

    def __init__(self, reason: str):
        import httpx
        super().__init__(reason, httpx.Request("POST", "https://governance.local/blocked"), body=None)
        self.reason = reason


def _agent(purpose: str) -> str:
    from app import governance
    return governance.agent_of(telemetry.current().get("agent", ""), purpose)


def _guard(purpose: str, model: str, agent: str | None = None) -> None:
    """The single governance choke point: every model call goes through chat() or stream_text()."""
    from app import governance
    reason = governance.check(telemetry.current().get("username", ""), agent or _agent(purpose))
    if reason:
        telemetry.record(purpose, model, 0, "blocked")
        raise LLMBlocked(reason)


def _route(purpose: str, model: str) -> tuple[openai.OpenAI, str]:
    """Endpoint, key and model for this call's agent (admin UI settings over .env), after governance."""
    agent = _agent(purpose)
    cfg = llm_config.resolve(agent)
    model = cfg.model or model
    _guard(purpose, model, agent)
    return llm_config.client_for(cfg), model


def _enabled(purpose: str) -> bool:
    """Whether this call may reach a model at all: LLM_MODE, then whether its agent has a key."""
    if settings.llm_mode in ("offline", "mock"):
        return False
    if settings.llm_mode == "api":
        return True
    return bool(llm_config.resolve(_agent(purpose)).api_key)


# Answers a person reads in a conversation follow their interface language; generated documents
# (livrables, minutes, COPIL packs…) stay in French, the language of what is delivered.
CONVERSATIONAL = {"copilot_turn", "Companion", "_QSuggestions", "LiveCopilot", "LiveAnswer", "LiveAnswerStream",
                  "assistant_ask", "ask_answer", "TechWatchAnalysis"}


def _localized(purpose: str, messages: list[dict]) -> list[dict]:
    if purpose not in CONVERSATIONAL:
        return messages
    code, name = telemetry.ui_language()
    if not code or code.split("-")[0] == "fr":
        return messages
    rule = (f"\n\nThe user's interface language is {f'{name} ({code})' if name else code}. Write your answer in that "
            "language, even though the context and these instructions are in French, unless an explicit answer language "
            "was set above. Keep quotes, names, codes and document titles as they are.")
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": messages[0]["content"] + rule}, *messages[1:]]
    return [{"role": "system", "content": rule.strip()}, *messages]


def _prepared(purpose: str, messages: list[dict]) -> list[dict]:
    """Every model call: the company rules (app/policy.py) and the user's language on the system prompt."""
    from app import policy

    messages = _localized(purpose, messages)
    rules = policy.instructions(purpose)
    if not rules:
        return messages
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": messages[0]["content"] + rules}, *messages[1:]]
    return [{"role": "system", "content": rules.strip()}, *messages]


def get_client() -> openai.OpenAI:
    """Client for the org-wide default endpoint (agent-specific calls go through _route)."""
    return llm_config.client_for(llm_config.resolve())


def model_for(tier: Tier) -> str:
    return {"reasoning": settings.llm_model_reasoning, "agent": settings.llm_model_agent, "fast": settings.llm_model_fast}[tier]


def _cost(usage) -> float:
    if usage is None:
        return 0.0
    return round((usage.prompt_tokens * settings.llm_price_input_per_m + usage.completion_tokens * settings.llm_price_output_per_m) / 1_000_000, 6)


def chat(purpose: str, *, model: str, messages: list[dict], **params):
    """Raw chat completion with concurrency limit and telemetry. Raises API errors (incl. LLMBlocked)."""
    client, model = _route(purpose, model)
    started = time.monotonic()
    with _slots:
        try:
            response = client.chat.completions.create(
                model=model, messages=_prepared(purpose, messages), temperature=params.pop("temperature", settings.llm_temperature),
                max_tokens=params.pop("max_tokens", settings.llm_max_tokens), **params,
            )
        except API_ERRORS:
            telemetry.record(purpose, model, int((time.monotonic() - started) * 1000), "error")
            raise
    telemetry.record(purpose, model, int((time.monotonic() - started) * 1000), "ok", response.usage, _cost(response.usage))
    return response


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def clean_text(text: str | None) -> str:
    """Remove reasoning traces some models (DeepSeek-R1, Nemotron reasoning) put in the content."""
    return _THINK.sub("", text or "").strip()


def extract_json(text: str) -> dict:
    body = _FENCE.sub("", clean_text(text)).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        if start < 0:
            raise
        depth = 0
        in_string = escaped = False
        for i, ch in enumerate(body[start:], start):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(body[start : i + 1])
        raise


def _json_params(schema: dict, name: str, mode: str) -> dict:
    if mode == "nvext":
        return {"extra_body": {"nvext": {"guided_json": schema}}}
    if mode == "json_schema":
        return {"response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}}
    if mode == "json_object":
        return {"response_format": {"type": "json_object"}}
    return {}


def structured(
    *,
    schema: type[T],
    task: str,
    context: str,
    fallback: Callable[[], T],
    tier: Tier = "agent",
    effort: str = "medium",  # kept for call-site compatibility; OpenAI-compatible APIs have no equivalent
    system: str | None = None,
    thinking: bool | None = None,
    max_tokens: int | None = None,
) -> tuple[T, bool]:
    """Returns (result, produced_by_llm). Output is validated against the Pydantic schema; one repair
    attempt is made with the validation error before falling back.

    `system` replaces the generic platform preamble (the JSON-schema instruction is always appended);
    `thinking=False` disables hybrid reasoning models' hidden reasoning pass (see stream_text)."""
    purpose = schema.__name__
    if not _enabled(purpose):
        telemetry.record(purpose, "offline", 0, "fallback")
        return fallback(), False

    json_schema = schema.model_json_schema()
    model = model_for(tier)
    messages = [
        {"role": "system", "content": f"{system or BASE_SYSTEM}\n\nRespond with a single JSON object that validates against this JSON schema, and nothing else:\n{json.dumps(json_schema, ensure_ascii=False)}"},
        {"role": "user", "content": f"<context>\n{context}\n</context>\n\n{task}"},
    ]
    mode = settings.llm_json_mode
    extra: dict = {"max_tokens": max_tokens} if max_tokens else {}
    for attempt in range(3):
        params = {**_json_params(json_schema, purpose, mode), **extra}
        if thinking is False:
            params["extra_body"] = {**params.get("extra_body", {}), "chat_template_kwargs": {"enable_thinking": False}}
        try:
            response = chat(purpose, model=model, messages=messages, **params)
        except openai.BadRequestError as exc:
            if mode != "prompt":  # endpoint/model does not support constrained decoding: retry with instructions only
                log.warning("%s: %s rejected (%s), retrying with prompt-only JSON", purpose, mode, exc)
                mode = "prompt"
                continue
            log.error("%s: LLM request rejected: %s", purpose, exc)
            break
        except API_ERRORS as exc:
            log.error("%s: LLM unavailable, using fallback: %s", purpose, exc)
            break
        content = response.choices[0].message.content or ""
        try:
            return schema.model_validate(extract_json(content)), True
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("%s: invalid JSON from model (attempt %d): %s", purpose, attempt + 1, str(exc)[:300])
            messages += [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": f"The JSON was invalid: {str(exc)[:800]}. Return only the corrected JSON object."},
            ]
    telemetry.record(purpose, model, 0, "fallback")
    return fallback(), False


def stream_text(*, task: str, context: str, tier: Tier = "agent", purpose: str = "text",
                system: str | None = None, thinking: bool | None = None, max_tokens: int | None = None):
    """Streaming counterpart of `text()`: yields text chunks as they arrive from the model instead of
    waiting for the full completion. Raises LLMUnavailable (offline mode or API error) so the caller
    can fall back to a static message — there is no repair/retry loop here, unlike `structured()`.

    Note: reasoning-model `<think>...</think>` traces (see `clean_text`) are not stripped per-chunk,
    since they can straddle chunk boundaries; callers should assume raw model output for this path.
    """
    if not _enabled(purpose):
        raise LLMUnavailable("LLM disabled (offline/mock mode)")
    model = model_for(tier)
    try:
        client, model = _route(purpose, model)
    except LLMBlocked as exc:
        err = LLMUnavailable(exc.reason)
        err.blocked = True
        raise err from exc
    messages = _prepared(purpose, [
        {"role": "system", "content": system or BASE_SYSTEM},
        {"role": "user", "content": f"<context>\n{context}\n</context>\n\n{task}"},
    ])
    # thinking=False turns off hybrid reasoning models' hidden reasoning pass (Nemotron 3 etc.):
    # measured ~0.5s vs ~5s to first visible token, which is what a live call overlay needs.
    extra = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}} if thinking is False else {}
    started = time.monotonic()
    got_any = False
    with _slots:
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, temperature=settings.llm_temperature,
                max_tokens=max_tokens or settings.llm_max_tokens, stream=True, **extra,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    got_any = True
                    yield delta
        except API_ERRORS as exc:
            telemetry.record(purpose, model, int((time.monotonic() - started) * 1000), "error")
            raise LLMUnavailable(str(exc)) from exc
    telemetry.record(purpose, model, int((time.monotonic() - started) * 1000), "ok" if got_any else "error")
    if not got_any:
        raise LLMUnavailable("empty stream response")


def text(*, task: str, context: str, fallback: Callable[[], str], tier: Tier = "agent", effort: str = "medium", purpose: str = "text",
         model: str | None = None, system: str | None = None, thinking: bool | None = None, max_tokens: int | None = None) -> tuple[str, bool]:
    """`model` overrides the tier's model (e.g. a bigger model for the personal search answer);
    `system` replaces the platform preamble; `thinking=False` disables hybrid models' reasoning pass."""
    if not _enabled(purpose):
        telemetry.record(purpose, "offline", 0, "fallback")
        return fallback(), False
    model = model or model_for(tier)
    extra: dict = {}
    if thinking is not None:
        extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": bool(thinking)}}
    if max_tokens:
        extra["max_tokens"] = max_tokens
    try:
        response = chat(purpose, model=model, messages=[
            {"role": "system", "content": system or BASE_SYSTEM},
            {"role": "user", "content": f"<context>\n{context}\n</context>\n\n{task}"},
        ], **extra)
    except API_ERRORS as exc:
        log.error("%s: LLM unavailable, using fallback: %s", purpose, exc)
        return fallback(), False
    content = clean_text(response.choices[0].message.content)
    if not content:
        return fallback(), False
    return content, True


def ping() -> dict:
    """Readiness probe for the configured LLM endpoint (lists models, no generation cost)."""
    if not settings.use_llm:
        return {"status": "offline"}
    try:
        with _slots:
            get_client().with_options(max_retries=0, timeout=10).models.list()
        return {"status": "ok", "base_url": llm_config.resolve().base_url}
    except API_ERRORS as exc:
        return {"status": "error", "error": type(exc).__name__}
