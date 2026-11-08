"""Description / recording -> workflow, with the LLM (reasoning tier, structured output).

The prompt carries the full step catalog, the template and trigger syntax, and a worked example
(the HR interns workflow). The output is normalised, then checked by validate.validate(); when it
has errors, ONE repair round feeds the errors back to the model. Whatever the model could not infer
goes into `questions` for the user to answer (answers are sent back on the next compile).
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app import llm
from app.automations import catalog
from app.automations.validate import validate
from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class CompiledStep(BaseModel):
    id: str
    type: str
    title: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    foreach: str | None = None
    when: str | None = None
    on_error: str = "stop"


class CompiledInput(BaseModel):
    key: str
    label: str = ""
    type: str = "file"


class CompiledWorkflow(BaseModel):
    name: str
    description: str = ""
    trigger: dict[str, Any] = Field(default_factory=lambda: {"type": "manual"})
    steps: list[CompiledStep] = Field(default_factory=list)
    inputs: list[CompiledInput] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    explanation: str = ""


class CompileUnavailable(RuntimeError):
    pass


SYSTEM = (
    "Tu es l'architecte d'automatisations de DeliveryOps. Un utilisateur non technicien décrit (ou a enregistré à "
    "l'écran) une tâche répétitive de bureau ; tu la transformes en un workflow d'ÉTAPES SÉMANTIQUES exécutables, "
    "robustes aux changements de données (boucles sur des lignes, recherche de mails par critères), jamais en "
    "rejeu de clics. Tu n'utilises QUE les types d'étapes du catalogue fourni, avec leurs paramètres exacts. "
    "Tout le texte destiné à l'utilisateur (name, description, title, questions, warnings, explanation) est en français."
)

RULES = """\
Règles de construction :
1. Identifiants d'étapes s1, s2, s3… dans l'ordre d'exécution ; chaque étape a un "title" court en français décrivant
   l'intention métier (« Trouver le mail hebdomadaire des stagiaires »), pas le mécanisme.
2. Une étape ne peut référencer que les sorties des étapes PRÉCÉDENTES (steps.<id>.<champ> selon la sortie du catalogue).
3. Pour traiter chaque ligne d'un tableau : "foreach": "{{steps.<id>.rows}}" puis item.<Nom de colonne>. Pour associer
   la i-ème sortie d'une étape foreach précédente à l'élément courant : {{steps.<id>.$index.<champ>}}. Un seul niveau
   de boucle. Une étape foreach produit une LISTE.
4. Fichiers fournis par l'utilisateur une fois pour toutes (modèle Word/Excel, fichier de référence) → une entrée
   "inputs" de type "file" (clé en snake_case), utilisée via {{inputs.<clé>.file_id}}. Valeurs fixes à saisir par
   l'utilisateur → entrée de type "text", utilisée via {{inputs.<clé>}}.
5. N'invente jamais d'adresse e-mail, d'objet de mail, de nom d'expéditeur ou de nom de colonne précis : mets la valeur
   la plus plausible ET ajoute une question dans "questions" pour la faire confirmer. Les questions sont concrètes,
   une information par question, et ne portent que sur ce qui manque réellement.
6. Préfère les étapes spécialisées. N'utilise "local.powershell" qu'en dernier recours, avec un script statique (aucune
   expression {{…}}), non destructif, sans élévation, et signale-le dans "warnings".
7. Étapes à effet de bord (mail.send, local.print, local.save_file, local.open, local.powershell, desktop.open_app,
   desktop.window, desktop.click, desktop.type, desktop.keys, vision.click, vision.type, web.browse) : uniquement si
   l'utilisateur les demande.
   Utilise "when" pour ignorer une étape quand une donnée peut manquer.
