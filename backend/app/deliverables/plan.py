"""Plan de charge — from the matrice de couverture + the staffed team to a weekly workload plan.

Charge model (deterministic; the manager is asked what only they know):
  days(exigence) = base_days_per_use_case × nb_use_cases_covering_it × factor(criticité)
  factor: criticité 3 → 1.5, 2 → 1.0, 1 → 0.7 (a critical requirement needs more test effort)
Each exigence is assigned among the team members whose skills match the technologies, to the least
loaded one relative to capacity (skill score breaks ties) and scheduled week by week within that person's availability (capacity − absences).
Costs = days × TJM of the person's profile (contract rate card), so the plan feeds billing.

The workbook mirrors the firm's plan de charge: a weekly grid (months / S-weeks / dates / working
days), a calendar of availability per resource, the charge per exigence and per resource, occupation
rates, plus Congés, Charge.appli (with costs), Param and Périmètre sheets.
"""

import io
import re
from datetime import date, timedelta

from app.deliverables import templates as T

CRIT_FACTOR = {3: 1.5, 2: 1.0, 1: 0.7}
DEFAULT_RATE = 550.0
HOURS_PER_DAY = 7.0
MONTHS_FR = ["Janv.", "Fév.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]

Q_BASE = "Charge moyenne pour couvrir un cas d'usage (en jours-homme) ?"
Q_START = "Date de début du plan (AAAA-MM-JJ) ?"
Q_HORIZON = "Sur combien de semaines planifier ?"
Q_TEAM = "Qui travaille sur ce projet ? (noms séparés par des virgules)"
Q_RATE = "Taux journalier moyen à appliquer (TJM, en €) ?"


def _next_monday(d: date | None = None) -> date:
    d = d or date.today()
    return d + timedelta(days=(7 - d.weekday()) % 7 or 7)


def _to_float(s: str, default: float) -> float:
    try:
        return float(str(s).replace(",", ".").replace("j", "").replace("€", "").strip())
    except (TypeError, ValueError):
        return default


def _absent_days(absences: list, week_start: date) -> int:
    n = 0
    for a in absences or []:
        try:
            s = date.fromisoformat(str(a.get("start"))[:10])
            e = date.fromisoformat(str(a.get("end"))[:10])
        except (TypeError, ValueError, AttributeError):
            continue
        for i in range(5):
            d = week_start + timedelta(days=i)
            if s <= d <= e:
                n += 1
    return n


def _covers(case: dict, ex: dict) -> bool:
    exref, exn = T._norm(ex.get("ref", "")), T._norm(ex.get("name", ""))
    for w in case.get("exigences") or []:
        wn = T._norm(w)
        if not wn:
            continue
        if exref and wn == exref:
            return True
        if exn and (wn in exn or exn in wn):
            return True
    return False


# ---------------------------------------------------------------- questions

def clarifying_questions(members: list[dict], contract: dict, answers: dict) -> list[dict]:
    qs: list[dict] = []
    if Q_BASE not in answers:
        qs.append({"question": Q_BASE, "options": ["0,5", "1", "2", "3"], "recommended": "1"})
    if Q_START not in answers:
        nm = _next_monday()
        qs.append({"question": Q_START, "options": [nm.isoformat(), (nm + timedelta(days=7)).isoformat(), (nm + timedelta(days=14)).isoformat()], "recommended": nm.isoformat()})
    if Q_HORIZON not in answers:
        qs.append({"question": Q_HORIZON, "options": ["8", "12", "16", "26"], "recommended": "12"})
    if not members and Q_TEAM not in answers:
        qs.append({"question": Q_TEAM, "options": [], "recommended": ""})
    if not (contract or {}).get("rate_card") and Q_RATE not in answers:
        qs.append({"question": Q_RATE, "options": ["450", "550", "650", "800"], "recommended": "550"})
    return qs[:5]


# ---------------------------------------------------------------- the extractor / planner

def build_plan(matrice: dict, members: list[dict], contract: dict, technologies: list[str], answers: dict) -> dict:
    """matrice draft {exigences, cas_usage} + team + contract + answers → the plan draft."""
    base = _to_float(answers.get(Q_BASE, ""), 1.0) or 1.0
    try:
        start = date.fromisoformat(str(answers.get(Q_START, ""))[:10])
    except (TypeError, ValueError):
        start = _next_monday()
    start = start - timedelta(days=start.weekday())            # always a Monday
    weeks_n = int(_to_float(answers.get(Q_HORIZON, ""), 12)) or 12
    weeks_n = max(1, min(weeks_n, 60))
    rate_card = dict((contract or {}).get("rate_card") or {})
    default_rate = _to_float(answers.get(Q_RATE, ""), DEFAULT_RATE) if Q_RATE in answers else DEFAULT_RATE
    hpd = float((contract or {}).get("hours_per_day") or HOURS_PER_DAY)

    if not members and answers.get(Q_TEAM):
        members = [{"name": n.strip(), "profile": "confirmed", "weekly_capacity_h": 35.0, "absences": [], "skills": []}
                   for n in str(answers[Q_TEAM]).split(",") if n.strip()]
    if not members:
        members = [{"name": "Ressource à staffer", "profile": "confirmed", "weekly_capacity_h": 35.0, "absences": [], "skills": []}]

    weeks = []
    for i in range(weeks_n):
        ws = start + timedelta(days=7 * i)
        weeks.append({"idx": i, "label": f"S{ws.isocalendar()[1]}", "start": ws.isoformat(),
                      "month": f"{MONTHS_FR[ws.month - 1]} '{str(ws.year)[2:]}", "working_days": 5})

    # team capacity per week (days), minus absences
    team = []
    wanted = [T._norm(t) for t in technologies if str(t).strip()]
    for m in members:
        cap_week = min(5.0, float(m.get("weekly_capacity_h") or 35.0) / hpd)
        caps = [max(0.0, round(cap_week - _absent_days(m.get("absences") or [], date.fromisoformat(w["start"])), 2)) for w in weeks]
        skills = [T._norm(s) for s in (m.get("skills") or [])]
        score = sum(1 for s in skills for w in wanted if w and (w == s or w in s or s in w))
        rate = float(rate_card.get(m.get("profile") or "", 0) or default_rate)
        team.append({"name": m.get("name") or "?", "profile": m.get("profile") or "confirmed", "rate": rate, "score": score,
                     "capacity_days": caps, "alloc": [0.0] * weeks_n, "total_days": 0.0, "cursor": 0, "absences": m.get("absences") or []})

    # charge per exigence
    cases = matrice.get("cas_usage") or []
    exigences = []
    for e in matrice.get("exigences") or []:
        impact = T.match_scale(e.get("impact") or "", T.CRITICITE_IMPACTS, T.CRITICITE_IMPACTS[1])
        proba = T.match_scale(e.get("probabilite") or e.get("proba") or "", T.CRITICITE_PROBAS, T.CRITICITE_PROBAS[1])
        crit = int(e.get("crit") or T.criticite(impact, proba))
        n_cases = sum(1 for c in cases if _covers(c, e)) or 1
        days = round(base * n_cases * CRIT_FACTOR.get(crit, 1.0), 2)
        exigences.append({"ref": str(e.get("ref") or ""), "name": str(e.get("name") or ""), "crit": crit, "cases": n_cases, "days": days})
    exigences.sort(key=lambda x: (-x["crit"], -x["days"]))

    # assignment + weekly scheduling
    unscheduled = 0.0
    for ex in exigences:
        ex["assignee"], ex["cost"], ex["week_start"], ex["week_end"] = "", 0.0, None, None
        if not team:
            unscheduled += ex["days"]
            continue
        # Anyone with a relevant skill is eligible; the work goes to whoever is least loaded relative to
        # their capacity (skill score breaks ties). Picking the top scorer outright put 100 % of the charge
        # on one person while equally qualified teammates stayed at 0.
        who = min(team, key=lambda m: (m["score"] <= 0, m["total_days"] / max(sum(m["capacity_days"]), 1.0), -m["score"], m["name"]))
        ex["assignee"] = who["name"]
        ex["cost"] = round(ex["days"] * who["rate"], 2)
        remaining = ex["days"]
        first = None
        while remaining > 1e-9 and who["cursor"] < weeks_n:
            w = who["cursor"]
            free = who["capacity_days"][w] - who["alloc"][w]
            if free <= 1e-9:
                who["cursor"] += 1
                continue
            take = min(free, remaining)
            who["alloc"][w] = round(who["alloc"][w] + take, 2)
            who["total_days"] = round(who["total_days"] + take, 2)
            remaining -= take
            first = w if first is None else first
            ex["week_end"] = weeks[w]["label"]
            if who["alloc"][w] >= who["capacity_days"][w] - 1e-9:
                who["cursor"] += 1
        ex["week_start"] = weeks[first]["label"] if first is not None else None
        if remaining > 1e-9:
            unscheduled += remaining
        # deterministic re-sort key for the next pick: balance by load
        team.sort(key=lambda m: m["name"])

    for m in team:
        m["occupation"] = [round(a / c, 2) if c else 0.0 for a, c in zip(m["alloc"], m["capacity_days"])]
        m["cost"] = round(m["total_days"] * m["rate"], 2)
        m.pop("cursor", None)
    total_days = round(sum(e["days"] for e in exigences), 2)
    total_cost = round(sum(e["cost"] for e in exigences), 2)
    notes = []
    if unscheduled > 1e-9:
        notes.append(f"{round(unscheduled, 1)} jour(s) n'ont pas pu être planifiés sur {weeks_n} semaines : allongez l'horizon ou ajoutez une ressource.")
    if not rate_card:
        notes.append(f"Aucune grille tarifaire dans le contrat : TJM par défaut {default_rate:.0f} € appliqué.")
    return {"params": {"base_days": base, "start": start.isoformat(), "weeks": weeks_n, "technologies": technologies,
                       "rate_default": default_rate, "hours_per_day": hpd, "factors": CRIT_FACTOR},
            "weeks": weeks, "exigences": exigences, "members": team,
            "totals": {"days": total_days, "cost": total_cost, "unscheduled_days": round(unscheduled, 2)}, "notes": notes}


def extract_plan(template: dict, matrice: dict, members: list[dict], contract: dict, technologies: list[str], answers: dict | None = None) -> dict:
    answers = answers or {}
    questions = clarifying_questions(members, contract, answers)
    if questions:
        return {"draft": {"exigences_count": len(matrice.get("exigences") or []), "members": [m.get("name") for m in members]},
                "questions": questions, "used_llm": False, "sources": ["matrice", "équipe", "contrat"]}
    return {"draft": build_plan(matrice, members, contract, technologies, answers), "questions": [], "used_llm": False,
            "sources": ["matrice", "équipe", "contrat"]}


# ---------------------------------------------------------------- parse a matrice workbook back into a draft

def parse_matrice_xlsx(data: bytes) -> dict:
    """Reads a workbook produced by generate_matrice_from_draft (sheet 'Matrice') back into a draft."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)
    if "Matrice" not in wb.sheetnames:
        raise ValueError("Ce classeur ne contient pas de feuille « Matrice »")
    ws = wb["Matrice"]
    exigences, cases = [], []
    col = 2
    while ws.cell(1, col).value:
        head = str(ws.cell(1, col).value)
        m = re.match(r"^\s*([A-Z]{2,6}-\d{1,4})\s*[—–-]\s*(.+)$", head)
        ref, name = (m.group(1), m.group(2)) if m else ("", head)
        exigences.append({"ref": ref, "name": name.strip(), "impact": str(ws.cell(2, col).value or ""),
                          "probabilite": str(ws.cell(3, col).value or ""), "crit": ws.cell(4, col).value})
        col += 1
    row = 5
    while ws.cell(row, 1).value:
        head = str(ws.cell(row, 1).value)
        m = re.match(r"^\s*([A-Z]{2,6}-\d{1,4})\s*[—–-]\s*(.+)$", head)
        ref, name = (m.group(1), m.group(2)) if m else ("", head)
        covered = [exigences[j]["ref"] or exigences[j]["name"] for j in range(len(exigences)) if str(ws.cell(row, 2 + j).value or "").strip().upper() == "X"]
        cases.append({"ref": ref, "name": name.strip(), "exigences": covered})
        row += 1
    return {"exigences": exigences, "cas_usage": cases}


# ---------------------------------------------------------------- the workbook

def generate_plan_from_draft(template: dict, draft: dict, projet: str = "Projet") -> tuple[str, str, bytes]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    weeks = draft.get("weeks") or []
    members = draft.get("members") or []
    exigences = draft.get("exigences") or []
    params = draft.get("params") or {}
    head = Font(bold=True)
    grey = PatternFill("solid", fgColor="EDEDED")
    W0 = 6                                           # first week column (A..E are labels)

    wb = Workbook()
    ws = wb.active
    ws.title = "Plan de charge"
    ws.cell(1, 1, f"Plan de charge — {projet}").font = Font(bold=True, size=13)
    for j, w in enumerate(weeks):
        c = W0 + j
        if j == 0 or weeks[j - 1]["month"] != w["month"]:
            ws.cell(2, c, w["month"]).font = head
        ws.cell(3, c, w["label"]).font = head
        ws.cell(4, c, w["start"])
        ws.cell(5, c, w["working_days"])
    ws.cell(3, 1, "Semaine").font = head
    ws.cell(4, 1, "Début").font = head
    ws.cell(5, 1, "Jours ouvrés").font = head

    r = 7
    ws.cell(r, 1, "Gestion du calendrier projet (disponibilité en jours)").font = head
    r += 1
    for lab, cidx in (("Ressource", 1), ("Profil", 2), ("TJM", 3), ("Total j", 4), ("Taux moy.", 5)):
        ws.cell(r, cidx, lab).font = head
    r += 1
    for m in members:
        ws.cell(r, 1, m["name"]); ws.cell(r, 2, m["profile"]); ws.cell(r, 3, m["rate"])
        for j, cap in enumerate(m["capacity_days"]):
            ws.cell(r, W0 + j, cap)
        r += 1

    r += 1
    ws.cell(r, 1, "Détail des activités projet (charge en jours par exigence)").font = head
    r += 1
    for lab, cidx in (("Réf", 1), ("Exigence", 2), ("Criticité", 3), ("Ressource", 4), ("Jours", 5)):
        ws.cell(r, cidx, lab).font = head
    r += 1
    # per-exigence weekly spread: proportional to the assignee's weekly allocation between its start/end
    for e in exigences:
        ws.cell(r, 1, e["ref"]); ws.cell(r, 2, e["name"]); ws.cell(r, 3, e["crit"]); ws.cell(r, 4, e["assignee"]); ws.cell(r, 5, e["days"])
        if e.get("week_start") and e.get("week_end"):
            labels = [w["label"] for w in weeks]
            a, b = labels.index(e["week_start"]), labels.index(e["week_end"])
            span = max(1, b - a + 1)
            per = round(e["days"] / span, 2)
            for j in range(a, b + 1):
                ws.cell(r, W0 + j, per)
        r += 1

    r += 1
    ws.cell(r, 1, "Récapitulatif par ressource (charge en jours)").font = head
    r += 1
    for lab, cidx in (("Ressource", 1), ("Profil", 2), ("TJM", 3), ("Total j", 4), ("Coût €", 5)):
        ws.cell(r, cidx, lab).font = head
    r += 1
    first_alloc_row = r
    for m in members:
        ws.cell(r, 1, m["name"]); ws.cell(r, 2, m["profile"]); ws.cell(r, 3, m["rate"]); ws.cell(r, 4, m["total_days"]); ws.cell(r, 5, m["cost"])
        for j, a in enumerate(m["alloc"]):
            ws.cell(r, W0 + j, a)
        r += 1
    ws.cell(r, 1, "Total charges").font = head
    ws.cell(r, 4, draft.get("totals", {}).get("days")); ws.cell(r, 5, draft.get("totals", {}).get("cost"))
    for j in range(len(weeks)):
        ws.cell(r, W0 + j, round(sum(m["alloc"][j] for m in members), 2)).font = head
    r += 1
    ws.cell(r, 1, "Capacité restante").font = head
    for j in range(len(weeks)):
        ws.cell(r, W0 + j, round(sum(m["capacity_days"][j] - m["alloc"][j] for m in members), 2))
    r += 2

    ws.cell(r, 1, "Taux d'occupation").font = head
    r += 1
    for m in members:
        ws.cell(r, 1, m["name"])
        for j, occ in enumerate(m["occupation"]):
            c = ws.cell(r, W0 + j, occ)
            c.number_format = "0%"
            if occ > 1.0:
                c.fill = PatternFill("solid", fgColor="F4C0C0")
            elif occ >= 0.9:
                c.fill = PatternFill("solid", fgColor="F7E1B5")
        r += 1
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 34
    ws.freeze_panes = ws.cell(6, W0)

    cg = wb.create_sheet("Congés")
    for j, lab in enumerate(("Ressource", "Début", "Fin"), 1):
        cg.cell(1, j, lab).font = head
    rr = 2
    for m in members:
        for a in m.get("absences") or []:
            cg.cell(rr, 1, m["name"]); cg.cell(rr, 2, str(a.get("start", ""))[:10]); cg.cell(rr, 3, str(a.get("end", ""))[:10]); rr += 1

    ca = wb.create_sheet("Charge.appli")
    for j, lab in enumerate(("Réf", "Exigence", "Criticité", "Cas d'usage couvrants", "Jours", "Ressource", "Début", "Fin", "Coût €"), 1):
        ca.cell(1, j, lab).font = head
    for i, e in enumerate(exigences, 2):
        for j, v in enumerate((e["ref"], e["name"], e["crit"], e["cases"], e["days"], e["assignee"], e.get("week_start"), e.get("week_end"), e["cost"]), 1):
            ca.cell(i, j, v)
    ca.column_dimensions["B"].width = 44

    pa = wb.create_sheet("Param")
    rows = [("Charge de base par cas d'usage (j)", params.get("base_days")), ("Début", params.get("start")), ("Semaines", params.get("weeks")),
            ("Heures par jour", params.get("hours_per_day")), ("TJM par défaut (€)", params.get("rate_default")),
            ("Technologies", ", ".join(params.get("technologies") or [])),
            ("Facteur criticité 3 / 2 / 1", " / ".join(str(v) for v in (params.get("factors") or {}).values()))]
    for i, (k, v) in enumerate(rows, 1):
        pa.cell(i, 1, k).font = head; pa.cell(i, 2, v)
    pa.cell(len(rows) + 2, 1, "TJM par profil").font = head
    for i, m in enumerate({m["profile"]: m["rate"] for m in members}.items(), len(rows) + 3):
        pa.cell(i, 1, m[0]); pa.cell(i, 2, m[1])
    pa.column_dimensions["A"].width = 36

    pe = wb.create_sheet("Périmètre")
    t = draft.get("totals") or {}
    info = [("Projet", projet), ("Exigences", len(exigences)), ("Ressources", len(members)), ("Charge totale (j)", t.get("days")),
            ("Coût total (€)", t.get("cost")), ("Début", params.get("start")), ("Horizon (semaines)", params.get("weeks")),
            ("Jours non planifiés", t.get("unscheduled_days"))]
    for i, (k, v) in enumerate(info, 1):
        pe.cell(i, 1, k).font = head; pe.cell(i, 2, v)
    for i, n in enumerate(draft.get("notes") or [], len(info) + 2):
        pe.cell(i, 1, n)
    pe.column_dimensions["A"].width = 26

    buf = io.BytesIO()
    wb.save(buf)
    slug = "".join(ch if ch.isalnum() else "_" for ch in T._norm(projet))[:40].strip("_") or "projet"
    return f"Plan_de_charge_{slug}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue()
