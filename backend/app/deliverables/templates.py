"""Project-management deliverable templates (COPIL, matrice de couverture, …).

The method is the firm's house approach, seeded from client documents (see reference/*.md). Templates
are data so each company can tweak them — the built-ins below are the defaults; per-company overrides
are stored in the DB and merged on top (see effective_template). Adding a new deliverable = adding an
entry here (and a generator in generate.py).
"""

import copy
import unicodedata

# Criticité = grille de risque Impact × Probabilité (source: deck "matrice de couverture").
CRITICITE_IMPACTS = ["Catastrophique", "Grave", "Modéré", "Ennuyeux"]
CRITICITE_PROBAS = ["Inévitable", "Fréquent", "Occasionnel", "Rare"]
CRITICITE_GRID = {
    "Catastrophique": {"Inévitable": 3, "Fréquent": 3, "Occasionnel": 3, "Rare": 2},
    "Grave":          {"Inévitable": 3, "Fréquent": 3, "Occasionnel": 2, "Rare": 1},
    "Modéré":         {"Inévitable": 2, "Fréquent": 2, "Occasionnel": 1, "Rare": 1},
    "Ennuyeux":       {"Inévitable": 2, "Fréquent": 1, "Occasionnel": 1, "Rare": 1},
}


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").strip().lower()


def match_scale(value: str, scale: list[str], default: str) -> str:
    """Map a free-text answer to one of a scale's labels (accent/case-insensitive, prefix match)."""
    v = _norm(value)
    if not v:
        return default
    for label in scale:
        n = _norm(label)
        if v == n or v.startswith(n[:4]) or n.startswith(v[:4]):
            return label
    return default


def criticite(impact: str, proba: str, grid: dict | None = None) -> int:
    grid = grid or CRITICITE_GRID
    imp = match_scale(impact, list(grid.keys()), CRITICITE_IMPACTS[1])
    row = grid.get(imp, {})
    prob = match_scale(proba, list(row.keys()) or CRITICITE_PROBAS, CRITICITE_PROBAS[1])
    return int(row.get(prob, 1))


COPIL = {
    "key": "copil",
    "title": "Support de COPIL",
    "description": "Comité de pilotage : faits marquants, suivi d'activité, plan d'action / décisions / risques, perspectives.",
    "output": "pptx",
    "mode": "questions",   # answered step by step; no input files needed
    "inputs": [],
    "sections": ["Faits marquants", "Suivi de l'activité", "Plan d'action, décisions et risques", "Perspectives et divers"],
    "questions": [
        {"question": "Nom du projet ou périmètre ?", "options": []},
        {"question": "Client / société ?", "options": []},
        {"question": "Date du COPIL et période couverte ?", "options": []},
        {"question": "Actions réalisées depuis le dernier COPIL ? (une par ligne)", "options": []},
        {"question": "Points d'alerte ou de vigilance ? (une par ligne)", "options": []},
        {"question": "Actions en cours et actions à initier ? (une par ligne)", "options": []},
        {"question": "Suivi de l'activité : avancement, charges, jalons, indicateurs ? (une par ligne)", "options": []},
        {"question": "Décisions à acter, risques et plan d'action ? (une par ligne — « action | responsable | échéance » si possible)", "options": []},
        {"question": "Perspectives et divers ? (une par ligne)", "options": []},
    ],
    # which answer (by question index) feeds which section's bullets
    "map": {"projet": 0, "client": 1, "date": 2, "faits_realise": 3, "faits_alerte": 4,
            "faits_encours": 5, "suivi": 6, "plan": 7, "perspectives": 8},
}

