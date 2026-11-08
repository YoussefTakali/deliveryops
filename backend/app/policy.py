"""Company rules every agent and every user must respect ("Règles & conformité").

Two layers, both driven by the rules an admin manages (routes/policy.py):
  1. Agents — the active rules are added to the instructions of EVERY model call (app/llm.py), so
     answers, documents, plans and automations stay within them.
  2. Users — what a person types into an AI feature (copilot, companion, search, automations) is
     checked before the AI sees it: keywords first (instant), then a fast model pass that only flags
     clear breaches. A `block` rule refuses the request with the reason; a `warn` rule lets it
     through but makes the answer open with a warning. Only the rule, the person and the place are
     recorded — never the text itself, which may be exactly the confidential data the rule protects.
"""

import hashlib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app import telemetry
from app.db import SessionLocal

log = logging.getLogger(__name__)

CATEGORIES = {"confidentialite": "Confidentialité", "securite": "Sécurité", "logiciels": "Logiciels & installations",
              "conformite": "Conformité", "autre": "Autre"}
ACTIONS = {"block": "Bloquer", "warn": "Avertir", "guide": "Guider les agents"}
_INSTRUCTIONS_MAX = 5000
_TTL = 30.0
_VERDICT_TTL = 600.0
# The compliance check itself and rule extraction must not be steered by the rules they evaluate.
_UNGUIDED = {"PolicyVerdict", "ExtractedRules", "UiTranslationBatch"}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    text: str
    category: str
    action: str
    keywords: tuple[str, ...]


@dataclass
class Hit:
    rule: Rule
    reason: str
    by: str  # keyword | ai


@dataclass
class Verdict:
    blocked: list[Hit] = field(default_factory=list)
    warned: list[Hit] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.blocked and not self.warned


# ---------------------------------------------------------------- active rules (cached)

_lock = threading.Lock()
_cache: tuple[float, list[Rule]] | None = None
_verdicts: dict[str, tuple[float, Verdict]] = {}


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None
        _verdicts.clear()


def active_rules() -> list[Rule]:
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and _cache[0] > now:
            return _cache[1]
    try:
        from app.models import PolicyRule

        with SessionLocal() as db:
            rows = db.query(PolicyRule).filter(PolicyRule.active.is_(True), PolicyRule.proposed.is_(False)) \
                .order_by(PolicyRule.created_at).all()
            rules = [Rule(r.id, r.title, r.text or "", r.category, r.action, tuple(k for k in (r.keywords or []) if k)) for r in rows]
    except Exception:  # noqa: BLE001 — table missing / DB hiccup: no rules rather than no platform
        log.exception("could not load company rules")
        rules = []
    with _lock:
        _cache = (now + _TTL, rules)
    return rules


# ---------------------------------------------------------------- layer 1: every agent

def instructions(purpose: str) -> str:
    """The rules block appended to the system prompt of every model call (empty when there are none)."""
    if purpose in _UNGUIDED:
        return ""
    rules = active_rules()
    lines = [f"- [{ACTIONS[r.action].upper()}] {r.title}" + (f" : {r.text}" if r.text else "") for r in rules]
    notes = telemetry.policy_notes()
    if not lines and not notes:
        return ""
    block = ""
    if lines:
        body = "\n".join(lines)
        if len(body) > _INSTRUCTIONS_MAX:
            body = "\n".join(f"- [{ACTIONS[r.action].upper()}] {r.title}" for r in rules)[:_INSTRUCTIONS_MAX]
        block = ("\n\n<regles_entreprise>\nRègles de l'entreprise, à respecter strictement dans toute réponse, document, plan ou "
                 "action que tu produis. Ne propose jamais de les contourner. Si une demande les enfreint, refuse poliment la partie "
                 f"concernée en citant la règle, puis aide dans le respect des règles.\n{body}\n</regles_entreprise>")
    if notes:
        block += ("\n\n<alerte_conformite>\nLe message de la personne enfreint peut-être ces règles : "
                  + " ; ".join(notes) + ". Commence ta réponse par un court avertissement (une phrase) qui cite la règle, "
                  "puis aide-la sans enfreindre la règle.\n</alerte_conformite>")
    return block


# ---------------------------------------------------------------- layer 2: what users type

def _fold(text: str) -> str:
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()