7b. Interaction avec les applications et le web — ordre de préférence : (a) les étapes API (mail.*, file.*, doc.*) qui
   sont les plus fiables ; (b) web.browse pour un site web (actions goto/click/type/extract, sélecteurs « text=… » ou
   « label=… » plutôt que des CSS fragiles quand l'enregistrement ne donne pas de sélecteur) ; (c) desktop.* pour une
   application de bureau sans API : desktop.open_app, puis desktop.window (wait/focus), puis desktop.click / desktop.type
   en désignant l'élément par son libellé visible (name) et son type (control_type) — reprends tels quels le nom,
   l'automation_id et le control_type des éléments cliqués relevés dans l'enregistrement ; desktop.read pour lire le
   texte d'une fenêtre ; desktop.keys pour les raccourcis (^c, ^v, {ENTER}). Ajoute flow.wait (1-5 s) après
   l'ouverture d'une application ou une action qui déclenche un chargement. Pour lire les notifications Windows :
   desktop.read_notifications. Pour comprendre ce qui est affiché quand aucun texte n'est lisible autrement :
   desktop.screenshot puis ai.vision avec une question précise. N'utilise jamais de coordonnées écran.
   Chaque action de web.browse est un objet dont la clé est "do" (jamais "action") : {"do":"click","selector":"text=Connexion"}.
7b-bis. VISION — quand l'élément n'a pas de libellé fiable ou que l'application est « opaque » (Citrix / bureau à
   distance, application métier ancienne, interface dessinée, bouton-icône sans texte, ou aucun name relevé dans
   l'enregistrement), utilise vision.click / vision.type / vision.wait / vision.read au lieu de desktop.click / desktop.type :
   la cible y est décrite en langage naturel, comme à un collègue (« le bouton Valider en bas à droite », « l'icône
   d'impression de la barre d'outils », « le champ Montant TTC », « la ligne du client {{item.Client}} »), jamais par
   coordonnées. Renseigne toujours "window". Après une action qui change l'écran, préfère vision.wait sur le résultat
   attendu (« le message Enregistré ») à un flow.wait à durée fixe. Pour une application Windows standard dont
   l'enregistrement fournit name/control_type, garde desktop.* (plus rapide) : il bascule de lui-même sur la vision en
   cas d'échec.
7c. IDENTIFIANTS : ne demande JAMAIS de mot de passe, code ou identifiant de connexion, et n'en stocke jamais dans une
   entrée, un paramètre ou un script. Un site qui demande une connexion est ouvert dans le navigateur des automatisations,
   où l'utilisateur se connecte une fois (la session est conservée) : n'ajoute aucune action de saisie d'identifiants et
   mets un avertissement « Connectez-vous une fois au site via le bouton Ouvrir le navigateur des automatisations ».
   Même principe pour les applications de bureau : on suppose l'utilisateur déjà connecté.
7d. Éléments d'interface : mets directement dans name le libellé visible le plus plausible (« Journal des ventes »),
   ne crée pas d'entrée "inputs" pour des libellés d'interface, et ne pose jamais de question sur automation_id ou
   control_type (laisse-les vides : l'enregistrement de la tâche les fournit). Si l'interface d'une application est
   inconnue, ajoute l'avertissement « Pour plus de fiabilité, enregistrez la tâche une fois dans l'application ».
7e. Pose au maximum 5 questions, uniquement sur ce que l'utilisateur sait et qui change le résultat (destinataire,
   objet de mail, nom de colonne, heure…), jamais sur des détails techniques.
8. Déclencheur : "schedule" pour une tâche périodique (timezone "Europe/Paris" par défaut, heure plausible si non
   précisée + question), "mail" pour « dès qu'un mail … arrive », sinon "manual".
9. Si un workflow actuel est fourni, modifie-le selon la demande et les réponses en gardant les identifiants existants.
   Les réponses de l'utilisateur aux questions précédentes sont prioritaires : ne repose pas une question déjà répondue.
10. "explanation" : 2 à 4 phrases expliquant simplement ce que fera l'automatisation. "warnings" : risques ou limites
   (ex. impression et fichiers locaux nécessitent l'application de bureau).
11. Robustesse : les étapes d'observation qui ne bloquent pas la suite (desktop.read, desktop.read_notifications,
   desktop.screenshot, et une extraction web dont le résultat est facultatif) prennent "on_error": "continue". Les
   étapes d'interaction (desktop.open_app, desktop.window, desktop.click, desktop.type, web.browse) sont
   automatiquement réessayées en cas d'aléa (fenêtre qui charge encore, focus perdu) : garde "on_error": "stop" pour
   celles dont dépend la suite, et insère un flow.wait (1-3 s) après l'ouverture d'une application ou une action qui
   déclenche un chargement, plutôt que de multiplier les étapes.
"""

HR_EXAMPLE_REQUEST = (
    "Chaque lundi, je reçois un mail avec un fichier Excel listant les nouveaux stagiaires (nom, prénom, e-mail…). "
    "Pour chaque stagiaire, je remplis le même modèle Word de convention avec ses informations, je l'imprime, puis "
    "je l'envoie par mail au stagiaire."
)

HR_EXAMPLE_OUTPUT = {
    "name": "Conventions des stagiaires du lundi",
    "description": "Chaque lundi, génère, imprime et envoie la convention de chaque nouveau stagiaire listé dans le fichier Excel reçu par mail.",
    "trigger": {"type": "schedule", "every": "week", "weekdays": [0], "at": "10:00", "timezone": "Europe/Paris"},
    "steps": [
        {"id": "s1", "type": "mail.search", "title": "Trouver le mail hebdomadaire des stagiaires",
         "params": {"subject_contains": "stagiaires", "has_attachment": True, "since_days": 7, "top": 1}, "on_error": "stop"},
        {"id": "s2", "type": "mail.get_attachment", "title": "Récupérer le fichier Excel des stagiaires",
         "params": {"message_id": "{{steps.s1.latest.id}}", "name_contains": ".xlsx"}, "on_error": "stop"},
        {"id": "s3", "type": "file.extract_table", "title": "Lire la liste des stagiaires",
         "params": {"file_id": "{{steps.s2.file.file_id}}"}, "on_error": "stop"},
        {"id": "s4", "type": "doc.fill_template", "title": "Remplir la convention de chaque stagiaire",
         "foreach": "{{steps.s3.rows}}",
         "params": {"template_file_id": "{{inputs.modele_convention.file_id}}", "data": "{{item}}",
                    "output_name": "Convention - {{item.Nom}} {{item.Prénom}}.docx"}, "on_error": "stop"},
        {"id": "s5", "type": "local.print", "title": "Imprimer chaque convention", "foreach": "{{steps.s3.rows}}",
         "params": {"file_id": "{{steps.s4.$index.file.file_id}}", "copies": 1}, "on_error": "continue"},
        {"id": "s6", "type": "mail.send", "title": "Envoyer sa convention à chaque stagiaire", "foreach": "{{steps.s3.rows}}",
         "params": {"to": "{{item.Email}}", "subject": "Votre convention de stage",
                    "body": "Bonjour {{item.Prénom}},\n\nVous trouverez ci-joint votre convention de stage.\n\nBien cordialement,",
                    "attachments": ["{{steps.s4.$index.file.file_id}}"]}, "on_error": "continue"},
    ],
    "inputs": [{"key": "modele_convention", "label": "Modèle Word de la convention", "type": "file"}],
    "questions": [
        "Quel objet (ou quel expéditeur) permet de reconnaître le mail hebdomadaire des stagiaires ?",
        "Quels sont les intitulés exacts des colonnes Nom, Prénom et E-mail dans le fichier Excel ?",
        "À quelle heure le lundi le traitement doit-il être lancé (le mail doit être arrivé) ?",
    ],
    "warnings": ["L'impression nécessite l'application de bureau DeliveryOps ouverte sur ce PC."],
    "explanation": (
        "Chaque lundi à 10 h, DeliveryOps cherche le mail des stagiaires reçu dans la semaine, lit le fichier Excel joint, "
        "puis pour chaque stagiaire remplit votre modèle de convention, l'imprime et l'envoie à son adresse e-mail."
    ),
}


def _catalog_for_prompt() -> str:
    lines = []
    for e in catalog.CATALOG:
        params = ", ".join(f"{p['name']}{'' if p['required'] else '?'}:{p['type']}" for p in e["params"])
        lines.append(f"- {e['type']} [{e['executor']}{', effet de bord' if e['side_effect'] else ''}] {e['title']} — params {{{params}}} → {e['output']}")
    return "\n".join(lines)


def _context(description: str, recording_analysis: dict | None, answers: dict | None, current: dict | None) -> str:
    parts = [
        "<catalogue>\n" + _catalog_for_prompt() + "\n</catalogue>",
        "<syntaxe>\n" + catalog.TEMPLATE_SYNTAX + "\n" + catalog.TRIGGER_SYNTAX + "\n</syntaxe>",
        "<regles>\n" + RULES + "\n</regles>",
        "<exemple>\nDemande : " + HR_EXAMPLE_REQUEST + "\nRésultat attendu :\n"
        + json.dumps(HR_EXAMPLE_OUTPUT, ensure_ascii=False, indent=1) + "\n</exemple>",
    ]
    if current:
        parts.append("<workflow_actuel>\n" + json.dumps(current, ensure_ascii=False, indent=1)[:12000] + "\n</workflow_actuel>")
    if recording_analysis:
        parts.append("<enregistrement_ecran>\nAnalyse de l'enregistrement de l'utilisateur effectuant la tâche :\n"
                     + json.dumps(recording_analysis, ensure_ascii=False, indent=1)[:12000] + "\n</enregistrement_ecran>")
    if description:
        parts.append("<demande>\n" + description.strip()[:6000] + "\n</demande>")
    if answers:
        qa = "\n".join(f"- Q : {q}\n  R : {a}" for q, a in answers.items() if str(a).strip())
        if qa:
            parts.append("<reponses_utilisateur>\n" + qa + "\n</reponses_utilisateur>")
    return "\n\n".join(parts)


TASK = (
    "Produis le workflow JSON (name, description, trigger, steps, inputs, questions, warnings, explanation) qui "
    "automatise la tâche décrite dans <demande> et/ou observée dans <enregistrement_ecran>, en suivant le catalogue, "
    "la syntaxe et les règles, sur le modèle de <exemple>. \"inputs\" est une liste de {key, label, type}."
)


def normalize(result: CompiledWorkflow) -> dict:
    """Model output -> contract shape, with safe defaults."""
    steps = []
    for i, s in enumerate(result.steps):
        step: dict[str, Any] = {"id": (s.id or f"s{i + 1}").strip(), "type": s.type.strip(),
                                "title": s.title.strip() or catalog.BY_TYPE.get(s.type, {}).get("title", s.type),
                                "params": s.params or {}}
        if s.foreach and s.foreach.strip():
            step["foreach"] = s.foreach.strip()
        if s.when and s.when.strip():
            step["when"] = s.when.strip()
        step["on_error"] = s.on_error if s.on_error in ("stop", "continue") else "stop"
        steps.append(step)
    trigger = dict(result.trigger or {}) or {"type": "manual"}
    if trigger.get("type") == "schedule":
        trigger.setdefault("timezone", "Europe/Paris")
    inputs = {i.key.strip(): {"label": i.label.strip() or i.key.strip(), "type": i.type if i.type in ("file", "text") else "text"}
              for i in result.inputs if i.key and i.key.strip()}
    return {
        "name": result.name.strip()[:200] or "Nouvelle automatisation",
        "description": result.description.strip(),
        "trigger": trigger, "steps": steps, "inputs": inputs,
        "questions": [q.strip() for q in result.questions if q and q.strip()],
        "warnings": [w.strip() for w in result.warnings if w and w.strip()],
        "explanation": result.explanation.strip(),
    }


def _blocking(workflow: dict) -> list[dict]:
    return validate(workflow["trigger"], workflow["steps"], workflow["inputs"])["errors"]


def _fmt_errors(errors: list[dict]) -> str:
    return "\n".join(f"- {('[' + e['step_id'] + '] ') if e.get('step_id') else ''}{e['message']}" for e in errors)


def _call(context: str, task: str) -> CompiledWorkflow:
    result, used = llm.structured(
        schema=CompiledWorkflow, tier="reasoning", system=SYSTEM, context=context, task=task, max_tokens=16384,
        thinking=None if settings.automation_compile_thinking else False,
        fallback=lambda: CompiledWorkflow(name=""),
    )
    if not used:
        raise CompileUnavailable("L'IA est indisponible pour le moment, réessayez dans quelques instants.")
    return result


def compile_workflow(description: str = "", recording_analysis: dict | None = None,
                     answers: dict | None = None, current: dict | None = None) -> dict:
    if not (description or "").strip() and not recording_analysis and not current:
        raise ValueError("Décrivez la tâche à automatiser ou fournissez un enregistrement")
    context = _context(description, recording_analysis, answers, current)
    workflow = normalize(_call(context, TASK))
    errors = _blocking(workflow)
    if errors:
        log.info("compile: %d validation error(s), repair round", len(errors))
        repair_context = context + "\n\n<tentative_precedente>\n" + json.dumps(
            {**workflow, "inputs": [{"key": k, **v} for k, v in workflow["inputs"].items()]}, ensure_ascii=False, indent=1,
        ) + "\n</tentative_precedente>\n\n<erreurs_de_validation>\n" + _fmt_errors(errors) + "\n</erreurs_de_validation>"
        repair_task = ("Le workflow de <tentative_precedente> ne passe pas la validation (voir <erreurs_de_validation>). "
                       "Renvoie le workflow complet corrigé (même format), en conservant tout ce qui était correct.")
        try:
            repaired = normalize(_call(repair_context, repair_task))
        except CompileUnavailable:
            repaired = None
        if repaired is not None:
            repaired_errors = _blocking(repaired)
            if len(repaired_errors) <= len(errors):
                workflow, errors = repaired, repaired_errors
    if errors:
        workflow["warnings"].append("À corriger avant activation :\n" + _fmt_errors(errors))
    return workflow