MATRICE = {
    "key": "matrice_couverture",
    "title": "Matrice de couverture",
    "description": "Croise exigences métier × cas d'usage, évalue la criticité (Impact × Probabilité) et prépare l'effort de test.",
    "output": "xlsx",
    "impacts": CRITICITE_IMPACTS,
    "probas": CRITICITE_PROBAS,
    "grid": CRITICITE_GRID,
    # mode "extract": the content is EXTRACTED from typed inputs (files / connector data); the app asks
    # clarifying questions only when something is missing. Other companies can declare other inputs.
    "mode": "extract",
    "inputs": [
        {"key": "cahier_des_charges", "label": "Cahier des charges", "kind": "file", "required": True,
         "accept": ".md,.txt,.docx,.pdf,.xlsx", "hint": "Les exigences métier (idéalement avec impact et probabilité)"},
        {"key": "specs", "label": "Spécifications fonctionnelles (client)", "kind": "files", "required": False,
         "accept": ".md,.txt,.docx,.pdf", "hint": "User stories / cas d'usage et les exigences qu'ils couvrent"},
        {"key": "jira", "label": "Tickets Jira", "kind": "connector", "connector": "jira", "required": False,
         "hint": "Données de démonstration pour l'instant — branché au connecteur Jira ensuite"},
    ],
    # legacy free-text path (kept for API compatibility / quick manual use)
    "questions": [
        {"question": "Nom du projet ou périmètre ?", "options": []},
        {"question": "Client / société ?", "options": []},
        {"question": "Exigences métier ? (une par ligne — « exigence | impact | probabilité » si connu)", "options": []},
        {"question": "Cas d'usage / user stories ? (un par ligne)", "options": []},
        {"question": "Couverture connue ? (une par ligne — « cas d'usage : exigence, exigence »)", "options": []},
    ],
    "map": {"projet": 0, "client": 1, "exigences": 2, "cas": 3, "couverture": 4},
}

PLAN = {
    "key": "plan_de_charge",
    "title": "Plan de charge",
    "description": "À partir de la matrice de couverture et de l'équipe affectée : charge par exigence, planning hebdomadaire par ressource, taux d'occupation et coûts.",
    "output": "xlsx",
    "mode": "extract",
    "inputs": [
        {"key": "matrice", "label": "Matrice de couverture", "kind": "files", "required": False, "accept": ".xlsx",
         "hint": "Sinon : la dernière matrice générée pour ce projet est utilisée"},
        {"key": "equipe", "label": "Équipe affectée", "kind": "members", "required": False,
         "hint": "Sinon : toute l'équipe du périmètre (onglet Équipe)"},
        {"key": "technologies", "label": "Technologies retenues", "kind": "text", "required": False,
         "hint": "ex. Angular, Java, PostgreSQL — sert à choisir qui fait quoi (matrice de compétences)"},
    ],
    "questions": [],
    "map": {},
}

BUILTINS = {t["key"]: t for t in (COPIL, MATRICE, PLAN)}


def effective_template(key: str, override: dict | None = None) -> dict | None:
    """Built-in defaults with per-company overrides merged on top (title, questions, scales, grid)."""
    base = BUILTINS.get(key)
    if base is None:
        return None
    t = copy.deepcopy(base)
    for field in ("title", "description", "questions", "inputs", "impacts", "probas", "grid", "sections"):
        if override and override.get(field):
            t[field] = override[field]
    return t


def _prefill_questions(t: dict) -> dict:
    """Which questions can be pre-filled from the périmètre (project name, client), by their text —
    so the Livrables tab answers them from the project and skips those steps."""
    qs = t.get("questions") or []
    mp = t.get("map") or {}
    out = {}
    for field in ("projet", "client"):
        idx = mp.get(field)
        if idx is not None and idx < len(qs):
            out[field] = qs[idx]["question"]
    return out


def public_list(overrides: dict[str, dict] | None = None) -> list[dict]:
    overrides = overrides or {}
    out = []
    for key, base in BUILTINS.items():
        t = effective_template(key, overrides.get(key))
        out.append({"key": key, "title": t["title"], "description": t["description"], "output": t["output"],
                    "mode": t.get("mode", "questions"), "inputs": t.get("inputs") or [],
                    "questions": t["questions"], "prefill": _prefill_questions(t)})
    return out
