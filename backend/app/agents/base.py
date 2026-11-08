"""Shared agent contract: each agent reads perimeter data, produces a structured result and
recommendations. Recommendations with impact=high are held for human approval."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.models import AgentRun, Recommendation


@dataclass
class AgentContext:
    db: Session
    perimeter_id: str
    run_id: str | None
    params: dict
    # the delivery project this perimeter belongs to (None if unprojected) — lets agents reason
    # project-wide (sibling perimeters, the project plan/deliverables) via app.context helpers
    project_id: str | None = None
    # results of agents that already ran in this workflow (orchestrator passes them along)
    upstream: dict[str, dict] = field(default_factory=dict)


@dataclass
class AgentOutput:
    summary: str
    data: dict[str, Any]
    recommendations: list[Recommendation] = field(default_factory=list)
    llm_used: bool = False

    def to_state(self) -> dict:
        return {
            "summary": self.summary,
            "data": self.data,
            "recommendation_ids": [r.id for r in self.recommendations],
            "llm_used": self.llm_used,
        }


def recommend(
    ctx: AgentContext,
    agent: str,
    kind: str,
    title: str,
    detail: str,
    impact: str = "medium",
    confidence: float = 0.7,
    sources: list[dict] | None = None,
    payload: dict | None = None,
) -> Recommendation:
    rec = Recommendation(
        perimeter_id=ctx.perimeter_id,
        run_id=ctx.run_id,
        agent=agent,
        kind=kind,
        title=title[:300],
        detail=detail,
        impact=impact,
        confidence=round(confidence, 2),
        sources=sources or [],
        payload=payload or {},
        # only decisions that commit the team or the client need a human; the rest is informative
        status="pending" if impact in ("high", "medium") else "info",
    )
    if ctx.params.get("read_only"):
        return rec  # exploratory runs (free questions) must not flood the approval queue
    # a newer run re-raising the same item supersedes the undecided older one
    for old in ctx.db.scalars(
        select(Recommendation).where(
            Recommendation.perimeter_id == ctx.perimeter_id, Recommendation.agent == agent, Recommendation.kind == kind,
            Recommendation.title == rec.title, Recommendation.status.in_(("pending", "info")),
        )
    ):
        old.status = "superseded"
        # a workflow still waiting on this deliverable can never resume: close it
        run = ctx.db.get(AgentRun, old.run_id) if old.run_id else None
        if run and run.id != ctx.run_id and run.status == "awaiting_approval":
            run.status = "superseded"
    ctx.db.add(rec)
    ctx.db.flush()
    return rec


def ticket_source(ticket) -> dict:
    return {"type": "ticket", "ref": ticket.ref, "label": f"{ticket.ref} — {ticket.title}", "excerpt": ticket.description[:200]}
