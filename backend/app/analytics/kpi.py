"""Deterministic service KPIs. Numbers shown to a client must never come from an LLM."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from app.models import Ticket

DEFAULT_SLA = {
    "P1": {"resolution_h": 4, "target_pct": 95},
    "P2": {"resolution_h": 8, "target_pct": 95},
    "P3": {"resolution_h": 40, "target_pct": 90},
    "P4": {"resolution_h": 80, "target_pct": 90},
}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def resolution_hours(t: Ticket) -> float | None:
    if not t.resolved_at:
        return None
    return (_aware(t.resolved_at) - _aware(t.opened_at)).total_seconds() / 3600


def sla_breached(t: Ticket, sla: dict) -> bool | None:
    rule = sla.get(t.priority)
    hours = resolution_hours(t)
    if not rule or hours is None:
        return None
    return hours > rule["resolution_h"]


def sla_compliance(tickets: list[Ticket], sla: dict) -> dict[str, dict]:
    """Compliance per priority for resolved incidents."""
    out = {}
    for prio, rule in sla.items():
        scoped = [t for t in tickets if t.kind == "incident" and t.priority == prio and t.resolved_at]
        if not scoped:
            continue
        ok = sum(1 for t in scoped if not sla_breached(t, sla))
        pct = round(100 * ok / len(scoped), 1)
        out[prio] = {"resolved": len(scoped), "within_sla": ok, "pct": pct, "target_pct": rule["target_pct"], "met": pct >= rule["target_pct"]}
    return out


def linear_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xs = range(n)
    mx, my = (n - 1) / 2, mean(values)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, values)) / den if den else 0.0


def service_kpis(tickets: list[Ticket], contract: dict, now: datetime | None = None) -> dict:
    now = _aware(now) or datetime.now(timezone.utc)
    sla = contract.get("sla") or DEFAULT_SLA
    targets = contract.get("kpi_targets", {})

    incidents = [t for t in tickets if t.kind == "incident"]
    months = sorted({month_key(_aware(t.opened_at)) for t in tickets})[-6:]

    monthly = []
    for m in months:
        opened = [t for t in incidents if month_key(_aware(t.opened_at)) == m]
        resolved = [t for t in incidents if t.resolved_at and month_key(_aware(t.resolved_at)) == m]
        comp = sla_compliance(resolved, sla)
        total = sum(c["resolved"] for c in comp.values())
        within = sum(c["within_sla"] for c in comp.values())
        mttr = [resolution_hours(t) for t in resolved if t.priority in ("P1", "P2")]
        monthly.append(
            {
                "month": m,
                "incidents_opened": len(opened),
                "incidents_resolved": len(resolved),
                "sla_pct": round(100 * within / total, 1) if total else None,
                "mttr_p1p2_h": round(mean(mttr), 1) if mttr else None,
                "p1_count": sum(1 for t in opened if t.priority == "P1"),
            }
        )

    open_items = [t for t in tickets if t.status in ("open", "in_progress")]
    ages = [(now - _aware(t.opened_at)).days for t in open_items]
    changes = [t for t in tickets if t.kind == "change" and t.resolved_at]
    failed = sum(1 for t in changes if t.failed_change)
    resolved_all = [t for t in tickets if t.resolved_at]
    reopened = sum(1 for t in resolved_all if t.reopened)

    # the current month is still open: report on the last complete month
    ref_month = month_key(now.replace(day=1) - timedelta(days=1))
    current = next((m for m in monthly if m["month"] == ref_month), monthly[-1] if monthly else {})
    kpis = {
        "reference_month": ref_month,
        "sla_by_priority_last_month": sla_compliance(
            [t for t in incidents if t.resolved_at and month_key(_aware(t.resolved_at)) == ref_month], sla
        ),
        "backlog_open": len(open_items),
        "backlog_over_30d": sum(1 for a in ages if a > 30),
        "backlog_avg_age_d": round(mean(ages), 1) if ages else 0,
        "change_failure_rate_pct": round(100 * failed / len(changes), 1) if changes else 0,
        "reopen_rate_pct": round(100 * reopened / len(resolved_all), 1) if resolved_all else 0,
        "incident_trend_slope": round(linear_slope([m["incidents_opened"] for m in monthly]), 2),
        "sla_trend_slope": round(linear_slope([m["sla_pct"] for m in monthly if m["sla_pct"] is not None]), 2),
        "monthly": monthly,
        "top_applications": Counter(t.application_id for t in incidents if t.application_id).most_common(5),
    }

    # RAG status per indicator vs contract targets
    def rag(value, target, higher_is_better):
        if value is None or target is None:
            return "grey"
        if higher_is_better:
            return "green" if value >= target else ("amber" if value >= target - 5 else "red")
        return "green" if value <= target else ("amber" if value <= target * 1.25 else "red")

    sla_target = min((r["target_pct"] for r in sla.values()), default=90)
    kpis["scorecard"] = [
        {"kpi": "Respect SLA (mois)", "value": current.get("sla_pct"), "target": sla_target, "unit": "%", "status": rag(current.get("sla_pct"), sla_target, True)},
        {"kpi": "MTTR P1/P2", "value": current.get("mttr_p1p2_h"), "target": targets.get("mttr_p2_h", 8), "unit": "h", "status": rag(current.get("mttr_p1p2_h"), targets.get("mttr_p2_h", 8), False)},
        {"kpi": "Backlog ouvert", "value": kpis["backlog_open"], "target": targets.get("backlog_max", 40), "unit": "", "status": rag(kpis["backlog_open"], targets.get("backlog_max", 40), False)},
        {"kpi": "Taux de réouverture", "value": kpis["reopen_rate_pct"], "target": targets.get("reopen_rate_pct", 5), "unit": "%", "status": rag(kpis["reopen_rate_pct"], targets.get("reopen_rate_pct", 5), False)},
        {"kpi": "Échec des changements", "value": kpis["change_failure_rate_pct"], "target": targets.get("change_failure_rate_pct", 10), "unit": "%", "status": rag(kpis["change_failure_rate_pct"], targets.get("change_failure_rate_pct", 10), False)},
    ]
    statuses = [s["status"] for s in kpis["scorecard"]]
    kpis["overall_status"] = "red" if statuses.count("red") >= 2 else ("amber" if "red" in statuses or "amber" in statuses else "green")
    return kpis


def tickets_by_assignee_per_app(tickets: list[Ticket]) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for t in tickets:
        if t.application_id and t.assignee_id and t.resolved_at:
            out[t.application_id][t.assignee_id] += 1
    return out
