from datetime import date, datetime, timezone

from app.analytics.billing import prebill
from app.models import TeamMember, Ticket, Timesheet

CONTRACT = {
    "currency": "EUR",
    "run_fixed_monthly_fee": 10000,
    "hours_per_day": 7,
    "rate_card": {"senior": 700},
    "billable_activity_codes": {"RUN": "fixed", "EVOL": "time_material", "UO": "work_unit"},
    "work_units": {"UO-S": {"label": "Petite", "price": 1000, "max_h": 14}},
    "monthly_cap_time_material": 1500,
    "sla": {"P2": {"resolution_h": 8, "target_pct": 95}},
    "penalties": {"per_point_below_target_pct": 1, "cap_pct": 10},
    "scope": {"out_of_scope": ["migration"]},
}


def _dt(d, h):
    return datetime(2026, 3, d, h, tzinfo=timezone.utc)


def build():
    m = TeamMember(id="m1", name="Ana", profile="senior", absences=[])
    tickets = [
        Ticket(id="t1", ref="CHG1", kind="change", priority="P3", category="UO-S", title="Petit ajout", opened_at=_dt(2, 9), resolved_at=_dt(10, 9)),
        Ticket(id="t2", ref="CHG2", kind="change", priority="P3", category="REGIE", title="Assistance", opened_at=_dt(2, 9), resolved_at=_dt(20, 9)),
        Ticket(id="t3", ref="INC1", kind="incident", priority="P2", category="x", title="Panne", opened_at=_dt(3, 9), resolved_at=_dt(3, 20)),  # 11 h > 8 h SLA
        Ticket(id="t4", ref="REQ1", kind="request", priority="P3", category="x", title="Migration des archives", description="", opened_at=_dt(4, 9)),
    ]
    sheets = [
        Timesheet(member_id="m1", day=date(2026, 3, 9), activity_code="UO", ticket_ref="CHG1", hours=20),  # overrun (max 14 h)
        Timesheet(member_id="m1", day=date(2026, 3, 10), activity_code="EVOL", ticket_ref="CHG2", hours=21),  # 3 days × 700 = 2100 > cap 1500
        Timesheet(member_id="m1", day=date(2026, 3, 11), activity_code="EVOL", ticket_ref="CHG1", hours=2),  # régie on a UO ticket
        Timesheet(member_id="m1", day=date(2026, 3, 12), activity_code="RUN", ticket_ref="REQ1", hours=7),  # out of scope in fixed fee
    ]
    return m, tickets, sheets


def test_prebill_amounts_and_anomalies():
    m, tickets, sheets = build()
    result = prebill(CONTRACT, [m], tickets, sheets, 2026, 3)
    kinds = {a["kind"] for a in result["anomalies"]}

    assert {"work_unit_overrun", "time_material_cap_exceeded", "double_billing", "out_of_scope_absorbed", "sla_penalty", "missing_timesheets"} <= kinds
    by_section = {}
    for line in result["lines"]:
        by_section[line["section"]] = by_section.get(line["section"], 0) + line["amount"]
    assert by_section["Forfait RUN / MCO"] == 10000
    assert by_section["Unités d'œuvre"] == 1000
    assert by_section["Régie (évolutions)"] == 1500  # capped; régie on the UO ticket is excluded
    assert by_section["Pénalités SLA"] == -1000  # 95 points below target, capped at 10 %
    assert result["total_excl_tax"] == 11500


def test_prebill_ignores_other_months():
    m, tickets, sheets = build()
    result = prebill(CONTRACT, [m], tickets, sheets, 2026, 4)
    assert [l["section"] for l in result["lines"]] == ["Forfait RUN / MCO"]
