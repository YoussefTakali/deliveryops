"""Generates a realistic demo dataset relative to today. Run: python -m app.seed.seed [--reset]"""

import random
import sys
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app import knowledge
from app.db import Base, SessionLocal, engine, init_db
from app.models import (Application, BacklogItem, Client, DeliveryProject, Dependency, Meeting, Perimeter,
                        ProjectMember, TeamMember, Ticket, Timesheet, UserSkill)
from app.seed import documents as docs

rng = random.Random(42)
UTC = timezone.utc

ATLAS_CONTRACT = {
    "currency": "EUR",
    "billing_model": "mixed",
    "run_fixed_monthly_fee": 18000,
    "hours_per_day": 7,
    "rate_card": {"junior": 420, "confirmed": 560, "senior": 680, "architect": 850},
    "billable_activity_codes": {"RUN": "fixed", "MCO": "fixed", "EVOL": "time_material", "UO": "work_unit"},
    "non_billable_codes": ["INTERNAL", "TRAINING", "ONBOARDING"],
    "work_units": {
        "UO-S": {"label": "Petite évolution", "price": 1200, "max_h": 14},
        "UO-M": {"label": "Évolution moyenne", "price": 3400, "max_h": 40},
        "UO-L": {"label": "Évolution majeure", "price": 7800, "max_h": 90},
    },
    "monthly_cap_time_material": 12000,
    "sla": {
        "P1": {"resolution_h": 4, "target_pct": 95},
        "P2": {"resolution_h": 8, "target_pct": 95},
        "P3": {"resolution_h": 40, "target_pct": 90},
        "P4": {"resolution_h": 80, "target_pct": 90},
    },
    "penalties": {"per_point_below_target_pct": 1.0, "cap_pct": 10},
    "scope": {"out_of_scope": ["migration", "nouvelle application", "formation utilisateurs", "support infrastructure réseau", "refonte"]},
    "kpi_targets": {"mttr_p2_h": 8, "backlog_max": 40, "reopen_rate_pct": 5, "change_failure_rate_pct": 10},
}

HORIZON_CONTRACT = {
    **ATLAS_CONTRACT,
    "run_fixed_monthly_fee": 14500,
    "rate_card": {"confirmed": 590, "senior": 720},
    "work_units": {"UO-S": {"label": "Petite évolution", "price": 1300, "max_h": 14}, "UO-M": {"label": "Évolution moyenne", "price": 3600, "max_h": 40}},
    "monthly_cap_time_material": 10000,
    "sla": {"P1": {"resolution_h": 2, "target_pct": 98}, "P2": {"resolution_h": 6, "target_pct": 98}, "P3": {"resolution_h": 32, "target_pct": 92}, "P4": {"resolution_h": 72, "target_pct": 92}},
    "kpi_targets": {"mttr_p2_h": 6, "backlog_max": 25, "reopen_rate_pct": 5, "change_failure_rate_pct": 10},
}


def month_start(d: date, back: int) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def at(d: date, hour: float) -> datetime:
    return datetime.combine(d, time(0), UTC) + timedelta(hours=hour)


def workdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5]


INCIDENT_TEMPLATES = {
    "ClaimsPortal": [("Lenteur portail sinistres à l'ouverture d'un dossier", "performance", "Redémarrage du pool de connexions et purge du cache applicatif"), ("Erreur 500 à la soumission d'une déclaration", "anomalie", "Correctif de validation du formulaire déployé"), ("Pièce jointe non chargée sur la déclaration", "anomalie", "Timeout CMIS augmenté")],
    "SinistreCore": [("Blocage instruction dossier sinistre", "anomalie", "Déverrouillage du dossier en base et correctif de gestion des verrous"), ("Calcul de franchise erroné", "anomalie", "Correction du paramétrage de la garantie"), ("Lenteur écran de recherche sinistres", "performance", "Ajout d'un index Oracle")],
    "BatchIndemnisation": [("Échec batch indemnisation nocturne étape IND060", "batch", "Purge du tablespace INDEMN et reprise manuelle de l'étape IND060"), ("Fichier SEPA indemnisation non généré", "batch", "Relance de l'étape IND080 après correction des droits")],
    "GED Documents": [("Saturation mémoire GED en fin de mois", "infrastructure", "Redémarrage Alfresco et augmentation de la heap"), ("Document introuvable dans la GED", "anomalie", "Réindexation Solr")],
    "ReferentielClients": [("Contrat assuré introuvable depuis SinistreCore", "interface", "Resynchronisation du référentiel")],
    "EditiqueDocs": [("Courriers de règlement non générés", "interface", "Relance des messages MQ en erreur")],
    "PortailExperts": [("Expert ne peut pas déposer son rapport", "anomalie", "Réinitialisation du compte local"), ("Page blanche portail experts après connexion", "anomalie", "Correction de compatibilité PHP")],
    "ReportingSinistres": [("Tableau de bord sinistres non rafraîchi", "batch", "Relance de l'ETL nocturne")],
}


