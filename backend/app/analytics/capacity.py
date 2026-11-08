"""Capacity planning: demand forecast, per-week load vs capacity, scenarios, assignment proposals."""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from app.models import BacklogItem, TeamMember, Ticket, Timesheet


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def holt_forecast(series: list[float], horizon: int, alpha: float = 0.5, beta: float = 0.3) -> list[float]:
    """Holt's linear exponential smoothing — enough for weekly run-load forecasting."""
    if not series:
        return [0.0] * horizon
    if len(series) == 1:
        return [series[0]] * horizon
    level, trend = series[0], series[1] - series[0]
    for y in series[1:]:
        prev_level = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    return [max(0.0, round(level + (i + 1) * trend, 1)) for i in range(horizon)]


def member_capacity(member: TeamMember, week: date) -> float:
    days_off = 0
    for a in member.absences:
        start, end = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
        for i in range(5):
            if start <= week + timedelta(days=i) <= end:
                days_off += 1
    return round(member.weekly_capacity_h * (5 - days_off) / 5, 1)


def analyze(
    members: list[TeamMember],
    tickets: list[Ticket],
    timesheets: list[Timesheet],
    backlog: list[BacklogItem],
    today: date | None = None,
    horizon_weeks: int = 8,
    scenario: dict | None = None,
) -> dict:
    today = today or datetime.now(timezone.utc).date()
    scenario = scenario or {}
    this_week = week_start(today)

    # --- run demand history: hours logged on RUN/MCO per week over the past 12 weeks ---
    run_by_week: dict[date, float] = defaultdict(float)
    for s in timesheets:
        if s.activity_code in ("RUN", "MCO"):
            run_by_week[week_start(s.day)] += s.hours
    history_weeks = [this_week - timedelta(weeks=i) for i in range(12, 0, -1)]
    history = [round(run_by_week.get(w, 0.0), 1) for w in history_weeks]
    # ignore leading weeks without data (perimeter recently onboarded)
    while history and history[0] == 0:
        history.pop(0)
    run_forecast = holt_forecast(history, horizon_weeks)

    incident_growth = 1 + scenario.get("incident_growth_pct", 0) / 100
    run_forecast = [round(v * incident_growth, 1) for v in run_forecast]

    # --- team, with scenario adjustments ---
    team = [m for m in members if m.id not in set(scenario.get("remove_member_ids", []))]
    extra_fte = scenario.get("add_fte", 0)
    active_backlog = [b for b in backlog if b.status in ("todo", "in_progress")]

    # --- spread backlog over weeks by due date & priority (earliest-due-first) ---
    weeks = [this_week + timedelta(weeks=i) for i in range(horizon_weeks)]
    backlog_by_week: dict[date, float] = defaultdict(float)
    for b in sorted(active_backlog, key=lambda b: (b.due_date or date.max, b.priority)):
        due = week_start(b.due_date) if b.due_date else weeks[-1]
        span = [w for w in weeks if w <= due] or [weeks[0]]
        span = span[-3:]  # work lands in the 3 weeks before the due date
        for w in span:
            backlog_by_week[w] += b.estimate_h / len(span)

    forecast = []
    for i, w in enumerate(weeks):
        capacity = sum(member_capacity(m, w) for m in team) + extra_fte * 35
        demand = run_forecast[i] + backlog_by_week.get(w, 0)
        utilization = round(100 * demand / capacity, 1) if capacity else None
        forecast.append(
            {
                "week": w.isoformat(),
                "capacity_h": round(capacity, 1),
                "run_demand_h": run_forecast[i],
                "backlog_demand_h": round(backlog_by_week.get(w, 0), 1),
                "demand_h": round(demand, 1),
                "utilization_pct": utilization,
                "status": "overload" if utilization and utilization > 100 else ("tension" if utilization and utilization > 85 else "ok"),
            }
        )

    # --- per-member load (current open work) ---
    open_tickets = [t for t in tickets if t.status in ("open", "in_progress")]
    load: dict[str, float] = defaultdict(float)
    for t in open_tickets:
        if t.assignee_id:
            load[t.assignee_id] += t.estimate_h or 4
    for b in active_backlog:
        if b.assignee_id:
            load[b.assignee_id] += b.estimate_h
    members_view = []
    for m in team:
        cap2w = member_capacity(m, this_week) + member_capacity(m, this_week + timedelta(weeks=1))
        members_view.append(
            {
                "id": m.id,
                "name": m.name,
                "profile": m.profile,
                "skills": m.skills,
                "assigned_h": round(load[m.id], 1),
                "capacity_2w_h": cap2w,
                "load_pct": round(100 * load[m.id] / cap2w, 1) if cap2w else None,
                "absent_days_next_2w": round((2 * m.weekly_capacity_h - cap2w) / (m.weekly_capacity_h / 5)) if m.weekly_capacity_h else 0,
            }
        )

    # --- greedy assignment proposal for unassigned backlog ---
    proposals = []
    projected = {mv["id"]: mv["assigned_h"] for mv in members_view}
    caps = {mv["id"]: mv["capacity_2w_h"] for mv in members_view}
    for b in sorted([b for b in active_backlog if not b.assignee_id], key=lambda b: (b.priority, b.due_date or date.max)):
        candidates = [m for m in team if b.required_skill in m.skills]
        if not candidates:
            proposals.append({"backlog_id": b.id, "title": b.title, "skill": b.required_skill, "member_id": None, "member": None, "reason": "Aucune ressource ne possède la compétence requise"})
            continue
        best = min(candidates, key=lambda m: (projected[m.id] + b.estimate_h) / (caps[m.id] or 1))
        projected[best.id] += b.estimate_h
        proposals.append(
            {
                "backlog_id": b.id,
                "title": b.title,
                "skill": b.required_skill,
                "estimate_h": b.estimate_h,
                "member_id": best.id,
                "member": best.name,
                "projected_load_pct": round(100 * projected[best.id] / (caps[best.id] or 1), 1),
                "reason": f"Compétence {b.required_skill}, charge la plus faible parmi {len(candidates)} candidat(s)",
            }
        )

    # --- skill coverage (single points of knowledge) ---
    skill_holders: dict[str, list[str]] = defaultdict(list)
    for m in team:
        for s in m.skills:
            skill_holders[s].append(m.name)
    required = {b.required_skill for b in active_backlog}
    coverage = [{"skill": s, "holders": skill_holders.get(s, []), "required": s in required, "at_risk": s in required and len(skill_holders.get(s, [])) <= 1} for s in sorted(required | set(skill_holders))]

    overload_weeks = [f for f in forecast if f["status"] == "overload"]
    total_gap_h = round(sum(f["demand_h"] - f["capacity_h"] for f in overload_weeks), 1)
    return {
        "scenario": scenario,
        "history_run_h": history,
        "forecast": forecast,
        "members": members_view,
        "assignment_proposals": proposals,
        "skill_coverage": coverage,
        "overload_weeks": len(overload_weeks),
        "capacity_gap_h": total_gap_h,
        "fte_gap": round(total_gap_h / 35 / max(len(overload_weeks), 1), 2),
    }
