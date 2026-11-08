"""Risk Agent — the "watchdog". It reads everything available for the perimeter (KPIs, incidents,
scope, capacity, and the upstream agents' findings when orchestrated) and concludes a single,
prioritised risk register: what could go wrong, how likely, how bad, and what to do about it.

It is deliberately a *consolidator*: the heavy analytics live in app.analytics and are reused here so
the four public agents stay lean. The register is what the user "trains" over time via the params
(extra watch-items) — see `EXTRA_WATCH`.
"""

import json

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import llm, skills
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics import capacity as capacity_an
from app.analytics.incidents import open_duplicates, recurring_clusters
from app.analytics.kpi import service_kpis
from app.analytics.scope import is_out_of_scope
from app.models import BacklogItem, Perimeter, Recommendation, TeamMember, Ticket, Timesheet

NAME = "risk"

# Severity ordering so we can pick the worst.
_SEV = ["low", "medium", "high"]


class RiskItem(BaseModel):
    title: str = Field(description="short risk statement")
    category: str = Field(description="one of: delivery, contract, capacity, quality, financial, security")
    severity: str = Field(description="high, medium or low")
    likelihood: str = Field(description="high, medium or low")
    rationale: str = Field(description="why this is a risk, grounded in the figures")
    mitigation: str = Field(description="concrete next action to reduce it")


class RiskReport(BaseModel):
    headline: str = Field(description="one-sentence overall risk posture")
    risks: list[RiskItem]


def _collect_signals(ctx: AgentContext) -> dict:
    """Deterministic risk signals mined from the perimeter data (no LLM)."""
    db, pid = ctx.db, ctx.perimeter_id
    perimeter = db.get(Perimeter, pid)
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == pid)).all()
    members = db.scalars(select(TeamMember).where(TeamMember.perimeter_id == pid)).all()
    sheets = db.scalars(select(Timesheet).where(Timesheet.perimeter_id == pid)).all()
    backlog = db.scalars(select(BacklogItem).where(BacklogItem.perimeter_id == pid)).all()

    kpis = service_kpis(tickets, perimeter.contract)
    cap = capacity_an.analyze(members, tickets, sheets, backlog, horizon_weeks=ctx.params.get("horizon_weeks", 8))
    clusters = [c for c in recurring_clusters(tickets) if c["count"] >= 5]
    dups = open_duplicates(tickets)
    oos = [{"ref": t.ref, "title": t.title, "terms": terms} for t in tickets
           if (flag := is_out_of_scope(t, perimeter.contract))[0] and t.kind in ("request", "change", "incident")
           for terms in [flag[1]]]
    overloads = [w for w in cap["forecast"] if w["status"] == "overload"]
    single_point = [c for c in cap["skill_coverage"] if c["at_risk"]]
    return {
        "perimeter": perimeter, "kpis": kpis, "capacity": cap, "clusters": clusters,
        "duplicates": dups, "out_of_scope": oos, "overloads": overloads, "single_point": single_point,
    }


def _deterministic_risks(sig: dict) -> list[RiskItem]:
    """A baseline register from the signals, used as the LLM fallback and as a floor of certainty."""
    risks: list[RiskItem] = []
    kpis = sig["kpis"]
    for s in kpis["scorecard"]:
        if s["status"] == "red":
            risks.append(RiskItem(title=f"KPI hors cible : {s['kpi']}", category="quality", severity="high", likelihood="high",
                                  rationale=f"{s['kpi']} à {s['value']}{s['unit']} pour une cible de {s['target']}{s['unit']}.",
                                  mitigation="Plan de remédiation à présenter au prochain COPIL."))
    for prio, c in kpis["sla_by_priority_last_month"].items():
        if not c["met"]:
            risks.append(RiskItem(title=f"SLA {prio} non tenu", category="contract", severity="high", likelihood="high",
                                  rationale=f"{c['pct']} % de respect pour une cible de {c['target_pct']} % — risque de pénalité contractuelle.",
                                  mitigation="Analyser les tickets hors délai et sécuriser le mois en cours."))
    for c in sig["clusters"][:3]:
        risks.append(RiskItem(title=f"Incidents récurrents : {c['label']}", category="delivery", severity="high" if c["p1p2"] >= 2 else "medium", likelihood="high",
                              rationale=f"{c['count']} incidents similaires ({c['p1p2']} P1/P2) sur 90 jours.",
                              mitigation="Ouvrir un problème et lancer une analyse de cause racine."))
    if sig["out_of_scope"]:
        risks.append(RiskItem(title=f"{len(sig['out_of_scope'])} demande(s) hors périmètre", category="contract", severity="high", likelihood="medium",
                              rationale="Des travaux hors contrat risquent d'être absorbés dans le forfait.",
                              mitigation="Qualifier avec le client : devis séparé ou avenant."))
    if sig["overloads"]:
        peak = max(sig["overloads"], key=lambda w: w["utilization_pct"])
        risks.append(RiskItem(title=f"Surcharge prévue ({len(sig['overloads'])} semaine(s))", category="capacity", severity="high", likelihood="medium",
                              rationale=f"Pic d'utilisation à {peak['utilization_pct']} % (semaine du {peak['week']}), écart de {sig['capacity']['capacity_gap_h']} h.",
                              mitigation="Renfort temporaire, report de backlog non prioritaire ou arbitrage des priorités."))
    uncovered = [c for c in sig["single_point"] if not c["holders"]]
    if sig["single_point"]:
        risks.append(RiskItem(title=f"{len(sig['single_point'])} compétence(s) à point unique de défaillance", category="capacity", severity="high" if uncovered else "medium", likelihood="medium",
                              rationale="Compétence détenue par une personne au plus — risque en cas d'absence ou de départ.",
                              mitigation="Binômage et montée en compétence planifiés."))
    return risks


