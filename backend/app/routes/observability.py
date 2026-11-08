"""AI observability: model usage and cost, plus recommendation acceptance as a quality signal."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import llm_config
from app.api import visible_perimeters
from app.auth import User, require_permission
from app.config import get_settings
from app.db import get_db
from app.models import AgentRun, LLMCall, Recommendation, UsageLedger

router = APIRouter(prefix="/api")
settings = get_settings()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/observability")
def observability(days: int = 30, user: User = Depends(require_permission("observability.view")), db: Session = Depends(get_db)):
    allowed = [p.id for p in visible_perimeters(db, user)]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    calls = db.scalars(select(LLMCall).where(LLMCall.ts >= since, or_(LLMCall.perimeter_id.in_(allowed), LLMCall.perimeter_id.is_(None)))).all()

    by_agent: dict[str, dict] = defaultdict(lambda: {"calls": 0, "llm_calls": 0, "fallbacks": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0, "latency_ms": []})
    by_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0})
    for c in calls:
        a = by_agent[c.agent or "other"]
        a["calls"] += 1
        a["llm_calls"] += int(c.model != "offline")
        a["fallbacks"] += int(c.status == "fallback")
        a["errors"] += int(c.status in ("error", "refusal"))
        a["input_tokens"] += c.input_tokens
        a["output_tokens"] += c.output_tokens
        a["cache_read_tokens"] += c.cache_read_tokens
        a["cost_usd"] += c.cost_usd
        if c.model != "offline":
            a["latency_ms"].append(c.latency_ms)
        d = by_day[c.ts.strftime("%Y-%m-%d")]
        d["calls"] += 1
        d["cost_usd"] += c.cost_usd

    agents = []
    for name, a in sorted(by_agent.items()):
        lat = sorted(a.pop("latency_ms"))
        total_in = a["input_tokens"] + a["cache_read_tokens"]
        agents.append({
            "agent": name, **a, "cost_usd": round(a["cost_usd"], 4),
            "p50_latency_ms": lat[len(lat) // 2] if lat else None,
            "cache_hit_pct": round(100 * a["cache_read_tokens"] / total_in, 1) if total_in else None,
        })

    recs = db.scalars(select(Recommendation).where(Recommendation.perimeter_id.in_(allowed), Recommendation.created_at >= since)).all()
    quality: dict[str, dict] = defaultdict(lambda: {"total": 0, "approved": 0, "rejected": 0, "pending": 0, "superseded": 0, "info": 0})
    reasons: dict[str, list] = defaultdict(list)
    for r in recs:
        q = quality[r.agent]
        q["total"] += 1
        q[r.status] = q.get(r.status, 0) + 1
        if r.status == "rejected" and r.decision_comment:
            reasons[r.agent].append({"title": r.title, "comment": r.decision_comment})
    acceptance = []
    for agent, q in sorted(quality.items()):
        decided = q["approved"] + q["rejected"]
        acceptance.append({"agent": agent, **q, "acceptance_pct": round(100 * q["approved"] / decided, 1) if decided else None, "rejections": reasons[agent][-5:]})

    models: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0, "errors": 0})
    for c in calls:
        m = models[c.model or "?"]
        m["calls"] += 1
        m["tokens"] += (c.input_tokens or 0) + (c.output_tokens or 0)
        m["cost_usd"] += c.cost_usd or 0.0
        m["errors"] += int(c.status in ("error", "refusal"))
    by_model = sorted(({"model": k, **v, "cost_usd": round(v["cost_usd"], 4)} for k, v in models.items()), key=lambda x: -x["tokens"])
    # Per-user consumption comes from the governance ledger (LLMCall has no user column).
    users: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "blocked": 0})
    for r in db.scalars(select(UsageLedger).where(UsageLedger.ts >= since)):
        u = users[r.username or "(système)"]
        u["calls"] += 1
        u["tokens"] += r.tokens or 0
        u["blocked"] += int(r.status == "blocked")
    by_user = sorted(({"username": k, **v} for k, v in users.items()), key=lambda x: -x["tokens"])

    runs = db.scalars(select(AgentRun).where(AgentRun.perimeter_id.in_(allowed), AgentRun.started_at >= since)).all()
    durations = [(_aware(r.ended_at) - _aware(r.started_at)).total_seconds() for r in runs if r.ended_at and r.status == "completed"]
    return {
        "mode": "api" if settings.use_llm else "offline", "base_url": llm_config.resolve().base_url,
        "models": {"reasoning": settings.llm_model_reasoning, "agent": settings.llm_model_agent, "fast": settings.llm_model_fast},
        "days": days,
        "totals": {
            "calls": len(calls),
            "llm_calls": sum(1 for c in calls if c.model != "offline"),
            "cost_usd": round(sum(c.cost_usd for c in calls), 4),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "runs": len(runs),
            "runs_failed": sum(1 for r in runs if r.status == "failed"),
            "avg_run_s": round(sum(durations) / len(durations), 1) if durations else None,
        },
        "by_agent": agents,
        "by_day": [{"day": k, "calls": v["calls"], "cost_usd": round(v["cost_usd"], 4)} for k, v in sorted(by_day.items())],
        "acceptance": acceptance,
        "by_model": by_model,
        "by_user": by_user,
    }