def seed_atlas(db) -> None:
    today = date.today()
    client = Client(id="cli-atlas", name="Atlas Assurances", sector="Assurance")
    proj = DeliveryProject(id="prj-atlas", client=client, name="AMS Atlas Assurances", description="TMA du SI Sinistres et applications connexes", manager="karim", technologies=["Java 17", "Spring", "Angular", "PostgreSQL", "Business Objects"])
    p = Perimeter(id="per-atlas-sinistres", client=client, project=proj, name="SI Sinistres", description="Déclaration, instruction et indemnisation des sinistres IARD", contract=ATLAS_CONTRACT)
    db.add_all([client, proj, p])
    db.flush()  # Application.perimeter_id is a plain FK column, not a relationship: SQLAlchemy
    # can only order inserts correctly across mappers it has a dependency edge for, so the parent
    # row must exist before children referencing it are added to the session (Postgres enforces
    # the FK immediately; SQLite doesn't unless PRAGMA foreign_keys=ON, which is why this was
    # invisible against the dev/test SQLite database and only broke against real Postgres).
    # ProjectMember.project_id is likewise a plain FK, so add these only after the project is flushed.
    db.add_all([ProjectMember(project_id=proj.id, owner="karim", role_in_project="manager", added_by="karim"),
                ProjectMember(project_id=proj.id, owner="tom", role_in_project="member", added_by="karim")])

    app_specs = [
        ("ClaimsPortal", "Portail de déclaration des sinistres", "Angular 12 / Spring Boot", "critical", "Direction Indemnisation", 3500),
        ("SinistreCore", "Cœur de gestion des sinistres", "Java EE 7 / WebLogic / Oracle 12c", "critical", "Direction Indemnisation", 1200),
        ("BatchIndemnisation", "Calcul et émission des paiements", "COBOL / Control-M", "critical", "Direction Financière", 0),
        ("GED Documents", "Gestion électronique des pièces", "Alfresco 5.2", "high", "Direction Indemnisation", 1200),
        ("ReferentielClients", "Référentiel des assurés", "MDM Java / Oracle", "high", "DSI Transverse", 0),
        ("EditiqueDocs", "Production des courriers", "OpenText Exstream", "medium", "Direction Relation Client", 0),
        ("PortailExperts", "Dépôt des rapports d'expertise", "PHP 5.6 / MySQL", "medium", "Direction Indemnisation", 3500),
        ("ReportingSinistres", "Tableaux de bord métier", "Business Objects", "low", "Contrôle de gestion", 150),
        ("ArchivageLegal", "Archivage à valeur probante des dossiers clos", "Solution SaaS tierce", "high", "Direction Juridique", 20),
    ]
    apps = {}
    for name, desc, tech, crit, owner, users in app_specs:
        a = Application(perimeter_id=p.id, name=name, description=desc, technology=tech, criticality=crit, business_owner=owner, users_count=users)
        db.add(a)
        apps[name] = a
    db.flush()
    for src, tgt, kind, label in [
        ("ClaimsPortal", "SinistreCore", "api", "REST déclaration / consultation"),
        ("ClaimsPortal", "GED Documents", "api", "CMIS pièces jointes"),
        ("SinistreCore", "ReferentielClients", "api", "SOAP vérification contrat"),
        ("BatchIndemnisation", "SinistreCore", "db", "Lecture tables Oracle"),
        ("SinistreCore", "EditiqueDocs", "mq", "Demandes de courriers IBM MQ"),
        ("PortailExperts", "SinistreCore", "api", "Dépôt rapports"),
        ("ReportingSinistres", "SinistreCore", "db", "ETL nocturne"),
        ("GED Documents", "ReferentielClients", "api", "Indexation par assuré"),
        ("ArchivageLegal", "GED Documents", "file", "Versement des dossiers clos"),
    ]:
        db.add(Dependency(perimeter_id=p.id, source_id=apps[src].id, target_id=apps[tgt].id, kind=kind, description=label))

    absent_start = today + timedelta(days=(7 - today.weekday()) + 7)  # Monday in ~2 weeks
    team_specs = [
        ("Nadia Belkacem", "senior", ["java", "oracle", "weblogic"], []),
        ("Julien Moreau", "confirmed", ["angular", "java"], []),
        ("Mehdi Ouali", "senior", ["cobol", "controlm", "oracle"], [{"start": absent_start.isoformat(), "end": (absent_start + timedelta(days=11)).isoformat()}]),
        ("Claire Dubois", "junior", ["php", "support"], []),
        ("Paul Lefèvre", "architect", ["architecture", "oracle", "java"], []),
        ("Inès Charbonnier", "confirmed", ["alfresco", "exstream", "java"], []),
    ]
    team = {}
    for name, profile, skills, absences in team_specs:
        m = TeamMember(perimeter_id=p.id, name=name, profile=profile, skills=skills, weekly_capacity_h=35, absences=absences)
        db.add(m)
        team[name.split()[0]] = m
    db.flush()

    expert_for = {"ClaimsPortal": ["Julien", "Nadia"], "SinistreCore": ["Nadia", "Paul", "Julien"], "BatchIndemnisation": ["Mehdi"], "GED Documents": ["Inès", "Nadia"],
                  "ReferentielClients": ["Paul", "Nadia"], "EditiqueDocs": ["Inès"], "PortailExperts": ["Claire", "Julien"], "ReportingSinistres": ["Paul", "Claire"]}

    tickets: list[Ticket] = []
    counter = {"INC": 4000, "REQ": 2000, "CHG": 3000}

    def new_ref(prefix):
        counter[prefix] += 1
        return f"{prefix}{counter[prefix]}"

    first = month_start(today, 6)
    last_month = month_start(today, 1)
    # --- incidents over 6 months, volume rising, SLA degrading last month ---
    for back in range(6, -1, -1):
        m0 = month_start(today, back)
        m_end = min(month_start(today, back - 1) - timedelta(days=1) if back > 0 else today, today)
        volume = 14 + (6 - back) * 2 if back > 0 else max(4, today.day // 2)
        breach_rate = 0.08 if back > 1 else (0.3 if back == 1 else 0.15)
        for _ in range(volume):
            app_name = rng.choices(list(INCIDENT_TEMPLATES), weights=[4, 4, 2, 3, 1, 1, 2, 1])[0]
            title, category, resolution = rng.choice(INCIDENT_TEMPLATES[app_name])
            if app_name == "BatchIndemnisation" and back > 3:
                continue  # batch failures are a recent phenomenon (month-end volumes)
            prio = rng.choices(["P1", "P2", "P3", "P4"], weights=[1, 4, 8, 3])[0]
            day = m0 + timedelta(days=rng.randrange(max((m_end - m0).days, 1)))
            opened = at(day, rng.uniform(7, 18))
            sla_h = ATLAS_CONTRACT["sla"][prio]["resolution_h"]
            breach = rng.random() < breach_rate
            res_h = sla_h * (rng.uniform(1.1, 2.5) if breach else rng.uniform(0.2, 0.9))
            resolved = opened + timedelta(hours=res_h)
            is_open = resolved > datetime.now(UTC) or (back == 0 and rng.random() < 0.35)
            assignee = team[rng.choice(expert_for[app_name])]
            tickets.append(Ticket(perimeter_id=p.id, ref=new_ref("INC"), kind="incident", priority=prio, application_id=apps[app_name].id, category=category, title=title,
                                  description=f"{title}. Signalé par le support N1.", status="in_progress" if is_open else "closed", assignee_id=assignee.id, opened_at=opened,
                                  resolved_at=None if is_open else resolved, reopened=1 if rng.random() < 0.06 else 0, resolution="" if is_open else resolution, estimate_h=round(min(res_h, 16), 1)))

    # --- recurring batch failures in the last 60 days (problem candidate, Mehdi-only knowledge) ---
    for i in range(5):
        day = today - timedelta(days=55 - i * 11)
        opened = at(day, 1.5)
        tickets.append(Ticket(perimeter_id=p.id, ref=new_ref("INC"), kind="incident", priority="P2", application_id=apps["BatchIndemnisation"].id, category="batch",
                              title="Échec batch indemnisation nocturne étape IND060", description="Le batch s'arrête en erreur ORA-01653 sur le tablespace INDEMN. Paiements retardés.",
                              status="closed", assignee_id=team["Mehdi"].id, opened_at=opened, resolved_at=opened + timedelta(hours=rng.choice([5, 9, 11, 7, 12])), resolution="Purge du tablespace INDEMN et reprise manuelle de l'étape IND060", estimate_h=6))

    # --- open duplicates ---
    for h in (0, 5):
        tickets.append(Ticket(perimeter_id=p.id, ref=new_ref("INC"), kind="incident", priority="P3", application_id=apps["GED Documents"].id, category="infrastructure",
                              title="Saturation mémoire GED en fin de mois", description="Les gestionnaires ne peuvent plus consulter les pièces.", status="open", opened_at=at(today - timedelta(days=1), 9 + h)))

    # --- changes (work units), resolved last month, one overrun, one failed ---
    change_specs = [
        ("Ajout du motif de refus sur le courrier de règlement", "UO-S", "EditiqueDocs", "Inès", 12),
        ("Nouveau filtre par expert sur l'écran de recherche", "UO-S", "SinistreCore", "Julien", 22),  # overrun
        ("Export CSV des dossiers en attente d'expertise", "UO-M", "ClaimsPortal", "Julien", 34),
        ("Contrôle de cohérence IBAN avant indemnisation", "UO-M", "BatchIndemnisation", "Mehdi", 38),
        ("Refonte du parcours de déclaration mobile", "UO-L", "ClaimsPortal", "Nadia", 70),
    ]
    change_hours = {}
    for title, uo, app_name, who, hours in change_specs:
        ref = new_ref("CHG")
        resolved = at(last_month + timedelta(days=rng.randrange(5, 25)), 17)
        tickets.append(Ticket(perimeter_id=p.id, ref=ref, kind="change", priority="P3", application_id=apps[app_name].id, category=uo, title=title, description=title,
                              status="closed", assignee_id=team[who].id, opened_at=resolved - timedelta(days=20), resolved_at=resolved, resolution="Livré en production",
                              estimate_h=ATLAS_CONTRACT["work_units"][uo]["max_h"], failed_change=title.startswith("Contrôle")))
        change_hours[ref] = (team[who], hours, resolved.date())
    # historical changes for estimation
    for i, (title, uo, app_name, hours) in enumerate([
        ("Ajout d'un champ commentaire sur le courrier d'indemnisation", "UO-S", "EditiqueDocs", 10), ("Filtre par date sur l'écran de recherche sinistres", "UO-S", "SinistreCore", 13),
        ("Export Excel des sinistres clos", "UO-M", "ClaimsPortal", 30), ("Nouveau contrôle de doublon de déclaration", "UO-M", "SinistreCore", 36),
        ("Ajout d'un statut expertise en cours sur le portail", "UO-S", "PortailExperts", 11), ("Export CSV des paiements rejetés", "UO-M", "BatchIndemnisation", 28),
    ]):
        opened = at(month_start(today, 5) + timedelta(days=i * 12), 9)
        ref = new_ref("CHG")
        tickets.append(Ticket(perimeter_id=p.id, ref=ref, kind="change", priority="P3", application_id=apps[app_name].id, category=uo, title=title, description=title, status="closed",
                              assignee_id=team["Julien"].id, opened_at=opened, resolved_at=opened + timedelta(days=15), resolution="Livré", estimate_h=hours))
        change_hours[ref] = (team["Julien"], hours, (opened + timedelta(days=10)).date())

    # --- out-of-scope requests absorbed in RUN ---
    oos = [
        Ticket(perimeter_id=p.id, ref=new_ref("REQ"), kind="request", priority="P3", application_id=apps["GED Documents"].id, category="demande", title="Migration des pièces 2015-2018 vers le stockage objet",
               description="Le métier demande la migration des documents historiques.", status="in_progress", assignee_id=team["Inès"].id, opened_at=at(last_month + timedelta(days=3), 10)),
        Ticket(perimeter_id=p.id, ref=new_ref("REQ"), kind="request", priority="P4", application_id=apps["PortailExperts"].id, category="demande", title="Formation utilisateurs des nouveaux experts partenaires",
               description="Session de formation utilisateurs sur le portail.", status="closed", assignee_id=team["Claire"].id, opened_at=at(last_month + timedelta(days=8), 10), resolved_at=at(last_month + timedelta(days=9), 17), resolution="Formation réalisée"),
    ]
    tickets += oos
    db.add_all(tickets)
    db.flush()

    # --- timesheets: project work first, then daily run activity fills each day up to 7 h ---
    by_member_incidents: dict[str, list[Ticket]] = {}
    for t in tickets:
        if t.kind == "incident" and t.assignee_id:
            by_member_incidents.setdefault(t.assignee_id, []).append(t)
    start = today - timedelta(weeks=13)
    planned: dict[tuple[str, date], float] = {}

    def book(member, d, code, hours, ref=None, comment="", counts=True):
        db.add(Timesheet(perimeter_id=p.id, member_id=member.id, day=d, activity_code=code, ticket_ref=ref, hours=hours, comment=comment))
        if counts:
            planned[(member.id, d)] = planned.get((member.id, d), 0) + hours

    for ref, (member, hours, end_day) in change_hours.items():
        days = workdays(end_day - timedelta(days=int(hours / 3.5 * 1.4) + 1), end_day)
        for d in days:
            book(member, d, "UO", round(hours / len(days), 1), ref)

    lm_days = workdays(last_month, last_month + timedelta(days=27))
    regie = Ticket(perimeter_id=p.id, ref=new_ref("CHG"), kind="change", priority="P3", application_id=apps["SinistreCore"].id, category="REGIE",
                   title="Assistance à la recette de la montée de version Oracle 19c", description="Accompagnement en régie demandé par la DSI.", status="closed",
                   assignee_id=team["Paul"].id, opened_at=at(last_month, 9), resolved_at=at(last_month + timedelta(days=25), 18), resolution="Recette terminée")
    db.add(regie)
    for d in lm_days[:16]:  # pushes régie over the monthly cap
        book(team["Paul"], d, "EVOL", 4, regie.ref)
        book(team["Nadia"], d, "EVOL", 3.5, regie.ref)
    uo_ticket = next(r for r, v in change_hours.items() if v[0] is team["Nadia"])
    for d in lm_days[:8]:  # régie logged on a work-unit ticket: double billing
        book(team["Paul"], d, "EVOL", 2, uo_ticket)

    # injected anomalies last month
    lm_day = next(d for d in lm_days if d.day >= 12)
    claims_inc = next(t for t in tickets if t.kind == "incident" and t.priority == "P2" and t.application_id == apps["ClaimsPortal"].id)
    book(team["Julien"], lm_day, "EVOL", 6, claims_inc.ref, "Analyse")
    book(team["Claire"], lm_day, "EVOL", 7, None, "Support divers")
    book(team["Nadia"], lm_day + timedelta(days=1), "AVV", 3)
    for i, t in enumerate(oos):
        book(next(m for m in team.values() if m.id == t.assignee_id), lm_day + timedelta(days=i + 2), "RUN", 6, t.ref)

    for m in team.values():
        absences = [(date.fromisoformat(a["start"]), date.fromisoformat(a["end"])) for a in m.absences]
        for d in workdays(start, today - timedelta(days=1)):
            if any(s <= d <= e for s, e in absences):
                continue
            if m.name.startswith("Inès") and d.year == last_month.year and d.month == last_month.month and d.day in (6, 7, 8, 9, 10, 13):
                continue  # missing timesheets anomaly
            remaining = round(7 - planned.get((m.id, d), 0), 1)
            if remaining <= 0:
                continue
            candidates = [t for t in by_member_incidents.get(m.id, []) if t.opened_at.date() <= d <= (t.resolved_at or datetime.now(UTC)).date() + timedelta(days=1)]
            if candidates:
                book(m, d, "RUN", round(remaining * 0.6, 1), rng.choice(candidates).ref)
                book(m, d, "MCO", round(remaining * 0.4, 1))
            else:
                book(m, d, rng.choice(["MCO", "MCO", "INTERNAL"]), remaining)
    book(team["Paul"], lm_day, "RUN", 5, None, "Incident P1 nuit", counts=False)  # 12 h day

    # --- backlog ---
    for title, skill, est, prio, due_in, who in [
        ("Montée de version Angular 12 → 17 ClaimsPortal", "angular", 120, 1, 35, None),
        ("Correctif calcul franchise multi-garanties", "java", 24, 1, 10, "Nadia"),
        ("Automatisation reprise IND060", "cobol", 60, 1, 21, None),
        ("Migration PHP 5.6 → 8.2 PortailExperts", "php", 160, 1, 42, None),
        ("Supervision fonctionnelle EditiqueDocs", "exstream", 30, 2, 28, None),
        ("Renouvellement automatisé certificat TLS", "architecture", 16, 2, 20, None),
        ("Optimisation requêtes écran recherche", "oracle", 40, 2, 25, "Paul"),
        ("Plan de reprise Oracle — test de bascule", "oracle", 50, 1, 30, None),
        ("Nouveau courrier relance expert", "exstream", 20, 3, 40, "Inès"),
    ]:
        db.add(BacklogItem(perimeter_id=p.id, title=title, required_skill=skill, estimate_h=est, priority=prio, due_date=today + timedelta(days=due_in), assignee_id=team[who].id if who else None))

    for filename, doc_type, content in [("Dossier de transfert SI Sinistres.md", "transfer", docs.ATLAS_TRANSFER), ("DAT SI Sinistres.md", "dat", docs.ATLAS_DAT),
                                        ("DEX SI Sinistres.md", "dex", docs.ATLAS_DEX), ("Contrat TMA Atlas.md", "contract", docs.ATLAS_CONTRACT)]:
        knowledge.ingest_document(db, p.id, filename, doc_type, content)

    db.add(Meeting(perimeter_id=p.id, title="Comité de suivi mensuel", held_at=at(today - timedelta(days=2), 10), transcript=docs.DEMO_MEETING.strip(), status="ended"))


def seed_horizon(db) -> None:
    today = date.today()
    client = Client(id="cli-horizon", name="Banque Horizon", sector="Banque")
    proj = DeliveryProject(id="prj-horizon", client=client, name="TMA Banque Horizon", description="Paiements, monétique et lutte anti-fraude", manager="lea", technologies=["Java 17", "Kafka", "Spring Batch", "Python", "React"])
    p = Perimeter(id="per-horizon-paiements", client=client, project=proj, name="Paiements & Monétique", description="Virements, cartes, fraude et compensation", contract=HORIZON_CONTRACT)
    db.add_all([client, proj, p])
    db.flush()  # see the matching comment in seed_atlas: the parent row must exist before
    # children referencing it by plain FK column are added, or Postgres rejects the insert order.
    db.add(ProjectMember(project_id=proj.id, owner="lea", role_in_project="manager", added_by="lea"))
    apps = {}
    for name, tech, crit in [("PayHub", "Java 17 / Kafka", "critical"), ("CardManager", "Java / PostgreSQL", "high"), ("FraudShield", "Python / ML", "critical"), ("ClearingBatch", "Spring Batch", "high"), ("MerchantPortal", "React", "medium")]:
        apps[name] = Application(perimeter_id=p.id, name=name, technology=tech, criticality=crit, description=name)
        db.add(apps[name])
    db.flush()
    for s, t, k in [("PayHub", "FraudShield", "api"), ("CardManager", "FraudShield", "api"), ("ClearingBatch", "PayHub", "db"), ("MerchantPortal", "CardManager", "api")]:
        db.add(Dependency(perimeter_id=p.id, source_id=apps[s].id, target_id=apps[t].id, kind=k))
    team = []
    for name, profile, skills in [("Hugo Lambert", "senior", ["java", "kafka"]), ("Sofia Rahmani", "confirmed", ["python", "ml"]), ("Lucas Petit", "confirmed", ["java", "react"]), ("Amel Kaci", "senior", ["java", "spring", "postgresql"])]:
        m = TeamMember(perimeter_id=p.id, name=name, profile=profile, skills=skills, weekly_capacity_h=35, absences=[])
        db.add(m)
        team.append(m)
    db.flush()
    n = 5000
    for back in range(6, -1, -1):
        m0 = month_start(today, back)
        for _ in range(8 if back else today.day // 3):
            n += 1
            app = rng.choice(list(apps.values()))
            prio = rng.choices(["P1", "P2", "P3", "P4"], weights=[1, 3, 8, 4])[0]
            opened = at(m0 + timedelta(days=rng.randrange(27)), rng.uniform(8, 18))
            if opened.date() >= today:
                continue
            res = opened + timedelta(hours=HORIZON_CONTRACT["sla"][prio]["resolution_h"] * rng.uniform(0.2, 0.8))
            db.add(Ticket(perimeter_id=p.id, ref=f"HZ-INC{n}", kind="incident", priority=prio, application_id=app.id, category="anomalie", title=f"Anomalie {app.name}", description="", status="closed", assignee_id=rng.choice(team).id, opened_at=opened, resolved_at=res, resolution="Corrigé"))
    for d in workdays(today - timedelta(weeks=13), today - timedelta(days=1)):
        for m in team:
            db.add(Timesheet(perimeter_id=p.id, member_id=m.id, day=d, activity_code="RUN" if rng.random() < 0.5 else "MCO", hours=4.5))
            db.add(Timesheet(perimeter_id=p.id, member_id=m.id, day=d, activity_code="INTERNAL", hours=2.5))
    for title, skill, est in [("Montée de version Kafka 3.x", "kafka", 80), ("Runbook réentraînement FraudShield", "ml", 24), ("Refonte tableau de bord commerçant", "react", 40)]:
        db.add(BacklogItem(perimeter_id=p.id, title=title, required_skill=skill, estimate_h=est, priority=2, due_date=today + timedelta(days=30)))
    knowledge.ingest_document(db, p.id, "Dossier de transfert Paiements.md", "transfer", docs.HORIZON_TRANSFER)
    knowledge.ingest_document(db, p.id, "Contrat TMA Horizon.md", "contract", docs.HORIZON_CONTRACT)


def seed_skills(db) -> None:
    """Competence matrix for the demo users (owner = username, as everywhere else)."""
    matrix = {
        "karim": [("Java 17", 5, "technology"), ("Spring", 4, "technology"), ("Gestion de projet", 5, "method"), ("Anglais", 4, "language")],
        "tom": [("Angular", 4, "technology"), ("TypeScript", 4, "technology"), ("PostgreSQL", 3, "technology"), ("Java 17", 3, "technology")],
        "lea": [("Kafka", 4, "technology"), ("Python", 4, "technology"), ("Spring Batch", 4, "technology"), ("Gestion de projet", 4, "method")],
        "sarah": [("Gestion de projet", 5, "method"), ("Architecture", 5, "functional"), ("Anglais", 5, "language")],
    }
    for owner, skills in matrix.items():
        for skill, level, category in skills:
            db.add(UserSkill(owner=owner, skill=skill, level=level, category=category))


def main(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(Client).limit(1)):
            print("Database already seeded (use --reset to regenerate)")
            return
        seed_atlas(db)
        seed_horizon(db)
        seed_skills(db)
        db.commit()
    print("Seed complete: 2 clients, 2 projects, 2 perimeters")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
