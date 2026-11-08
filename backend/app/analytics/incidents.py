"""Incident intelligence: recurring-incident clustering, duplicates, similar past resolutions,
knowledge concentration (bus factor), effort estimation from history."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import quantiles

from app.analytics.kpi import _aware
from app.knowledge import tokens
from app.models import Ticket


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _sig(t: Ticket) -> set[str]:
    return set(tokens(f"{t.title} {t.category}"))


def recurring_clusters(tickets: list[Ticket], days: int = 90, min_size: int = 3, threshold: float = 0.4, now: datetime | None = None) -> list[dict]:
    now = _aware(now) or datetime.now(timezone.utc)
    recent = [t for t in tickets if t.kind == "incident" and _aware(t.opened_at) >= now - timedelta(days=days)]
    clusters: list[list[Ticket]] = []
    for t in sorted(recent, key=lambda t: t.opened_at):
        sig = _sig(t)
        for c in clusters:
            if c[0].application_id == t.application_id and jaccard(sig, _sig(c[0])) >= threshold:
                c.append(t)
                break
        else:
            clusters.append([t])
    out = []
    for c in clusters:
        if len(c) < min_size:
            continue
        hours = [(_aware(t.resolved_at) - _aware(t.opened_at)).total_seconds() / 3600 for t in c if t.resolved_at]
        common = Counter(w for t in c for w in _sig(t)).most_common(4)
        out.append(
            {
                "application_id": c[0].application_id,
                "label": c[0].title,
                "keywords": [w for w, _ in common],
                "count": len(c),
                "refs": [t.ref for t in c],
                "p1p2": sum(1 for t in c if t.priority in ("P1", "P2")),
                "total_resolution_h": round(sum(hours), 1),
                "resolutions": list({t.resolution for t in c if t.resolution})[:3],
            }
        )
    return sorted(out, key=lambda x: (x["count"], x["p1p2"]), reverse=True)


def open_duplicates(tickets: list[Ticket], threshold: float = 0.6) -> list[dict]:
    open_ = [t for t in tickets if t.status in ("open", "in_progress")]
    pairs = []
    for i, a in enumerate(open_):
        for b in open_[i + 1 :]:
            if a.application_id == b.application_id and abs((_aware(a.opened_at) - _aware(b.opened_at)).days) <= 3:
                score = jaccard(_sig(a), _sig(b))
                if score >= threshold:
                    pairs.append({"a": a.ref, "b": b.ref, "title": a.title, "similarity": round(score, 2)})
    return pairs


def similar_resolved(target: Ticket | str, tickets: list[Ticket], k: int = 3) -> list[tuple[Ticket, float]]:
    sig = set(tokens(target)) if isinstance(target, str) else set(tokens(f"{target.title} {target.description}"))
    scored = []
    for t in tickets:
        if not t.resolved_at or (not isinstance(target, str) and t.id == target.id):
            continue
        s = jaccard(sig, set(tokens(f"{t.title} {t.description}")))
        if s > 0.12:
            scored.append((t, round(s, 2)))
    return sorted(scored, key=lambda x: x[1], reverse=True)[:k]


def knowledge_concentration(tickets: list[Ticket], min_tickets: int = 5, share: float = 0.7) -> list[dict]:
    """Applications where one person resolves most tickets: a delivery risk if they leave."""
    per_app: dict[str, Counter] = defaultdict(Counter)
    for t in tickets:
        if t.application_id and t.assignee_id and t.resolved_at:
            per_app[t.application_id][t.assignee_id] += 1
    out = []
    for app_id, counts in per_app.items():
        total = sum(counts.values())
        member_id, top = counts.most_common(1)[0]
        if total >= min_tickets and top / total >= share:
            out.append({"application_id": app_id, "member_id": member_id, "share_pct": round(100 * top / total), "tickets": total, "contributors": len(counts)})
    return sorted(out, key=lambda x: x["share_pct"], reverse=True)


def estimate_from_history(description: str, tickets: list[Ticket], hours_by_ref: dict[str, float]) -> dict:
    """Effort range for a change request, from the actual hours of similar delivered changes."""
    changes = [t for t in tickets if t.kind == "change" and t.resolved_at and hours_by_ref.get(t.ref)]
    similar = similar_resolved(description, changes, k=6)
    if not similar:
        pool = [hours_by_ref[t.ref] for t in changes]
        basis = "historique global des évolutions (aucune demande similaire)"
        refs = []
    else:
        pool = [hours_by_ref[t.ref] for t, _ in similar]
        basis = f"{len(similar)} évolution(s) similaire(s)"
        refs = [{"ref": t.ref, "title": t.title, "hours": hours_by_ref[t.ref], "similarity": s} for t, s in similar]
    if not pool:
        return {"p50_h": None, "p80_h": None, "basis": "aucun historique", "similar": []}
    pool = sorted(pool)
    if len(pool) >= 2:
        q = quantiles(pool, n=10, method="inclusive")
        p50, p80 = q[4], q[7]
    else:
        p50 = p80 = pool[0]
    return {"p50_h": round(p50, 1), "p80_h": round(p80, 1), "min_h": pool[0], "max_h": pool[-1], "basis": basis, "similar": refs}
