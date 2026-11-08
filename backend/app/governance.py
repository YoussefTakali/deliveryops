"""Enterprise AI governance: who may use the platform, who may use the AI, and how much.

Controls (all editable at runtime by an admin with `governance.manage`):
- global AI kill switch (GovernancePolicy.ai_enabled)
- per-user app block (no access at all) and AI block (app yes, AI no)
- daily token quota: org default per user + per-user override (0 = unlimited)
- daily token cap per agent (copilot, companion, call assistant, livrables, billing…)

Enforcement points: `assert_app_access` (every authenticated request, app/auth.py), `check` (every
LLM call, the single choke point in app/llm.py) and `raise_if_ai_denied` (used by `auth.require_ai`, a
route dependency so interactive endpoints answer with a clear 403/429 instead of a silent fallback). Usage comes from UsageLedger,
written by app/telemetry for each call. Checks fail OPEN on database errors: governance must never be
the reason the platform stops.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import GovernancePolicy, UsageLedger, UserGovernance

log = logging.getLogger(__name__)

# Agents an admin can cap (key → label). Keys match LLMCall.agent / UsageLedger.agent.
AGENTS: dict[str, str] = {
    "copilot": "Copilot",
    "companion": "Compagnon",
    "call_assistant": "Assistant d'appel",
    "assistant": "Recherche & Mon espace",
    "deliverables": "Livrables",
    "automations": "Automatisations",
    "doc_generator": "Générateur de documents",
    "meeting_intelligence": "Comptes-rendus de réunion",
    "billing": "Facturation",
    "risk": "Analyse des risques",
    "incident": "Problèmes & incidents",
    "capacity": "Capacité & staffing",
    "governance": "Gouvernance & pilotage",
    "estimation": "Estimation",
    "knowledge_transfer": "Transfert de connaissance",
    "knowledge": "Recherche documentaire",
    "contract": "Extraction de contrat",
    "orchestrator": "Orchestrateur",
    "ui_translation": "Traduction de l'interface",
    "policy": "Contrôle de conformité (règles)",
    "tech_watch": "Veille technologique",
}

# What each consumer really is: an AGENT (its own loop / reasoning / recommendations: the orchestrated
# agents in app/agents/, the copilot, the companion, the call assistant) or an AI FEATURE (a single
# LLM-assisted function: livrables generation, automation compiling, search reranking…).
AGENT_KIND: dict[str, str] = {
    "copilot": "agent", "companion": "agent", "call_assistant": "agent", "orchestrator": "agent",
    "doc_generator": "agent", "meeting_intelligence": "agent", "billing": "agent", "risk": "agent",
    "incident": "agent", "capacity": "agent", "governance": "agent", "estimation": "agent", "knowledge_transfer": "agent",
    "assistant": "feature", "deliverables": "feature", "automations": "feature", "knowledge": "feature", "contract": "feature",
    "ui_translation": "feature", "policy": "agent", "tech_watch": "agent",
}

# Calls made outside an agent block (agent="") are attributed from their purpose.
_PURPOSE_AGENT = {
    "Companion": "companion", "_Distilled": "companion", "_QSuggestions": "companion",
    "LiveCopilot": "call_assistant", "LiveAnswerStream": "call_assistant", "LiveAnswer": "call_assistant",
    "assistant_ask": "assistant", "ask_answer": "assistant",
    "copilot_turn": "copilot",
    "MatriceDraft": "deliverables", "CustomSpec": "deliverables", "CustomContent": "deliverables",
    "GeneratedDoc": "doc_generator", "Minutes": "meeting_intelligence",
    "billing_cover_note": "billing", "capacity_summary": "capacity", "incident_summary": "incident",
    "RiskReport": "risk", "RiskExtraction": "risk", "CopilPack": "governance", "Estimate": "estimation",
    "AppCards": "knowledge_transfer", "OnboardingPlan": "knowledge_transfer",
    "_Ranked": "knowledge", "ContractRules": "contract", "Route": "orchestrator",
    "CompiledWorkflow": "automations", "RecordingAnalysis": "automations", "Decision": "automations",
    "_ResolvedElement": "automations", "ExtractedTable": "automations",
    "UiTranslationBatch": "ui_translation",
    "PolicyVerdict": "policy", "ExtractedRules": "policy", "TechWatchAnalysis": "tech_watch",
}


def agent_of(agent: str, purpose: str) -> str:
    if agent:
        return agent
    if purpose.startswith("automation_"):
        return "automations"
    return _PURPOSE_AGENT.get(purpose, "other")


def _day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------- policy & rules (short TTL cache)

_TTL = 5.0
_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, loader):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (now + _TTL, value)
    return value


def invalidate() -> None:
    with _lock:
        _cache.clear()


def policy(db: Session) -> GovernancePolicy:
    pol = db.get(GovernancePolicy, "org")
    if pol is None:
        pol = GovernancePolicy(id="org", ai_enabled=True, default_daily_tokens=0, agent_caps={})
        db.add(pol)
        try:
            db.flush()
        except IntegrityError:  # a parallel request created it first (first visit on a fresh database)
            db.rollback()
            pol = db.get(GovernancePolicy, "org")
    return pol


def _snapshot(username: str) -> dict:
    """Everything a check needs, in one DB round-trip, cached a few seconds."""
    def load():
        with SessionLocal() as db:
            pol = db.get(GovernancePolicy, "org")
            rule = db.get(UserGovernance, username) if username else None
            return {
                "ai_enabled": pol.ai_enabled if pol else True,
                "default_daily": (pol.default_daily_tokens if pol else 0) or 0,
                "agent_caps": dict((pol.agent_caps or {}) if pol else {}),
                "app_blocked": bool(rule and rule.app_blocked),
                "ai_blocked": bool(rule and rule.ai_blocked),
                "user_quota": rule.daily_token_quota if rule else None,
            }
    return _cached(("snap", username), load)


def usage_today(username: str | None = None, agent: str | None = None) -> int:
    """Tokens consumed since UTC midnight, for a user and/or an agent (cached a few seconds)."""
    def load():
        with SessionLocal() as db:
            stmt = select(func.coalesce(func.sum(UsageLedger.tokens), 0)).where(UsageLedger.ts >= _day_start())
            if username is not None:
                stmt = stmt.where(UsageLedger.username == username)
            if agent is not None:
                stmt = stmt.where(UsageLedger.agent == agent)
            return int(db.scalar(stmt) or 0)
    return _cached(("usage", username, agent), load)


def effective_quota(snap: dict) -> int:
    """0 = unlimited. A per-user value (even 0) overrides the org default."""
    return snap["user_quota"] if snap["user_quota"] is not None else snap["default_daily"]


def rules_for(username: str) -> dict:
    """The rules currently applied to a user (cached): ai_enabled, ai_blocked, app_blocked, quota."""
    snap = _snapshot(username)
    return {**snap, "quota": effective_quota(snap)}


# ---------------------------------------------------------------- checks

def check(username: str, agent: str) -> str | None:
    """None if the call may proceed, else a user-facing reason. Fails open on errors."""
    try:
        snap = _snapshot(username or "")
        if not snap["ai_enabled"]:
            return "L'IA est désactivée pour toute l'organisation par un administrateur."
        if snap["ai_blocked"]:
            return "Votre accès à l'IA a été suspendu par un administrateur."
        quota = effective_quota(snap)
        if username and quota > 0:
            used = usage_today(username=username)
            budget_alert("user", username, used, quota)
            if used >= quota:
                return f"Votre quota IA du jour est atteint ({quota:,} tokens). Il se réinitialise à minuit (UTC).".replace(",", " ")
        cap = int(snap["agent_caps"].get(agent) or 0)
        if cap > 0:
            used = usage_today(agent=agent)
            budget_alert("agent", agent, used, cap)
            if used >= cap:
                label = AGENTS.get(agent, agent)
                return f"La limite journalière de l'agent « {label} » est atteinte. Réessayez demain ou contactez un administrateur."
    except Exception:  # noqa: BLE001 — never let governance take the platform down
        log.exception("governance check failed; allowing the call")
    return None


# ---------------------------------------------------------------- budget alerts

ALERT_THRESHOLDS = (80, 100)  # % of a daily quota / agent cap
_alerted: set[str] = set()
_alert_lock = threading.Lock()


def budget_alert(scope: str, key: str, used: int, limit: int) -> None:
    """Warn once per threshold per day when a user's quota or an agent's cap is (almost) spent: the AI
    admins (in-app + Teams) and, for a personal quota, the person. Cheap on the LLM call path: an
    in-memory check, and the notification itself is written in a background thread."""
    if limit <= 0:
        return
    pct = used * 100 // limit
    reached = [t for t in ALERT_THRESHOLDS if pct >= t]
    if not reached:
        return
    day = _day_start().date().isoformat()
    fingerprint = f"budget:{day}:{scope}:{key}:{reached[-1]}"
    with _alert_lock:
        if fingerprint in _alerted:
            return
        _alerted.update(f"budget:{day}:{scope}:{key}:{t}" for t in reached)  # lower thresholds are implied
    threading.Thread(target=_send_budget_alert, args=(scope, key, reached[-1], used, limit, fingerprint),
                     daemon=True, name="budget-alert").start()


def _send_budget_alert(scope: str, key: str, threshold: int, used: int, limit: int, fingerprint: str) -> None:
    from app import notify
    from app.models import Notification

    def fmt(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    full = threshold >= 100
    if scope == "user":
        admin_title = f"{key} a {'atteint' if full else 'utilisé ' + str(threshold) + ' % de'} son quota IA du jour"
        body = f"{fmt(used)} / {fmt(limit)} tokens consommés aujourd'hui. Ajustez son quota dans Gouvernance IA si besoin."
    else:
        label = AGENTS.get(key, key)
        admin_title = f"Agent « {label} » : {'plafond journalier atteint' if full else str(threshold) + ' % du plafond journalier'}"
        body = f"{fmt(used)} / {fmt(limit)} tokens consommés aujourd'hui, tous utilisateurs confondus."
    try:
        with SessionLocal() as db:
            if db.scalar(select(Notification.id).where(Notification.fingerprint == fingerprint + ":admins")):
                return  # another process already sent it
            severity = "high" if full else "medium"
            db.add(Notification(kind="ai_budget", severity=severity, title=admin_title, body=body, link="/admin/governance",
                                fingerprint=fingerprint + ":admins", audience="governance.manage"))
            if scope == "user":
                own = ("Votre quota IA du jour est atteint" if full else f"Vous avez utilisé {threshold} % de votre quota IA du jour")
                db.add(Notification(kind="ai_budget", severity=severity, title=own,
                                    body=f"{fmt(used)} / {fmt(limit)} tokens. Le quota se réinitialise à minuit (UTC).",
                                    link="/account", fingerprint=fingerprint + ":user", recipient=key))
            db.commit()
        notify.budget_alert(admin_title, body)
    except Exception:  # noqa: BLE001 — an alert must never break anything
        log.exception("budget alert failed")


def assert_app_access(username: str) -> None:
    try:
        blocked = _snapshot(username)["app_blocked"]
    except Exception:  # noqa: BLE001
        log.exception("governance app-access check failed; allowing")
        return
    if blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Votre accès à DeliveryOps a été suspendu par un administrateur.")


def raise_if_ai_denied(username: str, agent: str = "") -> None:
    """HTTP flavour of `check` for interactive endpoints: 403 when blocked, 429 when over quota."""
    reason = check(username, agent)
    if reason:
        code = status.HTTP_429_TOO_MANY_REQUESTS if ("quota" in reason or "limite" in reason) else status.HTTP_403_FORBIDDEN
        raise HTTPException(code, reason)
