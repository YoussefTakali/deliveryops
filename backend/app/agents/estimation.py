"""Estimation Agent — sizes a change request from the actual effort of similar past changes."""

from collections import defaultdict

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import knowledge, llm
from app.context import scope_perimeter_ids
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics.incidents import estimate_from_history
from app.analytics.scope import is_out_of_scope_text
from app.models import Perimeter, Ticket, Timesheet

NAME = "estimation"


class Estimate(BaseModel):
    p50_h: float
    p80_h: float
    work_unit: str = Field(description="work unit code from the catalog that fits best, or 'régie'")
    assumptions: list[str]
    risks: list[str]
    rationale: str


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    request = ctx.params["request"]
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == ctx.perimeter_id)).all()
    hours = defaultdict(float)
    for s in db.scalars(select(Timesheet).where(Timesheet.perimeter_id == ctx.perimeter_id, Timesheet.ticket_ref.is_not(None))):
        hours[s.ticket_ref] += s.hours

    for t in tickets:  # changes older than the timesheet history fall back to their recorded effort
        if t.kind == "change" and t.ref not in hours and t.estimate_h:
            hours[t.ref] = t.estimate_h
    stats = estimate_from_history(request, tickets, hours)
    hits = knowledge.search(db, scope_perimeter_ids(db, perimeter), request, k=4)
    catalog = perimeter.contract.get("work_units", {})
    oos, terms = is_out_of_scope_text(request, perimeter.contract)

    def pick_uo(h: float) -> str:
        fitting = sorted(((code, uo) for code, uo in catalog.items() if uo.get("max_h", 0) >= h), key=lambda x: x[1]["max_h"])
        return fitting[0][0] if fitting else "régie"

    def fallback() -> Estimate:
        p50 = stats["p50_h"] or 21.0
        p80 = stats["p80_h"] or p50 * 1.5
        return Estimate(
            p50_h=p50, p80_h=p80, work_unit=pick_uo(p80),
            assumptions=["Environnements de recette disponibles", "Spécifications validées avant démarrage", f"Base : {stats['basis']}"],
            risks=[f"Hors périmètre : {', '.join(terms)}"] if oos else ["Impacts de non-régression sur les applications dépendantes"],
            rationale=f"Estimation fondée sur {stats['basis']} (P50 {p50} h, P80 {p80} h).",
        )

    est, used = llm.structured(
        schema=Estimate,
        context=f"Historical similar changes (actual hours): {stats}\nWork unit catalog: {catalog}\nOut-of-scope terms detected: {terms}\n\n{knowledge.format_context(hits)}",
        task=f"Estimate the effort for this change request: « {request} ». Anchor on the historical actuals, adjust for complexity visible in the documentation, pick the best-fitting work unit.",
        fallback=fallback,
    )
    rec = recommend(ctx, NAME, "estimate", f"Chiffrage : {request[:100]} — {est.p50_h:.0f} à {est.p80_h:.0f} h ({est.work_unit})", est.rationale, impact="medium", confidence=0.8 if stats["similar"] else 0.5,
                    sources=[{"type": "ticket", "ref": s["ref"], "label": f"{s['ref']} — {s['title']} ({s['hours']} h)", "excerpt": ""} for s in stats["similar"]] + [h.as_source() for h in hits[:2]],
                    payload={**est.model_dump(), "history": stats, "out_of_scope": oos})
    return AgentOutput(summary=est.rationale, data={"estimate": est.model_dump(), "history": stats, "out_of_scope": oos, "out_of_scope_terms": terms}, recommendations=[rec], llm_used=used)
