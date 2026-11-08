"""Pre-billing engine: consolidates timesheets and tickets, applies contract rules, detects gaps.
Pure functions over plain data so they are easy to test and audit."""

from collections import defaultdict
from datetime import date, datetime, timezone

from app.analytics.kpi import sla_compliance
from app.analytics.scope import is_out_of_scope
from app.models import TeamMember, Ticket, Timesheet


def _in_month(d: date | datetime | None, year: int, month: int) -> bool:
    return d is not None and d.year == year and d.month == month


def working_days(year: int, month: int) -> list[date]:
    from calendar import monthrange

    return [date(year, month, d) for d in range(1, monthrange(year, month)[1] + 1) if date(year, month, d).weekday() < 5]


def prebill(
    contract: dict,
    members: list[TeamMember],
    tickets: list[Ticket],
    timesheets: list[Timesheet],
    year: int,
    month: int,
) -> dict:
    currency = contract.get("currency", "EUR")
    hours_per_day = contract.get("hours_per_day", 7)
    rate_card = contract.get("rate_card", {})
    codes = contract.get("billable_activity_codes", {})
    non_billable = set(contract.get("non_billable_codes", []))
    work_units = contract.get("work_units", {})

    member_by_id = {m.id: m for m in members}
    ticket_by_ref = {t.ref: t for t in tickets}
    sheets = [s for s in timesheets if _in_month(s.day, year, month)]

    lines: list[dict] = []
    anomalies: list[dict] = []
    hours_by_code: dict[str, float] = defaultdict(float)

    def anomaly(kind, severity, message, refs=None, amount=None):
        anomalies.append({"kind": kind, "severity": severity, "message": message, "refs": refs or [], "amount": amount})

    # ---- timesheet hygiene ----
    per_member_day: dict[tuple[str, date], float] = defaultdict(float)
    for s in sheets:
        per_member_day[(s.member_id, s.day)] += s.hours
        hours_by_code[s.activity_code] += s.hours
        if s.activity_code not in codes and s.activity_code not in non_billable:
            anomaly("unknown_activity_code", "medium", f"Code activité inconnu « {s.activity_code} » ({s.hours} h)", [s.id])
        if s.ticket_ref and s.ticket_ref not in ticket_by_ref:
            anomaly("unknown_ticket", "medium", f"Temps saisi sur un ticket inexistant {s.ticket_ref} ({s.hours} h)", [s.ticket_ref])
        t = ticket_by_ref.get(s.ticket_ref) if s.ticket_ref else None
        if t and s.activity_code == "EVOL" and t.kind == "incident":
            anomaly("misclassified_activity", "medium", f"{s.hours} h en EVOL (régie) sur l'incident {t.ref} — relève normalement du forfait RUN", [t.ref], amount=None)
        if t and codes.get(s.activity_code) == "time_material" and t.category.startswith("UO-"):
            anomaly("double_billing", "high", f"{s.hours} h en régie sur {t.ref}, déjà facturé en unité d'œuvre {t.category} — risque de double facturation", [t.ref])
        if s.activity_code == "EVOL" and not s.ticket_ref:
            anomaly("unjustified_time_material", "high", f"{s.hours} h de régie sans ticket justificatif ({member_by_id.get(s.member_id).name if s.member_id in member_by_id else s.member_id}, {s.day})", [s.id])

    for (member_id, day), h in per_member_day.items():
        if h > 10:
            name = member_by_id[member_id].name if member_id in member_by_id else member_id
            anomaly("excessive_daily_hours", "medium", f"{name} : {h} h saisies le {day}", [member_id])

    days = working_days(year, month)
    for m in members:
        filled = {d for (mid, d) in per_member_day if mid == m.id}
        absent = set()
        for a in m.absences:
            start, end = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
            absent |= {d for d in days if start <= d <= end}
        missing = [d for d in days if d not in filled and d not in absent]
        if len(missing) >= 3:
            anomaly("missing_timesheets", "medium", f"{m.name} : {len(missing)} jours ouvrés sans saisie", [m.id])

    # ---- 1. fixed RUN fee ----
    fixed_fee = float(contract.get("run_fixed_monthly_fee", 0))
    run_hours = sum(h for c, h in hours_by_code.items() if codes.get(c) == "fixed")
    if fixed_fee:
        lines.append(
            {
                "section": "Forfait RUN / MCO",
                "label": f"Forfait mensuel de maintenance {year}-{month:02d}",
                "quantity": 1,
                "unit_price": fixed_fee,
                "amount": fixed_fee,
                "justification": f"{run_hours:.1f} h consommées sur le forfait",
                "refs": [],
            }
        )

    # ---- 2. time & material (régie) ----
    tm_hours: dict[str, float] = defaultdict(float)
    tm_refs: dict[str, set] = defaultdict(set)
    for s in sheets:
        if codes.get(s.activity_code) == "time_material" and s.ticket_ref in ticket_by_ref and not ticket_by_ref[s.ticket_ref].category.startswith("UO-"):
            tm_hours[s.member_id] += s.hours
            tm_refs[s.member_id].add(s.ticket_ref)
    tm_total = 0.0
    for member_id, h in sorted(tm_hours.items()):
        m = member_by_id.get(member_id)
        if not m:
            continue
        rate = rate_card.get(m.profile)
        if rate is None:
            anomaly("missing_rate", "high", f"Pas de tarif contractuel pour le profil « {m.profile} » ({m.name})", [m.id])
            continue
        days_worked = round(h / hours_per_day, 2)
        amount = round(days_worked * rate, 2)
        tm_total += amount
        lines.append(
            {
                "section": "Régie (évolutions)",
                "label": f"{m.name} — profil {m.profile}",
                "quantity": days_worked,
                "unit_price": rate,
                "amount": amount,
                "justification": f"{h:.1f} h sur {len(tm_refs[member_id])} ticket(s)",
                "refs": sorted(tm_refs[member_id]),
            }
        )
    cap = contract.get("monthly_cap_time_material")
    if cap and tm_total > cap:
        excess = round(tm_total - cap, 2)
        anomaly("time_material_cap_exceeded", "high", f"Plafond mensuel de régie dépassé de {excess} {currency} — accord client requis", [], amount=excess)
        lines.append({"section": "Régie (évolutions)", "label": "Écrêtement plafond contractuel", "quantity": 1, "unit_price": -excess, "amount": -excess, "justification": f"Plafond {cap} {currency}", "refs": []})

    # ---- 3. work units (UO) ----
    for t in tickets:
        if t.kind != "change" or not _in_month(t.resolved_at, year, month) or not t.category.startswith("UO-"):
            continue
        uo = work_units.get(t.category)
        if not uo:
            anomaly("unknown_work_unit", "high", f"Unité d'œuvre {t.category} absente du catalogue ({t.ref})", [t.ref])
            continue
        spent = sum(s.hours for s in timesheets if s.ticket_ref == t.ref)
        lines.append(
            {
                "section": "Unités d'œuvre",
                "label": f"{t.category} {uo['label']} — {t.ref} {t.title}",
                "quantity": 1,
                "unit_price": uo["price"],
                "amount": uo["price"],
                "justification": f"Livré le {t.resolved_at.date()} — {spent:.1f} h consommées",
                "refs": [t.ref],
            }
        )
        if spent > uo.get("max_h", 1e9):
            anomaly("work_unit_overrun", "medium", f"{t.ref} : {spent:.1f} h consommées pour une {t.category} plafonnée à {uo['max_h']} h (perte de marge)", [t.ref])

    # ---- 4. SLA penalties ----
    pen = contract.get("penalties", {})
    resolved_in_month = [t for t in tickets if _in_month(t.resolved_at, year, month)]
    compliance = sla_compliance(resolved_in_month, contract.get("sla", {})) if contract.get("sla") else {}
    penalty_pct = 0.0
    for prio, c in compliance.items():
        if not c["met"]:
            points = c["target_pct"] - c["pct"]
            penalty_pct += points * pen.get("per_point_below_target_pct", 0)
    penalty_pct = min(penalty_pct, pen.get("cap_pct", 10))
    if penalty_pct > 0 and fixed_fee:
        penalty = round(fixed_fee * penalty_pct / 100, 2)
        lines.append({"section": "Pénalités SLA", "label": f"Pénalité SLA ({penalty_pct:.1f} % du forfait)", "quantity": 1, "unit_price": -penalty, "amount": -penalty, "justification": ", ".join(f"{p}: {c['pct']}% / {c['target_pct']}%" for p, c in compliance.items() if not c["met"]), "refs": []})
        anomaly("sla_penalty", "high", f"Pénalité SLA applicable : {penalty} {currency}", [], amount=-penalty)

    # ---- 5. out-of-scope work absorbed by the fixed fee (contract amendment opportunity) ----
    oos_hours: dict[str, float] = defaultdict(float)
    for s in sheets:
        t = ticket_by_ref.get(s.ticket_ref) if s.ticket_ref else None
        if t and codes.get(s.activity_code) == "fixed" and is_out_of_scope(t, contract)[0]:
            oos_hours[t.ref] += s.hours
    for ref, h in oos_hours.items():
        t = ticket_by_ref[ref]
        value = round(h / hours_per_day * rate_card.get("confirmed", 0), 2)
        anomaly("out_of_scope_absorbed", "high", f"{h:.1f} h hors périmètre contractuel absorbées dans le forfait sur {ref} « {t.title} » — valeur ≈ {value} {currency}", [ref], amount=value)

    total = round(sum(l["amount"] for l in lines), 2)
    return {
        "period": f"{year}-{month:02d}",
        "currency": currency,
        "lines": lines,
        "total_excl_tax": total,
        "hours_by_code": {k: round(v, 1) for k, v in sorted(hours_by_code.items())},
        "sla_compliance": compliance,
        "anomalies": anomalies,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
