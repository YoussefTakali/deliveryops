"""Proactive watch: cheap deterministic checks run on a schedule for every perimeter, turned into
deduplicated notifications (and Teams cards for critical ones). No LLM cost.

Also runs a slower, LLM-backed pass (see `ai_scan`/`ai_scan_all` below): every Nth cycle, the
risk/billing agents are invoked read-only per perimeter and a genuinely new finding becomes an
`ai_suggestion` notification — the same agents used by the on-demand workflows in
app/agents/orchestrator.py, just triggered on a schedule instead of by a user action."""

import hashlib
import logging
import threading
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import notify
from app.agents import billing as billing_agent
from app.agents import risk as risk_agent
from app.agents.base import AgentContext
from app.analytics import capacity as capacity_engine
from app.analytics.incidents import recurring_clusters
from app.analytics.kpi import DEFAULT_SLA, _aware, service_kpis
from app.analytics.scope import is_out_of_scope
from app.config import get_settings
from app.db import SessionLocal
from app.models import PERSONAL_CLIENT, Application, BacklogItem, Notification, Perimeter, Recommendation, TeamMember, Ticket, Timesheet

log = logging.getLogger(__name__)
settings = get_settings()

# Agents eligible for the proactive AI pass (subset selected via settings.watch_ai_agents), and the
# human-readable label used in the resulting notification's title.
_AI_AGENTS = {"risk": (risk_agent, "Analyse des risques"), "billing": (billing_agent, "Facturation")}


def _all(db, model, pid):
    return db.scalars(select(model).where(model.perimeter_id == pid)).all()


