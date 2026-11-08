"""AI governance API (/api/governance) — enterprise controls, `governance.manage` only.

- policy: global AI switch, default daily token quota per user, daily token cap per agent
- users: suspend a user's access, block their AI use, override their daily quota
- usage: consumption by user / agent / day (from the usage ledger)
Every change is written to the audit log.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, governance, team
from app.auth import User, require_permission
from app.db import get_db
from app.models import UsageLedger, UserGovernance

router = APIRouter(prefix="/api/governance")
_admin = require_permission("governance.manage")


def _day_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _usage_by(db: Session, since: datetime, field) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for key, tokens in db.execute(select(field, UsageLedger.tokens).where(UsageLedger.ts >= since)):
        out[key or ""] += tokens or 0
    return out


def _policy_out(db: Session) -> dict:
    pol = governance.policy(db)
    today = _usage_by(db, _day_start(), UsageLedger.agent)
    caps = pol.agent_caps or {}
    return {
        "ai_enabled": pol.ai_enabled, "default_daily_tokens": pol.default_daily_tokens or 0,
        "agents": [{"key": k, "label": label, "kind": governance.AGENT_KIND.get(k, "feature"),
                    "cap": int(caps.get(k) or 0), "used_today": today.get(k, 0)}
                   for k, label in governance.AGENTS.items()],
        "updated_by": pol.updated_by, "updated_at": pol.updated_at.isoformat() if pol.updated_at else None,
    }


@router.get("/policy")
def get_policy(user: User = Depends(_admin), db: Session = Depends(get_db)):
    out = _policy_out(db)
    db.commit()
    return out


class PolicyIn(BaseModel):
    ai_enabled: bool | None = None
    default_daily_tokens: int | None = Field(None, ge=0, le=100_000_000)
    agent_caps: dict[str, int] | None = None


@router.put("/policy")
def put_policy(body: PolicyIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    pol = governance.policy(db)
    changes: dict = {}
    if body.ai_enabled is not None:
        pol.ai_enabled = changes["ai_enabled"] = body.ai_enabled
    if body.default_daily_tokens is not None:
        pol.default_daily_tokens = changes["default_daily_tokens"] = body.default_daily_tokens
    if body.agent_caps is not None:
        unknown = [k for k in body.agent_caps if k not in governance.AGENTS]
        if unknown:
            raise HTTPException(400, f"Agent inconnu : {', '.join(unknown)}")
        merged = dict(pol.agent_caps or {})
        merged.update({k: max(0, int(v)) for k, v in body.agent_caps.items()})
        pol.agent_caps = {k: v for k, v in merged.items() if v > 0}
        changes["agent_caps"] = body.agent_caps
    pol.updated_by = user.username
    audit.log(db, user.username, "governance.policy_update", None, "governance_policy", "org", **changes)
    db.commit()
    governance.invalidate()
    return _policy_out(db)


@router.get("/users")
def list_users(q: str = "", user: User = Depends(_admin), db: Session = Depends(get_db)):
    pol = governance.policy(db)
    rules = {r.username: r for r in db.scalars(select(UserGovernance))}
    today = _usage_by(db, _day_start(), UsageLedger.username)
    week = _usage_by(db, _day_start() - timedelta(days=6), UsageLedger.username)
    people = {p["owner"]: p["name"] for p in team.directory(db)}
    for extra in set(rules) | {u for u in today if u}:  # anyone governed or active today, even if not in the directory
        people.setdefault(extra, extra)
    needle = q.strip().lower()
    out = []
    for username, name in people.items():
        if needle and needle not in username.lower() and needle not in (name or "").lower():
            continue
        r = rules.get(username)
        quota = r.daily_token_quota if (r and r.daily_token_quota is not None) else (pol.default_daily_tokens or 0)
        out.append({
            "username": username, "name": name, "app_blocked": bool(r and r.app_blocked), "ai_blocked": bool(r and r.ai_blocked),
            "daily_token_quota": r.daily_token_quota if r else None, "effective_quota": quota,
            "used_today": today.get(username, 0), "used_7d": week.get(username, 0), "note": r.note if r else "",
            "is_self": username == user.username,
        })
    out.sort(key=lambda u: (-u["used_today"], (u["name"] or u["username"]).lower()))
    db.commit()
    return {"users": out, "default_daily_tokens": pol.default_daily_tokens or 0}


class UserRuleIn(BaseModel):
    app_blocked: bool | None = None
    ai_blocked: bool | None = None
    daily_token_quota: int | None = Field(None, ge=0, le=100_000_000)
    reset_quota: bool = False          # back to the org default
    note: str | None = Field(None, max_length=300)


@router.put("/users/{username}")
def put_user(username: str, body: UserRuleIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    if username == user.username and (body.app_blocked or body.ai_blocked):
        raise HTTPException(400, "Vous ne pouvez pas vous bloquer vous-même.")
    rule = db.get(UserGovernance, username) or UserGovernance(username=username)
    db.add(rule)
    changes: dict = {}
    for f in ("app_blocked", "ai_blocked", "note"):
        v = getattr(body, f)
        if v is not None:
            setattr(rule, f, v)
            changes[f] = v
    if body.reset_quota:
        rule.daily_token_quota = None
        changes["daily_token_quota"] = "default"
    elif body.daily_token_quota is not None:
        rule.daily_token_quota = changes["daily_token_quota"] = body.daily_token_quota
    rule.updated_by = user.username
    audit.log(db, user.username, "governance.user_update", None, "user", username, **changes)
    db.commit()
    governance.invalidate()
    return {"username": username, "app_blocked": rule.app_blocked, "ai_blocked": rule.ai_blocked,
            "daily_token_quota": rule.daily_token_quota, "note": rule.note}


@router.get("/usage")
def usage(days: int = 7, user: User = Depends(_admin), db: Session = Depends(get_db)):
    days = max(1, min(days, 90))
    since = _day_start() - timedelta(days=days - 1)
    by_user: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "calls": 0, "blocked": 0})
    by_agent: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "calls": 0, "blocked": 0})
    by_day: dict[str, int] = defaultdict(int)
    for r in db.scalars(select(UsageLedger).where(UsageLedger.ts >= since)):
        for bucket in (by_user[r.username or "(système)"], by_agent[r.agent or "other"]):
            bucket["tokens"] += r.tokens or 0
            bucket["calls"] += 1
            bucket["blocked"] += 1 if r.status == "blocked" else 0
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        by_day[ts.date().isoformat()] += r.tokens or 0
    return {
        "days": days,
        "by_user": sorted(({"username": k, **v} for k, v in by_user.items()), key=lambda x: -x["tokens"]),
        "by_agent": sorted(({"agent": k, "label": governance.AGENTS.get(k, k), **v} for k, v in by_agent.items()), key=lambda x: -x["tokens"]),
        "by_day": [{"day": d, "tokens": by_day[d]} for d in sorted(by_day)],
    }
