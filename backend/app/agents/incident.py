"""Incident & Problem Agent — recurring incidents, duplicates, suggested resolutions, scope drift."""

from sqlalchemy import select

from app import llm
from app.agents.base import AgentContext, AgentOutput, recommend, ticket_source
from app.analytics.incidents import open_duplicates, recurring_clusters, similar_resolved
from app.analytics.scope import is_out_of_scope
from app.models import Application, Perimeter, Ticket

NAME = "incident"


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == ctx.perimeter_id)).all()
    apps = {a.id: a.name for a in db.scalars(select(Application).where(Application.perimeter_id == ctx.perimeter_id))}
    by_ref = {t.ref: t for t in tickets}
    recs = []

    clusters = recurring_clusters(tickets)
    for c in [c for c in clusters if c["count"] >= 5][:3]:
        app = apps.get(c["application_id"], "?")
        recs.append(
            recommend(ctx, NAME, "problem", f"Ouvrir un problème : « {c['label']} » sur {app} ({c['count']} incidents / 90 j)",
                      f"{c['count']} incidents similaires dont {c['p1p2']} P1/P2, {c['total_resolution_h']} h cumulées de résolution. Résolutions appliquées : {' | '.join(c['resolutions']) or 'non renseignées'}. Une analyse de cause racine éviterait la récurrence.",
                      impact="high" if c["p1p2"] >= 2 or c["count"] >= 5 else "medium", confidence=0.8,
                      sources=[ticket_source(by_ref[r]) for r in c["refs"][:5]], payload={**c, "application": app})
        )

    dups = open_duplicates(tickets)
    for d in dups:
        recs.append(recommend(ctx, NAME, "duplicate", f"Doublon probable : {d['a']} / {d['b']}", f"« {d['title']} » — similarité {d['similarity']}. Fusionner pour éviter un double traitement.", impact="low", confidence=d["similarity"], sources=[ticket_source(by_ref[d["a"]]), ticket_source(by_ref[d["b"]])]))

    suggestions = []
    for t in [t for t in tickets if t.status in ("open", "in_progress") and t.kind == "incident"][:15]:
        sim = similar_resolved(t, tickets, k=2)
        if sim:
            suggestions.append({"ref": t.ref, "title": t.title, "priority": t.priority, "similar": [{"ref": s.ref, "title": s.title, "resolution": s.resolution, "similarity": score} for s, score in sim]})

    oos = []
    for t in tickets:
        flag, terms = is_out_of_scope(t, perimeter.contract)
        if flag and t.kind in ("request", "change", "incident"):
            oos.append({"ref": t.ref, "title": t.title, "terms": terms, "status": t.status})
    if oos:
        recs.append(recommend(ctx, NAME, "scope", f"{len(oos)} demande(s) hors périmètre contractuel détectée(s)", "\n".join(f"• {o['ref']} « {o['title']} » ({', '.join(o['terms'])})" for o in oos[:10]) + "\nQualifier avec le client : devis séparé ou avenant.", impact="high", confidence=0.75, sources=[ticket_source(by_ref[o["ref"]]) for o in oos[:5]], payload={"tickets": oos}))

    def fallback() -> str:
        return f"{len(clusters)} famille(s) d'incidents récurrents, {len(dups)} doublon(s) probable(s), {len(suggestions)} ticket(s) ouverts avec une résolution similaire connue, {len(oos)} demande(s) hors périmètre."

    summary, used = llm.text(purpose="incident_summary", context=f"Recurring clusters: {clusters[:5]}\nDuplicates: {dups}\nOut of scope: {oos[:10]}", task="Summarise in 4 lines the incident & problem management situation and the top problem to tackle first.", fallback=fallback, effort="low")
    return AgentOutput(summary=summary, data={"clusters": clusters, "duplicates": dups, "suggestions": suggestions, "out_of_scope": oos}, recommendations=recs, llm_used=used)