def checks(db: Session, perimeter: Perimeter, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    pid = perimeter.id
    contract = perimeter.contract or {}
    sla = contract.get("sla") or DEFAULT_SLA
    tickets = _all(db, Ticket, pid)
    apps = {a.id: a.name for a in _all(db, Application, pid)}
    link = f"/p/{pid}"
    alerts: list[dict] = []

    # 1. open incidents breaching or about to breach their SLA
    breached, at_risk = [], []
    for t in tickets:
        if t.kind != "incident" or t.status not in ("open", "in_progress") or t.priority not in sla:
            continue
        limit = sla[t.priority]["resolution_h"]
        elapsed = (now - _aware(t.opened_at)).total_seconds() / 3600
        if elapsed > limit:
            breached.append(t)
        elif elapsed > 0.75 * limit:
            at_risk.append((t, limit - elapsed))
    critical_breach = [t for t in breached if t.priority in ("P1", "P2")]
    if critical_breach:
        alerts.append({"kind": "sla_breach", "severity": "high", "fingerprint": f"sla_breach:{','.join(sorted(t.ref for t in critical_breach))}",
                       "title": f"{len(critical_breach)} incident(s) P1/P2 hors délai SLA",
                       "body": "\n".join(f"• {t.ref} {t.priority} {apps.get(t.application_id, '')} — {t.title}" for t in critical_breach[:8]), "link": f"{link}/incidents"})
    if at_risk:
        alerts.append({"kind": "sla_at_risk", "severity": "medium", "fingerprint": f"sla_at_risk:{','.join(sorted(t.ref for t, _ in at_risk))}",
                       "title": f"{len(at_risk)} incident(s) proches de l'échéance SLA",
                       "body": "\n".join(f"• {t.ref} {t.priority} — {t.title} ({left:.1f} h restantes)" for t, left in sorted(at_risk, key=lambda x: x[1])[:8]), "link": f"{link}/incidents"})

    # 2. KPI drifts on the last complete month
    kpis = service_kpis(tickets, contract, now)
    reds = [s for s in kpis["scorecard"] if s["status"] == "red"]
    if reds:
        alerts.append({"kind": "kpi_drift", "severity": "high", "fingerprint": f"kpi_drift:{kpis['reference_month']}:{','.join(s['kpi'] for s in reds)}",
                       "title": f"Dérive d'indicateurs ({kpis['reference_month']}) : {', '.join(s['kpi'] for s in reds)}",
                       "body": "\n".join(f"• {s['kpi']} : {s['value']}{s['unit']} pour une cible de {s['target']}{s['unit']}" for s in reds), "link": link})

    # 3. incident spike: same family 3+ times in 14 days
    for c in recurring_clusters(tickets, days=14, min_size=3, now=now)[:3]:
        alerts.append({"kind": "incident_spike", "severity": "high" if c["p1p2"] else "medium", "fingerprint": f"spike:{c['application_id']}:{c['label']}:{now:%Y-%W}",
                       "title": f"Pic d'incidents : « {c['label']} » ({c['count']} en 14 j) sur {apps.get(c['application_id'], '?')}",
                       "body": f"Références : {', '.join(c['refs'])}. Envisager l'ouverture d'un problème.", "link": f"{link}/incidents"})

    # 4. capacity overload in the next two weeks
    cap = capacity_engine.analyze(_all(db, TeamMember, pid), tickets, _all(db, Timesheet, pid), _all(db, BacklogItem, pid), today=now.date(), horizon_weeks=2)
    near = [w for w in cap["forecast"] if w["status"] == "overload"]
    if near:
        alerts.append({"kind": "capacity", "severity": "high" if any(w["utilization_pct"] > 120 for w in near) else "medium", "fingerprint": f"capacity:{','.join(w['week'] for w in near)}",
                       "title": f"Surcharge de l'équipe dans les 2 semaines ({max(w['utilization_pct'] for w in near)} %)",
                       "body": "\n".join(f"• Semaine du {w['week']} : demande {w['demand_h']} h pour {w['capacity_h']} h disponibles" for w in near), "link": f"{link}/capacity"})

    # 5. time & material run-rate vs monthly cap
    cap_tm = contract.get("monthly_cap_time_material")
    rates = contract.get("rate_card", {})
    if cap_tm and rates:
        members = {m.id: m for m in _all(db, TeamMember, pid)}
        start = now.date().replace(day=1)
        tm = sum(s.hours / contract.get("hours_per_day", 7) * rates.get(members[s.member_id].profile, 0)
                 for s in _all(db, Timesheet, pid) if s.day >= start and s.activity_code == "EVOL" and s.member_id in members)
        elapsed_days = max((now.date() - start).days, 1)
        projected = tm / elapsed_days * 30
        if projected > cap_tm:
            alerts.append({"kind": "billing_cap", "severity": "medium", "fingerprint": f"billing_cap:{start:%Y-%m}",
                           "title": f"Plafond de régie en voie de dépassement ({projected:,.0f} € projetés / {cap_tm:,.0f} €)".replace(",", " "),
                           "body": f"Consommé à date : {tm:,.0f} €. Obtenir l'accord écrit du client ou prioriser.".replace(",", " "), "link": f"{link}/billing"})

    # 6. missing timesheets over the last 5 working days
    days = [now.date() - timedelta(days=i) for i in range(1, 10) if (now.date() - timedelta(days=i)).weekday() < 5][:5]
    filled = Counter((s.member_id, s.day) for s in _all(db, Timesheet, pid) if s.day in days)
    late = []
    for m in _all(db, TeamMember, pid):
        absent = {d for a in m.absences for d in days if date.fromisoformat(a["start"]) <= d <= date.fromisoformat(a["end"])}
        missing = [d for d in days if (m.id, d) not in filled and d not in absent]
        if len(missing) >= 3:
            late.append((m.name, len(missing)))
    if late:
        alerts.append({"kind": "timesheets", "severity": "low", "fingerprint": f"timesheets:{days[0]}:{','.join(n for n, _ in late)}",
                       "title": f"Saisies de temps manquantes ({len(late)} personne(s))",
                       "body": "\n".join(f"• {n} : {k} jour(s) sur les 5 derniers" for n, k in late), "link": f"{link}/billing"})

    # 7. open out-of-scope requests not yet qualified
    oos = [t for t in tickets if t.status in ("open", "in_progress") and is_out_of_scope(t, contract)[0]]
    if oos:
        alerts.append({"kind": "scope", "severity": "medium", "fingerprint": f"scope:{','.join(sorted(t.ref for t in oos))}",
                       "title": f"{len(oos)} demande(s) hors périmètre en cours de traitement",
                       "body": "\n".join(f"• {t.ref} — {t.title}" for t in oos[:8]) + "\nQualifier avec le client avant de consommer du forfait.", "link": f"{link}/incidents"})

    # 8. deliverables waiting for a decision for more than 3 days
    stale = [r for r in _all(db, Recommendation, pid) if r.status == "pending" and r.impact == "high" and _aware(r.created_at) < now - timedelta(days=3)]
    if stale:
        alerts.append({"kind": "stale_approvals", "severity": "medium", "fingerprint": f"stale:{len(stale)}:{now:%Y-%m-%d}",
                       "title": f"{len(stale)} décision(s) à fort impact en attente depuis plus de 3 jours",
                       "body": "\n".join(f"• {r.title}" for r in stale[:8]), "link": "/approvals"})
    return alerts


def scan(db: Session, perimeter: Perimeter) -> list[Notification]:
    created = []
    for a in checks(db, perimeter):
        existing = db.scalar(select(Notification).where(Notification.perimeter_id == perimeter.id, Notification.fingerprint == a["fingerprint"]))
        if existing:
            continue  # same situation already notified
        n = Notification(perimeter_id=perimeter.id, **a)
        db.add(n)
        created.append(n)
        if a["severity"] == "high":
            notify.alert(perimeter, a["title"], a["body"], a["link"])
    db.commit()
    return created


# In-memory cadence/dedup state for the AI pass. Reset on process restart, which is harmless: the
# worst case is one extra LLM pass per perimeter right after a restart, not a missed one.
_ai_cycle = 0
_ai_signal_cache: dict[str, str] = {}


def _ai_signal(db: Session, pid: str) -> str:
    """Cheap fingerprint of "is there anything new to look at" for a perimeter, used to skip an
    LLM pass entirely when nothing changed since the last one. Mirrors the kind of data the
    deterministic checks() above already reads, just reduced to counts/latest timestamps instead of
    full content, so computing it costs no LLM calls."""
    tickets = _all(db, Ticket, pid)
    sig = (
        len(tickets),
        sorted(Counter(t.status for t in tickets).items()),
        max((t.opened_at for t in tickets), default=None),
        max((t.resolved_at for t in tickets if t.resolved_at), default=None),
        len(_all(db, Timesheet, pid)),
        len(_all(db, BacklogItem, pid)),
        len(_all(db, Recommendation, pid)),
    )
    return hashlib.sha1(repr(sig).encode()).hexdigest()


def ai_scan(db: Session, perimeter: Perimeter, now: datetime) -> list[Notification]:
    """Periodic AI pass: run the configured agents (risk/billing, from app.agents.orchestrator) in
    read-only mode for this perimeter and turn a new finding into an `ai_suggestion` notification.
    Read-only mode (same flag the "ask" workflow uses) means no Recommendation rows are written and
    the on-demand workflows are unaffected — this is purely an additional, scheduled trigger into
    the same agents."""
    pid = perimeter.id
    signal = _ai_signal(db, pid)
    if _ai_signal_cache.get(pid) == signal:
        return []  # nothing changed since the last AI pass for this perimeter: skip the LLM call(s)
    _ai_signal_cache[pid] = signal

    created: list[Notification] = []
    names = [a.strip() for a in settings.watch_ai_agents.split(",") if a.strip()]
    for name in names:
        agent = _AI_AGENTS.get(name)
        if not agent:
            continue
        module, label = agent
        ctx = AgentContext(db=db, perimeter_id=pid, run_id=None, params={"read_only": True})
        try:
            out = module.run(ctx)
        except Exception:  # noqa: BLE001 — one failing agent must not stop the watch or the other agent
            db.rollback()
            log.exception("ai watch failed for perimeter %s agent %s", pid, name)
            continue
        if not out.summary:
            continue
        # Weekly bucket in the fingerprint: an unchanged underlying situation still resurfaces as a
        # reminder every week instead of going silent forever after the first notification.
        digest = hashlib.sha1(out.summary.encode()).hexdigest()[:12]
        fingerprint = f"ai_suggestion:{name}:{now:%G-W%V}:{digest}"
        existing = db.scalar(select(Notification).where(Notification.perimeter_id == pid, Notification.fingerprint == fingerprint))
        if existing:
            continue
        n = Notification(perimeter_id=pid, kind="ai_suggestion", severity="low", fingerprint=fingerprint,
                         title=f"Suggestion IA — {label}", body=out.summary[:2000], link=f"/p/{pid}")
        db.add(n)
        created.append(n)
    db.commit()
    return created


def ai_scan_all() -> int:
    """Runs ai_scan() for every perimeter, every Nth call (see settings.watch_ai_every_n_cycles) so
    it stays much less frequent than the deterministic scan_all() driving the same loop."""
    global _ai_cycle
    if not settings.watch_ai_enabled:
        return 0
    _ai_cycle += 1
    if _ai_cycle % max(settings.watch_ai_every_n_cycles, 1) != 0:
        return 0
    total = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for p in db.scalars(select(Perimeter).where(Perimeter.client_id != PERSONAL_CLIENT)).all():
            try:
                total += len(ai_scan(db, p, now))
            except Exception:  # noqa: BLE001 — one broken perimeter must not stop the watch
                db.rollback()
                log.exception("ai watch failed for perimeter %s", p.id)
    log.info("ai watch: %d new suggestion(s)", total)
    return total


def scan_all() -> int:
    total = 0
    with SessionLocal() as db:
        for p in db.scalars(select(Perimeter).where(Perimeter.client_id != PERSONAL_CLIENT)).all():
            try:
                total += len(scan(db, p))
            except Exception:  # noqa: BLE001 — one broken perimeter must not stop the watch
                db.rollback()
                log.exception("watch failed for perimeter %s", p.id)
    log.info("watch: %d new notification(s)", total)
    return total


def run_once() -> int:
    """One cycle: sync all connectors, then run the deterministic checks. Returns notifications created."""
    try:
        from app import connectors

        with SessionLocal() as db:
            connectors.sync_all(db)
    except Exception:  # noqa: BLE001 — a broken connector must not stop the watch
        log.exception("connector sync failed")
    return scan_all() + ai_scan_all()


_stop = threading.Event()


def run_forever(stop: threading.Event) -> None:
    """Blocking loop: run_once(), sleep, repeat, until `stop` is set. Used both by the in-process
    scheduler thread (ROLE=all, local dev) and by the standalone worker process (ROLE=worker,
    production — see app/worker.py)."""
    stop.wait(10)  # let the API finish starting before the first cycle, when running in-process
    while not stop.is_set():
        run_once()
        stop.wait(settings.watch_interval_minutes * 60)


def start_scheduler() -> None:
    """In-process background thread — used when one container runs both the API and the
    scheduler (ROLE=all, the default for local dev). In production run a dedicated ROLE=worker
    container via app/worker.py instead, so the scheduler survives API restarts/redeploys."""
    if not settings.watch_enabled:
        return
    threading.Thread(target=run_forever, args=(_stop,), daemon=True, name="proactive-watch").start()


def stop_scheduler() -> None:
    _stop.set()
