"""Delivery Governance Agent — scorecard, drift detection, trends, COPIL pack, action plan.
In the COPIL workflow it runs last and consolidates the other agents' results."""

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import llm
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics.kpi import service_kpis
from app.models import Application, Perimeter, Recommendation, Ticket

NAME = "governance"


class ActionPlanItem(BaseModel):
    action: str
    owner: str = Field(description="role, e.g. Delivery Manager, Lead technique, Client")
    due: str
    kpi_impacted: str


class CopilPack(BaseModel):
    headline: str = Field(description="one-sentence overall message for the steering committee")
    highlights: list[str]
    concerns: list[str]
    decisions_requested: list[str]
    action_plan: list[ActionPlanItem]


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == ctx.perimeter_id)).all()
    apps = {a.id: a.name for a in db.scalars(select(Application).where(Application.perimeter_id == ctx.perimeter_id))}
    kpis = service_kpis(tickets, perimeter.contract)
    kpis["top_applications"] = [{"application": apps.get(a, a), "incidents": n} for a, n in kpis["top_applications"]]
    recs = []

    # ---- drift detection ----
    for s in kpis["scorecard"]:
        if s["status"] == "red":
            recs.append(recommend(ctx, NAME, "drift", f"Dérive : {s['kpi']} à {s['value']}{s['unit']} (cible {s['target']}{s['unit']})", "Indicateur hors tolérance contractuelle. Analyser les causes et présenter un plan de remédiation au prochain COPIL.", impact="high", confidence=0.95, payload=s))
    for prio, c in kpis["sla_by_priority_last_month"].items():
        if not c["met"]:
            recs.append(recommend(ctx, NAME, "sla", f"SLA {prio} non tenu : {c['pct']} % (cible {c['target_pct']} %)", f"{c['resolved'] - c['within_sla']} ticket(s) {prio} hors délai sur {c['resolved']}. Risque de pénalité contractuelle.", impact="high", confidence=0.95, payload=c))
    if kpis["incident_trend_slope"] > 2:
        recs.append(recommend(ctx, NAME, "trend", f"Volume d'incidents en hausse (+{kpis['incident_trend_slope']}/mois)", "Tendance haussière sur 6 mois. Vérifier les mises en production récentes et les incidents récurrents.", impact="medium", confidence=0.7))
    if kpis["sla_trend_slope"] < -1:
        recs.append(recommend(ctx, NAME, "trend", f"Respect des SLA en baisse ({kpis['sla_trend_slope']} pt/mois)", "Dégradation progressive : anticiper avant le passage sous le seuil contractuel.", impact="medium", confidence=0.7))

    # ---- COPIL pack (uses upstream agents when orchestrated) ----
    upstream = ctx.upstream
    open_high = db.scalars(select(Recommendation).where(Recommendation.perimeter_id == ctx.perimeter_id, Recommendation.impact == "high", Recommendation.status == "pending")).all()
    context = (
        f"Perimeter: {perimeter.name}\nScorecard: {kpis['scorecard']}\nMonthly trend: {kpis['monthly']}\n"
        f"SLA last month: {kpis['sla_by_priority_last_month']}\nBacklog: open={kpis['backlog_open']} over30d={kpis['backlog_over_30d']}\n"
        f"Top incident applications: {kpis['top_applications']}\n"
        + "".join(f"\n{name} agent summary: {res.get('summary')}" for name, res in upstream.items())
        + "\nOpen high-impact items: " + "; ".join(r.title for r in open_high[:15])
    )

    def fallback() -> CopilPack:
        sc = {s["kpi"]: s for s in kpis["scorecard"]}
        greens = [f"{s['kpi']} : {s['value']}{s['unit']} (cible {s['target']}{s['unit']})" for s in kpis["scorecard"] if s["status"] == "green"]
        reds = [f"{s['kpi']} : {s['value']}{s['unit']} (cible {s['target']}{s['unit']})" for s in kpis["scorecard"] if s["status"] != "green"]
        return CopilPack(
            headline={"green": "Service maîtrisé, indicateurs dans les cibles.", "amber": "Service sous tension : points de vigilance à arbitrer.", "red": "Service en dérive : plan de remédiation requis."}[kpis["overall_status"]],
            highlights=greens or ["Continuité de service assurée"],
            concerns=reds + [r.title for r in open_high[:4]],
            decisions_requested=[r.title for r in open_high if r.kind in ("scope", "staffing", "capacity_alert", "prebilling")][:4],
            action_plan=[ActionPlanItem(action=f"Plan de remédiation « {s['kpi']} »", owner="Delivery Manager", due="COPIL suivant", kpi_impacted=s["kpi"]) for s in kpis["scorecard"] if s["status"] == "red"]
            + [ActionPlanItem(action=r.title, owner="Lead technique", due="30 jours", kpi_impacted="Incidents") for r in open_high if r.kind == "problem"][:3],
        )

    pack, used = llm.structured(schema=CopilPack, context=context, task="Prepare the steering committee (COPIL) pack for the client: headline, highlights, concerns, decisions requested from the client, and a concrete action plan. Be factual, reuse the figures as given.", fallback=fallback, tier="reasoning", effort="high")
    recs.append(recommend(ctx, NAME, "copil", f"Support COPIL — {perimeter.name}", pack.headline, impact="high", confidence=0.8, payload=pack.model_dump()))

    return AgentOutput(summary=pack.headline, data={"kpis": kpis, "copil": pack.model_dump()}, recommendations=recs, llm_used=used)
