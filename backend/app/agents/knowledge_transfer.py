"""Knowledge Transfer Agent — accelerates onboarding of a new application perimeter."""

import networkx as nx
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import knowledge, llm
from app.agents.base import AgentContext, AgentOutput, recommend
from app.analytics.incidents import knowledge_concentration
from app.models import Application, Dependency, Document, Perimeter, TeamMember, Ticket

NAME = "knowledge_transfer"
CRIT_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class ExtractedRisk(BaseModel):
    title: str
    description: str
    severity: str = Field(description="low, medium or high")
    application: str = Field(description="application name concerned, or 'transverse'")
    source_ids: list[str] = Field(description="ids of the <source> elements supporting the risk")


class RiskExtraction(BaseModel):
    risks: list[ExtractedRisk]


class AppCard(BaseModel):
    application: str
    purpose: str = Field(description="business purpose in one or two sentences")
    key_flows: list[str] = Field(description="main interfaces and data flows")
    operations: list[str] = Field(description="run procedures, batches, restart/recovery steps")
    known_issues: list[str] = Field(description="recurring incidents and how they are resolved")
    watch_points: list[str] = Field(description="what a newcomer must be careful about")
    source_ids: list[str]


class AppCards(BaseModel):
    cards: list[AppCard]


class OnboardingWeek(BaseModel):
    week: int
    focus: str
    activities: list[str]
    deliverables: list[str]


class OnboardingPlan(BaseModel):
    executive_summary: str
    weeks: list[OnboardingWeek]


RISK_QUERIES = [
    "risques points d'attention fragilité dette technique",
    "obsolescence version non supportée fin de support",
    "dépendance fournisseur connaissance limitée documentation manquante",
    "incident récurrent batch échec reprise manuelle",
    "sécurité vulnérabilité mot de passe accès",
]


def build_map(apps: list[Application], deps: list[Dependency], tickets: list[Ticket]) -> dict:
    g = nx.DiGraph()
    for a in apps:
        g.add_node(a.id)
    for d in deps:
        g.add_edge(d.source_id, d.target_id, kind=d.kind)
    centrality = nx.betweenness_centrality(g) if len(g) > 2 else {n: 0 for n in g}
    incidents = {a.id: 0 for a in apps}
    for t in tickets:
        if t.kind == "incident" and t.application_id in incidents:
            incidents[t.application_id] += 1
    max_inc = max(incidents.values(), default=0) or 1

    nodes = []
    for a in apps:
        # blast radius = apps that (transitively) depend on this one
        dependents = nx.ancestors(g, a.id)
        score = (
            0.4 * CRIT_WEIGHT.get(a.criticality, 2) / 4
            + 0.25 * min(len(dependents) / max(len(apps) - 1, 1), 1)
            + 0.15 * centrality.get(a.id, 0)
            + 0.2 * incidents[a.id] / max_inc
        )
        nodes.append(
            {
                "id": a.id,
                "name": a.name,
                "technology": a.technology,
                "criticality": a.criticality,
                "business_owner": a.business_owner,
                "users": a.users_count,
                "incidents": incidents[a.id],
                "dependents": len(dependents),
                "depends_on": g.out_degree(a.id),
                "risk_score": round(100 * score),
                "single_point_of_failure": len(dependents) >= 2 and a.criticality in ("high", "critical"),
            }
        )
    names = {a.id: a.name for a in apps}
    edges = [{"source": d.source_id, "target": d.target_id, "kind": d.kind, "label": d.description, "source_name": names.get(d.source_id), "target_name": names.get(d.target_id)} for d in deps]
    cycles = [[names[n] for n in c] for c in nx.simple_cycles(g)][:5]
    return {"nodes": sorted(nodes, key=lambda n: n["risk_score"], reverse=True), "edges": edges, "cycles": cycles}