def keyword_hits(rules: list[Rule], text: str) -> list[Hit]:
    folded = _fold(text)
    hits = []
    for r in rules:
        for kw in r.keywords:
            if len(kw) > 2 and kw.startswith("/") and kw.endswith("/"):
                try:
                    found = re.search(kw[1:-1], text, re.IGNORECASE)
                except re.error:
                    continue
                reason = "contient un élément interdit par cette règle"  # a raw pattern means nothing to the reader
            else:
                word = _fold(kw).strip()
                found = bool(word) and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", folded)
                reason = f"contient « {kw} »"
            if found:
                hits.append(Hit(r, reason, "keyword"))
                break
    return hits


class _Violation(BaseModel):
    rule: int = Field(description="numéro de la règle enfreinte")
    explanation: str = Field(description="pourquoi, en une phrase, sans recopier de donnée sensible")


class PolicyVerdict(BaseModel):
    violations: list[_Violation] = Field(default_factory=list)


def _ai_hits(rules: list[Rule], text: str, where: str) -> list[Hit]:
    from app import llm

    if not rules:
        return []
    numbered = "\n".join(f"{i + 1}. {r.title}" + (f" — {r.text}" if r.text else "") for i, r in enumerate(rules))
    with telemetry.attribute(agent="policy"):
        verdict, used = llm.structured(
            schema=PolicyVerdict, tier="fast", thinking=False, max_tokens=400,
            system=("Tu es l'agent de conformité de l'entreprise. Tu vérifies si un message qu'une personne veut envoyer à un "
                    "assistant IA enfreint CLAIREMENT une des règles numérotées. Juge ce que la personne écrit ou demande, pas "
                    "le sujet en général. En cas de doute, ne signale rien. Ne recopie jamais de donnée sensible."),
            context=f"<regles>\n{numbered}\n</regles>\n\n<message lieu=\"{where}\">\n{text[:6000]}\n</message>",
            task="Liste les règles clairement enfreintes par ce message (liste vide si aucune).",
            fallback=lambda: PolicyVerdict(),
        )
    if not used:
        return []
    return [Hit(rules[v.rule - 1], v.explanation.strip()[:300], "ai") for v in verdict.violations if 1 <= v.rule <= len(rules)]


def check(text: str, where: str) -> Verdict:
    """Which block/warn rules this text breaks. Keyword matches first; the model pass covers the rest."""
    rules = [r for r in active_rules() if r.action in ("block", "warn")]
    text = (text or "").strip()
    if not rules or not text:
        return Verdict()
    key = hashlib.sha256(f"{where}|{','.join(r.id for r in rules)}|{text}".encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        hit = _verdicts.get(key)
        if hit and hit[0] > now:
            return hit[1]
    hits = keyword_hits(rules, text)
    matched = {h.rule.id for h in hits}
    try:
        hits += _ai_hits([r for r in rules if r.id not in matched], text, where)
    except Exception:  # noqa: BLE001 — the model check is best effort; keywords still apply
        log.exception("policy model check failed")
    verdict = Verdict(blocked=[h for h in hits if h.rule.action == "block"], warned=[h for h in hits if h.rule.action == "warn"])
    with _lock:
        if len(_verdicts) > 2000:
            _verdicts.clear()
        _verdicts[key] = (now + _VERDICT_TTL, verdict)
    return verdict


def enforce(db, username: str, text: str, where: str) -> Verdict:
    """Check a user's input; refuse it (HTTP 403) on a blocking rule, or arm the warning for the answer.
    Every breach is audited — rule, person, place, decision — never the text."""
    from app import audit

    verdict = check(text, where)
    if verdict.clean:
        return verdict
    for hits, decision in ((verdict.blocked, "blocked"), (verdict.warned, "warned")):
        for h in hits:
            audit.log(db, username, "policy.violation", None, "policy_rule", h.rule.id, rule=h.rule.title,
                      where=where, decision=decision, detected_by=h.by, length=len(text))
    db.commit()
    if verdict.blocked:
        first = verdict.blocked[0]
        others = f" (et {len(verdict.blocked) - 1} autre(s) règle(s))" if len(verdict.blocked) > 1 else ""
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"⛔ Refusé par la règle de l'entreprise « {first.rule.title} »{others} : {first.reason}. "
                            "Reformulez sans enfreindre la règle, ou contactez un administrateur.")
    telemetry.add_policy_notes([f"« {h.rule.title} » ({h.reason})" for h in verdict.warned])
    return verdict
