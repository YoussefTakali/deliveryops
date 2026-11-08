"""Static validation of a workflow (trigger + steps + inputs) before it's saved/activated/compiled.

Returns {"errors": [{step_id?, message}], "warnings": [{step_id?, message}]} — errors block
activation, warnings are shown to the user. Messages are French (displayed as-is in the UI).
"""

import re
from typing import Any

from app.automations import catalog
from app.automations.schedule import schedule_errors

EXPR = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_ONLY_EXPR = re.compile(r"^\s*\{\{\s*[^{}]+?\s*\}\}\s*$")
_STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
_INPUT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
_ROOTS = {"steps", "inputs", "trigger", "item", "index", "now", "today", "run", "row", "row_index", "row_total"}
_FALSY_EMPTY = (None, "", [], {})


def expressions(value: Any) -> list[str]:
    """Every {{…}} expression inside a (possibly nested) param value."""
    found: list[str] = []
    if isinstance(value, str):
        found.extend(m.group(1) for m in EXPR.finditer(value))
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(expressions(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(expressions(v))
    return found


class _Report:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, message: str, step_id: str | None = None):
        self.errors.append({"step_id": step_id, "message": message} if step_id else {"message": message})

    def warn(self, message: str, step_id: str | None = None):
        entry = {"step_id": step_id, "message": message} if step_id else {"message": message}
        if entry not in self.warnings:
            self.warnings.append(entry)

    def as_dict(self) -> dict:
        return {"errors": self.errors, "warnings": self.warnings}


def _check_trigger(trigger: Any, r: _Report) -> str:
    if not isinstance(trigger, dict) or not trigger:
        r.error("Déclencheur manquant")
        return ""
    kind = trigger.get("type")
    if kind == "schedule":
        for message in schedule_errors(trigger):
            r.error(f"Planification : {message}")
    elif kind == "mail":
        if not any(trigger.get(k) for k in ("from_contains", "subject_contains", "has_attachment")):
            r.warn("Déclencheur mail sans filtre : chaque nouveau mail lancerait l'automatisation")
    elif kind != "manual":
        r.error(f"Type de déclencheur inconnu « {kind} » (manual, schedule ou mail)")
    return kind or ""


def _check_inputs(inputs: Any, r: _Report) -> dict:
    if inputs in (None, {}):
        return {}
    if not isinstance(inputs, dict):
        r.error("Les entrées doivent être un objet clé → {label, type}")
        return {}
    datasets = 0
    for key, spec in inputs.items():
        if not _INPUT_KEY.match(str(key)):
            r.error(f"Clé d'entrée invalide « {key} » (lettres, chiffres et _ uniquement)")
        if not isinstance(spec, dict) or spec.get("type") not in ("file", "text", "dataset"):
            r.error(f"Entrée « {key} » : type attendu « file », « text » ou « dataset »")
            continue
        value = spec.get("value")
        if value in _FALSY_EMPTY:
            r.warn(f"Entrée « {spec.get('label') or key} » sans valeur")
        elif spec["type"] == "file" and not (isinstance(value, dict) and value.get("file_id")):
            r.error(f"Entrée fichier « {spec.get('label') or key} » : valeur {{file_id, name}} attendue")
        elif spec["type"] == "dataset":
            # A dataset value is a list of rows (each row a {colonne: valeur} object). The whole
            # automation is replayed once per row, with the row exposed as {{row.<colonne>}}.
            datasets += 1
            if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
                r.error(f"Jeu de données « {spec.get('label') or key} » : liste de lignes {{colonne: valeur}} attendue")
            elif len(value) > catalog.MAX_FOREACH:
                r.error(f"Jeu de données « {spec.get('label') or key} » : {len(value)} lignes, maximum {catalog.MAX_FOREACH}")
    if datasets > 1:
        r.error("Un seul jeu de données par automatisation (row.* désigne la ligne en cours)")
    return inputs


def _check_reference(expr: str, *, step: dict, idx: int, positions: dict[str, int], steps: list[dict],
                     inputs: dict, trigger_type: str, in_foreach_expr: bool, has_dataset: bool, r: _Report) -> None:
    sid = step.get("id")
    segments = [s.strip() for s in expr.split(".")]
    root = segments[0]
    looping = bool(step.get("foreach")) and not in_foreach_expr
    if root not in _ROOTS:
        r.error(f"Expression inconnue « {{{{{expr}}}}} » (début attendu : {', '.join(sorted(_ROOTS))})", sid)
        return
    if "$index" in segments and not looping:
        r.error(f"« $index » n'est utilisable que dans une étape foreach : {{{{{expr}}}}}", sid)
    if root in ("row", "row_index", "row_total"):
        if not has_dataset:
            r.warn(f"{{{{{expr}}}}} n'a de valeur que si un jeu de données (entrée « dataset ») est fourni", sid)
        return
    if root in ("item", "index"):
        if in_foreach_expr:
            r.error("Boucle imbriquée non prise en charge : le foreach ne peut pas dépendre de item/index", sid)
        elif not looping:
            r.error(f"« {root} » n'est disponible que dans une étape foreach : {{{{{expr}}}}}", sid)
        return
    if root == "inputs":
        if len(segments) < 2 or segments[1] not in inputs:
            r.error(f"Entrée inconnue : {{{{{expr}}}}}", sid)
        return
    if root == "trigger":
        if trigger_type != "mail":
            r.warn(f"{{{{{expr}}}}} n'a de valeur qu'avec un déclencheur mail", sid)
        return
    if root == "run" and segments[1:] != ["id"]:
        r.error(f"Seul run.id est disponible : {{{{{expr}}}}}", sid)
        return
    if root != "steps":
        return
    if len(segments) < 2:
        r.error("Référence d'étape incomplète : {{steps}}", sid)
        return
    ref = segments[1]
    if ref not in positions:
        r.error(f"Étape inconnue « {ref} » dans {{{{{expr}}}}}", sid)
        return
    if positions[ref] >= idx:
        r.error(f"L'étape « {ref} » n'est pas encore exécutée à ce stade ({{{{{expr}}}}})", sid)
        return
    target = steps[positions[ref]]
    rest = segments[2:]
    if target.get("foreach"):
        if rest and (rest[0] == "$index" or rest[0].isdigit()):
            rest = rest[1:]
        elif rest:
            r.warn(f"La sortie de « {ref} » est une liste (foreach) : utilisez steps.{ref}.$index.… dans {{{{{expr}}}}}", sid)
            return
    if rest:
        known = catalog.OUTPUT_FIELDS.get(target.get("type"), set())
        if known and rest[0] not in known:
            r.warn(f"Champ « {rest[0]} » absent de la sortie de {target.get('type')} ({', '.join(sorted(known))}) dans {{{{{expr}}}}}", sid)


def validate(trigger: Any, steps: Any, inputs: Any) -> dict:
    r = _Report()
    trigger_type = _check_trigger(trigger, r)
    inputs = _check_inputs(inputs, r)
    has_dataset = isinstance(inputs, dict) and any(isinstance(s, dict) and s.get("type") == "dataset" for s in inputs.values())
    if not isinstance(steps, list):
        r.error("Les étapes doivent être une liste")
        return r.as_dict()
    if not steps:
        r.error("Aucune étape")
    if len(steps) > catalog.MAX_STEPS:
        r.error(f"Trop d'étapes ({len(steps)}, maximum {catalog.MAX_STEPS})")

    positions: dict[str, int] = {}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        sid = step.get("id")
        if not isinstance(sid, str) or not _STEP_ID.match(sid):
            r.error(f"Étape n°{i + 1} : identifiant invalide « {sid} »")
        elif sid in positions:
            r.error(f"Identifiant d'étape en double « {sid} »", sid)
        else:
            positions[sid] = i

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            r.error(f"Étape n°{i + 1} : objet attendu")
            continue
        sid = step.get("id") if isinstance(step.get("id"), str) else None
        stype = step.get("type")
        entry = catalog.BY_TYPE.get(stype)
        if entry is None:
            r.error(f"Type d'étape inconnu « {stype} »", sid)
            continue
        params = step.get("params") if step.get("params") is not None else {}
        if not isinstance(params, dict):
            r.error("Les paramètres doivent être un objet", sid)
            continue
        known = {p["name"] for p in entry["params"]}
        for p in entry["params"]:
            if p["required"] and params.get(p["name"]) in _FALSY_EMPTY:
                r.error(f"Paramètre obligatoire manquant « {p['name']} » ({entry['title']})", sid)
        for name in params:
            if name not in known:
                r.warn(f"Paramètre inconnu « {name} » ignoré pour {stype}", sid)
        if step.get("on_error", "stop") not in ("stop", "continue"):
            r.error("on_error doit valoir « stop » ou « continue »", sid)

        # type-specific rules
        if stype == "local.print" and params.get("file_id") in _FALSY_EMPTY and params.get("path") in _FALSY_EMPTY:
            r.error("Impression : indiquez file_id ou path", sid)
        if stype == "ai.generate" and params.get("output", "text") not in ("text", "json"):
            r.error("ai.generate : output doit valoir « text » ou « json »", sid)
        if stype == "local.powershell":
            if expressions(params.get("script")):
                r.error("Le script PowerShell ne doit pas contenir d'expression {{…}} : le script approuvé doit être exactement celui exécuté", sid)
            r.warn("Script PowerShell : exécuté uniquement après votre approbation explicite du script exact", sid)
        if stype in ("desktop.click",) and params.get("name") in _FALSY_EMPTY and params.get("automation_id") in _FALSY_EMPTY:
            r.error("Clic : indiquez le libellé (name) ou l'identifiant (automation_id) de l'élément", sid)
        if stype.startswith(("desktop.", "vision.")) and entry["side_effect"]:
            r.warn("Cette étape prend le contrôle de la souris/du clavier : évitez d'utiliser le PC pendant l'exécution", sid)
        if stype == "flow.wait" and not expressions(params.get("seconds")):
            try:
                if not 0 < float(params.get("seconds")) <= 600:
                    r.error("Attente : entre 1 et 600 secondes", sid)
            except (TypeError, ValueError):
                r.error("Attente : durée invalide", sid)
        if stype == "web.browse":
            actions = params.get("actions")
            # Only a list given wholesale as one {{…}} expression is resolved at run time; a literal list
            # (even with expressions inside its values) is checked action by action.
            if actions not in _FALSY_EMPTY and not (isinstance(actions, str) and expressions(actions)):
                if not isinstance(actions, list):
                    r.error("web.browse : actions doit être une liste", sid)
                else:
                    for n, a in enumerate(actions, 1):
                        if not isinstance(a, dict) or a.get("do") not in catalog.WEB_ACTIONS:
                            r.error(f"web.browse : action n°{n} invalide (do ∈ {', '.join(sorted(catalog.WEB_ACTIONS))})", sid)
                        elif a["do"] in ("click", "type", "select", "wait_for", "extract", "extract_table") and not a.get("selector"):
                            r.error(f"web.browse : action n°{n} ({a['do']}) sans selector", sid)
        if stype == "mail.send" and step.get("foreach"):
            r.warn(f"Envoi de mails en boucle : maximum {catalog.MAX_MAIL_SENDS_PER_RUN} envois par exécution", sid)

        foreach = step.get("foreach")
        if foreach not in (None, ""):
            if not isinstance(foreach, str) or not _ONLY_EXPR.match(foreach):
                r.error("foreach doit être une seule expression, ex. {{steps.s3.rows}}", sid)
            else:
                for expr in expressions(foreach):
                    _check_reference(expr, step=step, idx=i, positions=positions, steps=steps, inputs=inputs,
                                     trigger_type=trigger_type, in_foreach_expr=True, has_dataset=has_dataset, r=r)
        when = step.get("when")
        if when not in (None, "") and not isinstance(when, str):
            r.error("when doit être une expression texte", sid)
        for expr in expressions(params) + (expressions(when) if isinstance(when, str) else []):
            _check_reference(expr, step=step, idx=i, positions=positions, steps=steps, inputs=inputs,
                             trigger_type=trigger_type, in_foreach_expr=False, has_dataset=has_dataset, r=r)
    return r.as_dict()


def script_hashes(steps: list) -> dict[str, str]:
    """step_id -> sha256(script, utf-8) of every local.powershell step."""
    import hashlib

    out = {}
    for step in steps or []:
        if isinstance(step, dict) and step.get("type") == "local.powershell":
            script = (step.get("params") or {}).get("script") or ""
            out[step.get("id") or ""] = hashlib.sha256(str(script).encode("utf-8")).hexdigest()
    return out