def run(ctx: AgentContext) -> AgentOutput:
    db = ctx.db
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    apps = db.scalars(select(Application).where(Application.perimeter_id == ctx.perimeter_id)).all()
    deps = db.scalars(select(Dependency).where(Dependency.perimeter_id == ctx.perimeter_id)).all()
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == ctx.perimeter_id)).all()
    members = {m.id: m.name for m in db.scalars(select(TeamMember).where(TeamMember.perimeter_id == ctx.perimeter_id))}
    docs = db.scalars(select(Document).where(Document.perimeter_id == ctx.perimeter_id)).all()
    app_names = {a.id: a.name for a in apps}

    cartography = build_map(apps, deps, tickets)
    recs = []
    llm_used = False

    # ---- 1. risks from documentation (RAG) ----
    hits: dict[str, knowledge.Hit] = {}
    for q in RISK_QUERIES + [f"{a.name} risques exploitation" for a in apps[:8]]:
        for h in knowledge.search(db, ctx.perimeter_id, q, k=3):
            hits.setdefault(h.chunk_id, h)
    ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:18]

    def risk_fallback() -> RiskExtraction:
        keywords = ("risque", "attention", "obsolète", "fin de support", "manuel", "aucun", "unique", "fragile", "dette")
        risks = []
        for h in ranked:
            for sentence in h.text.replace("\n", " ").split(". "):
                if any(k in sentence.lower() for k in keywords) and len(sentence) > 30:
                    app = next((a.name for a in apps if a.name.lower() in sentence.lower()), "transverse")
                    risks.append(ExtractedRisk(title=sentence[:90], description=sentence.strip(), severity="high" if "critique" in sentence.lower() or "fin de support" in sentence.lower() else "medium", application=app, source_ids=[h.chunk_id]))
        uniq = {r.title: r for r in risks}
        return RiskExtraction(risks=list(uniq.values())[:8])

    extraction, used = llm.structured(
        schema=RiskExtraction,
        context=knowledge.format_context(ranked),
        task=(
            "Identify the delivery and operational risks for the team taking over this application perimeter "
            "(obsolescence, undocumented components, manual operations, single points of failure, supplier "
            "dependencies, security). Only report risks explicitly supported by the sources. Max 10, most severe first."
        ),
        fallback=risk_fallback,
    )
    llm_used |= used
    for r in extraction.risks:
        sources = [hits[i].as_source() for i in r.source_ids if i in hits]
        recs.append(
            recommend(ctx, NAME, "risk", f"[{r.application}] {r.title}", r.description, impact="high" if r.severity == "high" else "medium", confidence=0.8 if sources else 0.5, sources=sources)
        )

    # ---- 2. structural risks from the map ----
    for n in cartography["nodes"]:
        if n["single_point_of_failure"]:
            recs.append(
                recommend(ctx, NAME, "risk", f"Point de défaillance unique : {n['name']}", f"{n['name']} (criticité {n['criticality']}) est utilisée par {n['dependents']} application(s). Prévoir un runbook de reprise et une astreinte formée en priorité.", impact="high", confidence=0.85, sources=[{"type": "application", "ref": n["id"], "label": n["name"], "excerpt": f"{n['dependents']} dépendants, {n['incidents']} incidents"}])
            )
    documented = {a.id for a in apps for d in docs if a.name.lower() in d.content.lower()}
    for a in apps:
        if a.id not in documented and a.criticality in ("high", "critical"):
            recs.append(recommend(ctx, NAME, "risk", f"Application critique sans documentation : {a.name}", "Aucun document transmis ne mentionne cette application. Demander le DEX et le DAT au prestataire sortant avant la fin de la phase de transfert.", impact="high", confidence=0.9, sources=[{"type": "application", "ref": a.id, "label": a.name, "excerpt": ""}]))

    # ---- 3. knowledge concentration (bus factor) ----
    for kc in knowledge_concentration(tickets):
        recs.append(
            recommend(ctx, NAME, "knowledge", f"Connaissance concentrée : {app_names.get(kc['application_id'])}", f"{members.get(kc['member_id'], kc['member_id'])} a traité {kc['share_pct']} % des {kc['tickets']} tickets résolus. Organiser un binômage et documenter les procédures clés.", impact="medium", confidence=0.8, payload=kc)
        )

    # ---- 4. application knowledge cards (business knowledge extraction) ----
    card_hits: dict[str, knowledge.Hit] = {}
    focus_apps = [a for a in apps if a.id in {n["id"] for n in cartography["nodes"][:5]}]
    for a in focus_apps:
        for h in knowledge.search(db, ctx.perimeter_id, f"{a.name} fonctionnement procédure exploitation", k=4):
            card_hits.setdefault(h.chunk_id, h)
    incidents_by_app: dict[str, list[Ticket]] = {}
    for t in tickets:
        if t.kind == "incident" and t.resolution and t.application_id:
            incidents_by_app.setdefault(t.application_id, []).append(t)
    edges_by_app = {a.id: [e for e in cartography["edges"] if a.id in (e["source"], e["target"])] for a in focus_apps}

    def cards_fallback() -> AppCards:
        cards = []
        for a in focus_apps:
            sentences = [s.strip() for h in card_hits.values() for s in h.text.replace("\n", " ").split(". ") if a.name.lower() in s.lower()]
            ops = [s for s in sentences if any(k in s.lower() for k in ("relancer", "reprise", "redémarrage", "purge", "procédure", "batch", "ordonnanc"))]
            watch = [s for s in sentences if any(k in s.lower() for k in ("attention", "obsolète", "fin de support", "manuel", "seule", "non documenté", "risque"))]
            issues = {}
            for t in incidents_by_app.get(a.id, []):
                issues.setdefault(t.title, t.resolution)
            cards.append(AppCard(
                application=a.name, purpose=a.description or next(iter(sentences), ""),
                key_flows=[f"{e['source_name']} → {e['target_name']} ({e['kind']}) {e['label']}".strip() for e in edges_by_app[a.id]],
                operations=ops[:4], known_issues=[f"{k} → {v}" for k, v in list(issues.items())[:4]], watch_points=watch[:3],
                source_ids=[h.chunk_id for h in card_hits.values() if a.name.lower() in h.text.lower()][:3],
            ))
        return AppCards(cards=cards)

    cards_context = knowledge.format_context(list(card_hits.values())) + "\n\n" + "\n".join(
        f"<application name=\"{a.name}\" technology=\"{a.technology}\" criticality=\"{a.criticality}\">flows: {[(e['source_name'], e['target_name'], e['kind']) for e in edges_by_app[a.id]]}; "
        f"resolved incidents: {[(t.title, t.resolution) for t in incidents_by_app.get(a.id, [])[:6]]}</application>" for a in focus_apps
    )
    app_cards, used = llm.structured(
        schema=AppCards, context=cards_context, fallback=cards_fallback,
        task="Write one knowledge card per <application> for engineers joining the maintenance team: purpose, key flows, run procedures, known issues with their fix, watch points. Use only the context; leave a list empty rather than guessing.",
    )
    llm_used |= used

    # ---- 5. onboarding plan + executive summary ----
    top = cartography["nodes"][:6]
    doc_types = sorted({d.doc_type for d in docs})
    plan_context = (
        f"Perimeter: {perimeter.name} — {perimeter.description}\n"
        f"Documents available: {', '.join(f'{d.filename} ({d.doc_type})' for d in docs)}\n"
        f"Applications ranked by risk: " + "; ".join(f"{n['name']} [{n['criticality']}, {n['incidents']} incidents, {n['dependents']} dependents, {n['technology']}]" for n in cartography["nodes"]) + "\n"
        f"Identified risks: " + "; ".join(r.title for r in recs)
    )

    def plan_fallback() -> OnboardingPlan:
        crit = [n["name"] for n in top[:3]]
        return OnboardingPlan(
            executive_summary=(
                f"Le périmètre {perimeter.name} compte {len(apps)} applications et {len(deps)} flux inter-applicatifs. "
                f"Les applications prioritaires sont {', '.join(crit)}. {len(recs)} risques ont été identifiés, dont "
                f"{sum(1 for r in recs if r.impact == 'high')} majeurs. Documentation reçue : {', '.join(doc_types) or 'aucune'}."
            ),
            weeks=[
                OnboardingWeek(week=1, focus="Immersion et cartographie", activities=["Kick-off avec le prestataire sortant", "Revue du DAT et de la cartographie", f"Accès et environnements pour {', '.join(crit)}"], deliverables=["Cartographie validée", "Matrice des accès"]),
                OnboardingWeek(week=2, focus="Applications critiques", activities=[f"Sessions de transfert sur {c}" for c in crit] + ["Revue des incidents des 6 derniers mois"], deliverables=["Fiches applicatives", "Runbooks P1"]),
                OnboardingWeek(week=3, focus="Exploitation en double commande", activities=["Traitement des tickets en binôme", "Exécution accompagnée des batchs critiques", "Traitement des risques majeurs"], deliverables=["Plan de réduction des risques", "Base de connaissance initialisée"]),
                OnboardingWeek(week=4, focus="Autonomie et go/no-go", activities=["Prise en charge autonome sous supervision", "Revue de la couverture de compétences", "Comité de fin de transfert"], deliverables=["PV de fin de transfert", "Plan de staffing cible"]),
            ],
        )

    plan, used = llm.structured(
        schema=OnboardingPlan,
        context=plan_context,
        task="Write a 4-week onboarding plan for the incoming AMS team (critical apps first, risks addressed early) and a 5-line executive summary for the Delivery Director.",
        fallback=plan_fallback,
        tier="reasoning",
    )
    llm_used |= used
    cards_payload = [c.model_dump() | {"sources": [card_hits[i].as_source() for i in c.source_ids if i in card_hits]} for c in app_cards.cards]
    recs.append(
        recommend(ctx, NAME, "onboarding_plan", f"Plan d'onboarding — {perimeter.name}", plan.executive_summary, impact="high", confidence=0.75, payload=plan.model_dump() | {"app_cards": cards_payload})
    )

    return AgentOutput(
        summary=plan.executive_summary,
        data={
            "cartography": cartography,
            "onboarding_plan": plan.model_dump(),
            "app_cards": cards_payload,
            "documents": [{"filename": d.filename, "type": d.doc_type} for d in docs],
            "risk_count": sum(1 for r in recs if r.kind == "risk"),
        },
        recommendations=recs,
        llm_used=llm_used,
    )
