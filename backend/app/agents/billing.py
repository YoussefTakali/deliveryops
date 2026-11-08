"""Billing Agent — pre-billing and contractual compliance. Amounts come from the rules engine;
the LLM only writes the cover note."""

from datetime import date

from sqlalchemy import select

from app import llm
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics.billing import prebill
from app.models import Perimeter, TeamMember, Ticket, Timesheet

NAME = "billing"


def previous_month(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    year, month = ctx.params.get("year"), ctx.params.get("month")
    if not (year and month):
        year, month = previous_month()

    members = db.scalars(select(TeamMember).where(TeamMember.perimeter_id == ctx.perimeter_id)).all()
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == ctx.perimeter_id)).all()
    sheets = db.scalars(select(Timesheet).where(Timesheet.perimeter_id == ctx.perimeter_id)).all()

    result = prebill(perimeter.contract, members, tickets, sheets, year, month)
    cur = result["currency"]
    recs = []

    by_kind: dict[str, list[dict]] = {}
    for a in result["anomalies"]:
        by_kind.setdefault(a["kind"], []).append(a)
    labels = {
        "out_of_scope_absorbed": ("scope", "Travaux hors périmètre absorbés par le forfait"),
        "time_material_cap_exceeded": ("billing", "Plafond de régie dépassé"),
        "sla_penalty": ("billing", "Pénalité SLA applicable"),
        "unjustified_time_material": ("billing", "Régie sans justificatif"),
        "misclassified_activity": ("billing", "Temps mal imputé (régie sur incident)"),
        "missing_timesheets": ("billing", "Saisies de temps manquantes"),
        "work_unit_overrun": ("margin", "Dépassement d'unités d'œuvre"),
        "double_billing": ("billing", "Risque de double facturation (régie sur UO)"),
        "excessive_daily_hours": ("billing", "Saisies journalières excessives"),
        "unknown_activity_code": ("billing", "Codes activité inconnus"),
        "unknown_ticket": ("billing", "Temps saisi sur des tickets inexistants"),
        "missing_rate": ("billing", "Profils sans tarif contractuel"),
        "unknown_work_unit": ("billing", "Unités d'œuvre hors catalogue"),
    }
    for kind, items in by_kind.items():
        rec_kind, title = labels.get(kind, ("billing", kind.replace("_", " ").capitalize()))
        severity = max((i["severity"] for i in items), key=["low", "medium", "high"].index)
        amount = sum(i["amount"] or 0 for i in items)
        detail = "\n".join(f"• {i['message']}" for i in items)
        recs.append(
            recommend(ctx, NAME, rec_kind, f"{title} ({len(items)})" + (f" — {amount:,.0f} {cur}" if amount else ""), detail, impact=severity, confidence=0.95,
                      sources=[{"type": "ticket", "ref": r, "label": r, "excerpt": ""} for i in items for r in i["refs"] if r.startswith(("INC", "CHG", "REQ"))][:10],
                      payload={"anomalies": items})
        )

    def note_fallback() -> str:
        return (
            f"Pré-facturation {result['period']} — {perimeter.name} : total HT {result['total_excl_tax']:,.2f} {cur} "
            f"sur {len(result['lines'])} ligne(s). {len(result['anomalies'])} écart(s) détecté(s), "
            f"dont {sum(1 for a in result['anomalies'] if a['severity'] == 'high')} bloquant(s) à arbitrer avant émission."
        )

    note, used = llm.text(purpose="billing_cover_note", 
        context=f"Pre-billing lines: {result['lines']}\nAnomalies: {result['anomalies']}\nSLA compliance: {result['sla_compliance']}",
        task="Write a short cover note (max 6 lines, French) for the Delivery Manager validating this pre-invoice: total, main items, blocking anomalies and what must be decided before sending it to the client. Do not recompute amounts.",
        fallback=note_fallback,
    )
    recs.insert(
        0,
        recommend(ctx, NAME, "prebilling", f"Valider la pré-facture {result['period']} — {result['total_excl_tax']:,.2f} {cur}", note, impact="high", confidence=0.9, payload={"period": result["period"], "total": result["total_excl_tax"]}),
    )
    return AgentOutput(summary=note, data=result, recommendations=recs, llm_used=used)