def assess(ctx: AgentContext) -> tuple[RiskReport, dict, bool]:
    """Core risk assessment, reusable by the /api risk endpoint outside a full workflow."""
    sig = _collect_signals(ctx)
    baseline = _deterministic_risks(sig)
    perimeter = sig["perimeter"]
    kpis = sig["kpis"]

    # Consume upstream agents' STRUCTURED data (not just their prose summary) — e.g. billing's actual
    # figures/anomalies flow into the risk reasoning, so the agents genuinely swap data.
    def _agent_block(n: str, r: dict) -> str:
        block = f"\n<agent name=\"{n}\">{r.get('summary')}"
        data = r.get("data") or {}
        if data:
            block += "\n<data>" + json.dumps(data, ensure_ascii=False, default=str)[:800] + "</data>"
        return block + "</agent>"

    upstream_txt = "".join(_agent_block(n, r) for n, r in ctx.upstream.items())
    watch = ctx.params.get("watch_items") or []  # user-trained extra items to always check
    watch_txt = ("\nWatch-items définis par l'utilisateur : " + "; ".join(watch)) if watch else ""
    sk = skills.prompt_block(ctx.db, NAME, ctx.perimeter_id)
    context = (
        f"{sk}\nPerimeter: {perimeter.name}\nScorecard: {kpis['scorecard']}\n"
        f"SLA last month: {kpis['sla_by_priority_last_month']}\n"
        f"Backlog: open={kpis['backlog_open']} over30d={kpis['backlog_over_30d']}\n"
        f"Recurring clusters: {[{'label': c['label'], 'count': c['count'], 'p1p2': c['p1p2']} for c in sig['clusters'][:5]]}\n"
        f"Out of scope: {sig['out_of_scope'][:8]}\n"
        f"Capacity overloads: {len(sig['overloads'])}, single-point skills: {len(sig['single_point'])}\n"
        f"Baseline register: {[r.model_dump() for r in baseline]}"
        + upstream_txt + watch_txt
    )

    def fallback() -> RiskReport:
        worst = max((r.severity for r in baseline), key=_SEV.index, default="low")
        headline = {"high": "Plusieurs risques élevés à traiter en priorité.",
                    "medium": "Risques modérés sous surveillance.",
                    "low": "Aucun risque majeur détecté."}[worst if baseline else "low"]
        return RiskReport(headline=headline, risks=baseline)

    report, used = llm.structured(
        schema=RiskReport, context=context,
        task="En tant qu'agent de risque, consolide un registre des risques priorisé pour ce périmètre : "
             "reprends les signaux fournis, ajoute les risques implicites, et pour chacun donne sévérité, "
             "probabilité, justification chiffrée et action de mitigation concrète. Traite en priorité les watch-items.",
        fallback=fallback, tier="reasoning", effort="high",
    )
    return report, sig, used


def run(ctx: AgentContext) -> AgentOutput:
    report, sig, used = assess(ctx)
    recs = []
    for r in report.risks:
        impact = r.severity if r.severity in _SEV else "medium"
        recs.append(recommend(ctx, NAME, "risk", f"[{r.category}] {r.title}",
                              f"{r.rationale}\n\nProbabilité : {r.likelihood}. Mitigation : {r.mitigation}",
                              impact=impact, confidence=0.75, payload=r.model_dump()))
    recs.append(recommend(ctx, NAME, "risk_report", f"Registre des risques — {sig['perimeter'].name}", report.headline,
                          impact="medium", confidence=0.8, payload={"headline": report.headline, "risks": [r.model_dump() for r in report.risks]}))
    return AgentOutput(summary=report.headline, data={"headline": report.headline, "risks": [r.model_dump() for r in report.risks],
                                                       "signals": {"overloads": len(sig["overloads"]), "clusters": len(sig["clusters"]), "out_of_scope": len(sig["out_of_scope"])}},
                       recommendations=recs, llm_used=used)
