"""Capacity Planning Agent — load forecast, overload alerts, scenarios, staffing proposals."""

from sqlalchemy import select

from app import llm
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics import capacity
from app.models import BacklogItem, TeamMember, Ticket, Timesheet

NAME = "capacity"


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    pid = ctx.perimeter_id
    members = db.scalars(select(TeamMember).where(TeamMember.perimeter_id == pid)).all()
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == pid)).all()
    sheets = db.scalars(select(Timesheet).where(Timesheet.perimeter_id == pid)).all()
    backlog = db.scalars(select(BacklogItem).where(BacklogItem.perimeter_id == pid)).all()

    result = capacity.analyze(members, tickets, sheets, backlog, horizon_weeks=ctx.params.get("horizon_weeks", 8), scenario=ctx.params.get("scenario"))
    recs = []

    overloads = [w for w in result["forecast"] if w["status"] == "overload"]
    if overloads:
        peak = max(overloads, key=lambda w: w["utilization_pct"])
        recs.append(
            recommend(ctx, NAME, "capacity_alert", f"Surcharge prévue sur {len(overloads)} semaine(s) — pic {peak['utilization_pct']} % semaine du {peak['week']}",
                      f"Écart cumulé {result['capacity_gap_h']} h (≈ {result['fte_gap']} ETP en moyenne sur les semaines en surcharge). Options : renfort temporaire, report de backlog non prioritaire, ou arbitrage client sur les priorités.",
                      impact="high", confidence=0.7, payload={"weeks": overloads})
        )
    for m in result["members"]:
        if m["load_pct"] and m["load_pct"] > 120:
            recs.append(recommend(ctx, NAME, "capacity_alert", f"{m['name']} en surcharge ({m['load_pct']} %)", f"{m['assigned_h']} h affectées pour {m['capacity_2w_h']} h disponibles sur 2 semaines. Réaffecter une partie de ses tickets.", impact="medium", confidence=0.8, payload=m))
    at_risk = [c for c in result["skill_coverage"] if c["at_risk"]]
    if at_risk:
        uncovered = [c for c in at_risk if not c["holders"]]
        recs.append(recommend(ctx, NAME, "knowledge", f"{len(at_risk)} compétence(s) requise(s) détenue(s) par une personne au plus",
                              "\n".join(f"• {c['skill']} : {', '.join(c['holders']) or 'aucun détenteur'}" for c in at_risk) + "\nPlanifier du binômage ou une montée en compétence.",
                              impact="high" if uncovered else "medium", confidence=0.85, payload={"skills": at_risk}))

    assignable = [p for p in result["assignment_proposals"] if p["member_id"]]
    if assignable:
        recs.append(
            recommend(ctx, NAME, "staffing", f"Proposition d'affectation de {len(assignable)} élément(s) du backlog",
                      "\n".join(f"• {p['title']} → {p['member']} ({p['reason']})" for p in assignable), impact="high", confidence=0.75, payload={"assignments": assignable})
        )

    def fallback() -> str:
        util = [w["utilization_pct"] or 0 for w in result["forecast"]]
        return (
            f"Utilisation prévue de {min(util):.0f} % à {max(util):.0f} % sur {len(util)} semaines. "
            f"{len(overloads)} semaine(s) en surcharge, écart de {result['capacity_gap_h']} h. "
            f"{sum(1 for c in result['skill_coverage'] if c['at_risk'])} compétence(s) détenue(s) par une seule personne."
        )

    summary, used = llm.text(purpose="capacity_summary", 
        context=f"Forecast: {result['forecast']}\nMembers: {result['members']}\nSkill coverage: {result['skill_coverage']}\nScenario: {result['scenario']}",
        task="Summarise the capacity outlook in 4 lines for a Delivery Manager and give the single most important staffing recommendation. Do not recompute figures.",
        fallback=fallback,
    )
    return AgentOutput(summary=summary, data=result, recommendations=recs, llm_used=used)
