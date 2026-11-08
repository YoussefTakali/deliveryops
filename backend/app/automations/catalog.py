"""Step catalog — the canonical list of step types a workflow may use. Served verbatim by
`GET /api/automations/catalog`, used by validate.py and embedded in the compiler's prompt.

executor: "server" (POST /runs/{id}/steps), "local" (desktop app bridge), "control" (runner-internal).
side_effect: the step changes something outside DeliveryOps -> a dry run only previews it.
"""


def _p(name: str, type_: str, required: bool, description: str, **extra) -> dict:
    return {"name": name, "type": type_, "required": required, "description": description, **extra}


# Param types: string, text (multi-line), number, boolean, file_id, file_ids (list), emails
# (one address, "a;b" or a list), object (key -> value mapping), choice (see "options"), list (JSON array).
CATALOG: list[dict] = [
    {
        "type": "mail.search", "title": "Rechercher des mails", "executor": "server", "side_effect": False,
        "params": [
            _p("from_contains", "string", False, "L'expéditeur (nom ou adresse) contient ce texte"),
            _p("subject_contains", "string", False, "L'objet contient ce texte"),
            _p("body_contains", "string", False, "Le corps du mail contient ce texte"),
            _p("since_days", "number", False, "Ne chercher que les mails reçus depuis N jours", default=7),
            _p("has_attachment", "boolean", False, "Uniquement les mails avec pièce jointe"),
            _p("top", "number", False, "Nombre maximum de mails retournés (plus récents d'abord)", default=10),
        ],
        "output": "{messages:[{id,subject,from_name,from_email,received,body_preview,attachments:[{id,name,size,content_type}]}], latest: messages[0]|null, count}",
    },
    {
        "type": "mail.get_attachment", "title": "Récupérer une pièce jointe", "executor": "server", "side_effect": False,
        "params": [
            _p("message_id", "string", True, "Identifiant du mail (ex. {{steps.s1.latest.id}})"),
            _p("name_contains", "string", False, "Le nom du fichier contient ce texte (ex. .xlsx)"),
        ],
        "output": "{file:{file_id,name,size,content_type}}",
    },
    {
        "type": "file.extract_table", "title": "Lire un tableau (Excel, CSV, Word, PDF)", "executor": "server", "side_effect": False,
        "params": [
            _p("file_id", "file_id", True, "Fichier à lire (ex. {{steps.s2.file.file_id}})"),
            _p("sheet", "string", False, "Nom de la feuille Excel (par défaut la première non vide)"),
        ],
        "output": "{rows:[{<colonne>:<valeur>}], columns:[...], count}",
    },
    {
        "type": "doc.fill_template", "title": "Remplir un modèle de document", "executor": "server", "side_effect": False,
        "params": [
            _p("template_file_id", "file_id", True, "Modèle .docx/.xlsx/.txt/.html/.md avec des champs {{Nom}}, «Nom» ou [Nom]"),
            _p("data", "object", True, "Valeurs des champs (ex. {{item}} dans une boucle)"),
            _p("output_name", "string", False, "Nom du fichier produit (ex. Convention - {{item.Nom}}.docx)"),
        ],
        "output": "{file:{file_id,name,size,content_type}, missing:[champs du modèle sans valeur]}",
    },
    {
        "type": "mail.send", "title": "Envoyer un mail", "executor": "server", "side_effect": True,
        "params": [
            _p("to", "emails", True, "Destinataire(s)"),
            _p("cc", "emails", False, "Copie"),
            _p("subject", "string", True, "Objet"),
            _p("body", "text", True, "Corps du mail"),
            _p("body_is_html", "boolean", False, "Le corps est en HTML", default=False),
            _p("attachments", "file_ids", False, "Pièces jointes (identifiants de fichiers)"),
        ],
        "output": "{sent:true,to:[...]} — test à blanc : {sent:false, preview:{to,subject,body,attachments:[noms]}}",
    },
    {
        "type": "ai.generate", "title": "Générer avec l'IA", "executor": "server", "side_effect": False,
        "params": [
            _p("instruction", "text", True, "Ce que l'IA doit produire (rédiger, résumer, classer, extraire...)"),
            _p("input", "text", False, "Données à traiter"),
            _p("output", "choice", False, "Format de sortie", options=["text", "json"], default="text"),
        ],
        "output": "{text} (output=text) ou {data} (output=json)",
    },
    {
        "type": "file.create", "title": "Créer un fichier texte", "executor": "server", "side_effect": False,
        "params": [
            _p("name", "string", True, "Nom du fichier (ex. rapport.csv)"),
            _p("content", "text", True, "Contenu"),
            _p("content_type", "string", False, "Type MIME (déduit de l'extension par défaut)"),
        ],
        "output": "{file:{file_id,name,size,content_type}}",
    },
    {
        "type": "local.print", "title": "Imprimer", "executor": "local", "side_effect": True,
        "params": [
            _p("file_id", "file_id", False, "Fichier à imprimer (sinon path)"),
            _p("path", "string", False, "Chemin local du fichier (sinon file_id)"),
            _p("printer", "string", False, "Imprimante (par défaut celle de Windows)"),
            _p("copies", "number", False, "Nombre de copies", default=1),
        ],
        "output": "{printed:true}",
    },
    {
        "type": "local.save_file", "title": "Enregistrer sur le PC", "executor": "local", "side_effect": True,
        "params": [
            _p("file_id", "file_id", True, "Fichier à enregistrer"),
            _p("folder", "string", True, "Dossier de destination (dans les dossiers autorisés)"),
            _p("name", "string", False, "Nom du fichier (par défaut celui d'origine)"),
        ],
        "output": "{path}",
    },
    {
        "type": "local.read_file", "title": "Lire un fichier du PC", "executor": "local", "side_effect": False,
        "params": [_p("path", "string", True, "Chemin du fichier")],
        "output": "{file:{file_id,name,size,content_type}}",
    },
    {
        "type": "local.list_files", "title": "Lister les fichiers d'un dossier", "executor": "local", "side_effect": False,
        "params": [
            _p("folder", "string", True, "Dossier"),
            _p("pattern", "string", False, "Filtre (ex. *.pdf)"),
            _p("modified_since_days", "number", False, "Modifiés depuis N jours"),
        ],
        "output": "{files:[{path,name,size,modified}], count}",
    },
    {
        "type": "local.open", "title": "Ouvrir un fichier ou un lien", "executor": "local", "side_effect": True,
        "params": [_p("target", "string", True, "Chemin ou URL http(s)")],
        "output": "{opened:true}",
    },
    {
        "type": "local.powershell", "title": "Script PowerShell (approbation requise)", "executor": "local", "side_effect": True,
        "params": [
            _p("script", "text", True, "Script exécuté tel quel, uniquement après approbation explicite (sans expression {{…}})"),
            _p("timeout_s", "number", False, "Délai maximum en secondes", default=120),
        ],
        "output": "{stdout, stderr, exit_code}",
    },
    # ---- desktop applications (Windows UI Automation — desktop app only) ----
    # Elements are found by what the user SEES (name/label, control type), never by screen position,
    # so the step survives window moves, resolution changes and layout tweaks.
    {
        "type": "desktop.open_app", "title": "Ouvrir une application", "executor": "local", "side_effect": True,
        "params": [
            _p("app", "string", True, "Nom de l'application comme dans le menu Démarrer (Outlook, Excel, Bloc-notes…), exécutable (notepad.exe) ou chemin complet"),
            _p("args", "string", False, "Arguments de ligne de commande (ex. un fichier à ouvrir)"),
            _p("wait_window", "string", False, "Attendre qu'une fenêtre dont le titre contient ce texte soit ouverte"),
            _p("timeout_s", "number", False, "Délai d'attente de la fenêtre en secondes", default=30),
        ],
        "output": "{opened:true, window}",
    },
    {
        "type": "desktop.window", "title": "Agir sur une fenêtre", "executor": "local", "side_effect": True,
        "params": [
            _p("title_contains", "string", True, "Le titre de la fenêtre contient ce texte"),
            _p("action", "choice", False, "focus = mettre au premier plan, wait = attendre son ouverture, wait_close = attendre sa fermeture",
               default="focus", options=["focus", "wait", "wait_close", "close", "maximize", "minimize"]),
            _p("timeout_s", "number", False, "Délai d'attente en secondes", default=30),
        ],
        "output": "{found, window}",
    },
    {
        "type": "desktop.click", "title": "Cliquer dans une application", "executor": "local", "side_effect": True,
        "params": [
            _p("window", "string", True, "Le titre de la fenêtre contient ce texte"),
            _p("name", "string", False, "Texte ou libellé visible de l'élément (bouton « Envoyer », menu « Fichier »…)"),
            _p("automation_id", "string", False, "Identifiant technique de l'élément (fourni par l'enregistrement)"),
            _p("control_type", "string", False, "Type d'élément : Button, MenuItem, Hyperlink, ListItem, TabItem, CheckBox, TreeItem, Edit…"),
            _p("index", "number", False, "Si plusieurs éléments correspondent : lequel (0 = le premier)", default=0),
            _p("double", "boolean", False, "Double-clic"),
            _p("timeout_s", "number", False, "Attendre que l'élément apparaisse (secondes)", default=15),
        ],
        "output": "{clicked:true, element}",
    },
    {
        "type": "desktop.type", "title": "Saisir du texte dans une application", "executor": "local", "side_effect": True,
        "params": [
            _p("window", "string", True, "Le titre de la fenêtre contient ce texte"),
            _p("text", "text", True, "Texte à saisir"),
            _p("name", "string", False, "Libellé du champ (vide = champ qui a le focus)"),
            _p("automation_id", "string", False, "Identifiant technique du champ (fourni par l'enregistrement)"),
            _p("clear", "boolean", False, "Effacer le contenu existant avant la saisie", default=True),
            _p("submit", "boolean", False, "Appuyer sur Entrée après la saisie"),
        ],
        "output": "{typed:true, element}",
    },
    {
        "type": "desktop.keys", "title": "Raccourci clavier", "executor": "local", "side_effect": True,
        "params": [
            _p("keys", "string", True, "Syntaxe SendKeys : ^c = Ctrl+C, ^v = Ctrl+V, %{F4} = Alt+F4, {ENTER}, {TAB}, +{TAB} = Maj+Tab, {DOWN}"),
            _p("window", "string", False, "Mettre d'abord au premier plan la fenêtre dont le titre contient ce texte"),
        ],
        "output": "{sent:true}",
    },
    {
        "type": "desktop.read", "title": "Lire le texte d'une application", "executor": "local", "side_effect": False,
        "params": [
            _p("window", "string", True, "Le titre de la fenêtre contient ce texte"),
            _p("name", "string", False, "Libellé de l'élément à lire (vide = tout le texte visible de la fenêtre)"),
            _p("automation_id", "string", False, "Identifiant technique de l'élément"),
            _p("control_type", "string", False, "Type d'élément (Edit, Text, DataItem, Document…)"),
        ],
        "output": "{text, lines:[…]}",
    },
    {
        "type": "desktop.read_notifications", "title": "Lire les notifications Windows", "executor": "local", "side_effect": False,
        "params": [
            _p("app_contains", "string", False, "Uniquement les notifications de cette application (ex. Teams, Outlook)"),
            _p("text_contains", "string", False, "Uniquement les notifications contenant ce texte"),
        ],
        "output": "{notifications:[{app,text}], count, latest}",
    },
    {
        "type": "desktop.screenshot", "title": "Capture d'écran", "executor": "local", "side_effect": False,
        "params": [_p("window", "string", False, "Capturer seulement la fenêtre dont le titre contient ce texte (vide = écran entier)")],
        "output": "{file:{file_id,name,size,content_type}}",
    },
    # ---- vision: act on what is VISIBLE on screen (OCR + image memory + AI decision) — works on any
    # application, including those with no accessibility names (Citrix, remote desktop, legacy tools) ----
    {
        "type": "vision.click", "title": "Cliquer sur ce qui est visible à l'écran (vision IA)", "executor": "local", "side_effect": True,
        "params": [
            _p("target", "string", True, "Ce qu'il faut cliquer, décrit comme à un collègue : « le bouton Valider », « l'icône d'impression », « la ligne de la facture {{item.Numero}} »"),
            _p("window", "string", False, "Limiter la recherche à la fenêtre dont le titre contient ce texte (recommandé)"),
            _p("double", "boolean", False, "Double-clic"),
            _p("right", "boolean", False, "Clic droit"),
            _p("timeout_s", "number", False, "Attendre que la cible apparaisse (secondes)", default=20),
            _p("ground_truth", "object", False, "Repère capturé à l'enregistrement (rejeu fidèle) : élément, empreinte visuelle, position. Ne pas modifier."),
        ],
        "output": "{clicked:true, x, y, method:'image-gt'|'image'|'uia-id'|'text'|'ai', label, confidence}",
    },
    {
        "type": "vision.type", "title": "Saisir du texte dans un champ visible à l'écran (vision IA)", "executor": "local", "side_effect": True,
        "params": [
            _p("target", "string", True, "Le champ, décrit par son libellé visible : « le champ Montant », « la zone de recherche »"),
            _p("text", "text", True, "Texte à saisir"),
            _p("window", "string", False, "Limiter la recherche à cette fenêtre"),
            _p("clear", "boolean", False, "Vider le champ avant de saisir", default=True),
            _p("submit", "boolean", False, "Appuyer sur Entrée après la saisie"),
            _p("timeout_s", "number", False, "Attendre que le champ apparaisse (secondes)", default=20),
            _p("ground_truth", "object", False, "Repère capturé à l'enregistrement (rejeu fidèle). Ne pas modifier."),
        ],
        "output": "{typed:true, x, y, method, label, confidence}",
    },
    {
        "type": "vision.wait", "title": "Attendre qu'un élément soit visible à l'écran (vision IA)", "executor": "local", "side_effect": False,
        "params": [
            _p("target", "string", True, "Ce qui doit apparaître : « le message Enregistrement réussi », « le bouton Télécharger »"),
            _p("window", "string", False, "Limiter la recherche à cette fenêtre"),
            _p("timeout_s", "number", False, "Durée maximale d'attente (secondes)", default=60),
        ],
        "output": "{visible:true|false, label, waited_s}",
    },
    {
        "type": "vision.read", "title": "Lire tout le texte visible à l'écran (OCR)", "executor": "local", "side_effect": False,
        "params": [_p("window", "string", False, "Lire seulement la fenêtre dont le titre contient ce texte (vide = écran entier)")],
        "output": "{text, lines:[...]}",
    },
    # ---- web (built-in browser with its own persistent session — desktop app only) ----
    {
        "type": "web.browse", "title": "Naviguer sur un site web", "executor": "local", "side_effect": True,
        "params": [
            _p("url", "string", True, "Adresse de départ (https://…)"),
            _p("actions", "list", False,
               "Actions dans l'ordre, chacune {do, …} : goto{url}, click{selector}, type{selector,text}, press{key}, "
               "select{selector,value}, wait{ms}, wait_for{selector}, scroll{selector?}, extract{selector,name,all?}, "
               "extract_table{selector,name}. Sélecteur : CSS, ou « text=Libellé visible », ou « label=Libellé du champ »"),
            _p("show", "boolean", False, "Afficher la fenêtre du navigateur pendant l'exécution"),
            _p("timeout_s", "number", False, "Délai maximum par action en secondes", default=30),
        ],
        "output": "{url, title, extracted:{<name>: texte | [textes] | [lignes]}, text}",
    },
    {
        "type": "ai.vision", "title": "Analyser une image avec l'IA", "executor": "server", "side_effect": False,
        "params": [
            _p("file_id", "file_id", True, "Image à analyser (ex. {{steps.s2.file.file_id}} d'une capture d'écran)"),
            _p("question", "text", True, "Ce qu'il faut lire ou vérifier dans l'image"),
        ],
        "output": "{answer}",
    },
    {
        "type": "flow.wait", "title": "Attendre", "executor": "control", "side_effect": False,
        "params": [_p("seconds", "number", True, "Durée d'attente en secondes (max 600)")],
        "output": "{waited}",
    },
    {
        "type": "flow.confirm", "title": "Demander confirmation", "executor": "control", "side_effect": False,
        "params": [_p("message", "string", True, "Question affichée dans la notification")],
        "output": "{confirmed:true}",
    },
]

WEB_ACTIONS = frozenset({"goto", "click", "type", "press", "select", "wait", "wait_for", "scroll", "extract", "extract_table"})

BY_TYPE: dict[str, dict] = {entry["type"]: entry for entry in CATALOG}

# Top-level fields of each step's output (reference checks in validate.py warn on anything else).
OUTPUT_FIELDS: dict[str, set[str]] = {
    "mail.search": {"messages", "latest", "count"},
    "mail.get_attachment": {"file"},
    "file.extract_table": {"rows", "columns", "count"},
    "doc.fill_template": {"file", "missing"},
    "mail.send": {"sent", "to", "preview"},
    "ai.generate": {"text", "data"},
    "file.create": {"file"},
    "local.print": {"printed", "preview"},
    "local.save_file": {"path", "preview"},
    "local.read_file": {"file"},
    "local.list_files": {"files", "count"},
    "local.open": {"opened", "preview"},
    "local.powershell": {"stdout", "stderr", "exit_code", "preview"},
    "desktop.open_app": {"opened", "window", "preview"},
    "desktop.window": {"found", "window", "preview"},
    "desktop.click": {"clicked", "element", "preview"},
    "desktop.type": {"typed", "element", "preview"},
    "desktop.keys": {"sent", "preview"},
    "desktop.read": {"text", "lines"},
    "desktop.read_notifications": {"notifications", "count", "latest"},
    "desktop.screenshot": {"file"},
    "vision.click": {"clicked", "x", "y", "method", "label", "confidence", "preview"},
    "vision.type": {"typed", "x", "y", "method", "label", "confidence", "preview"},
    "vision.wait": {"visible", "label", "waited_s"},
    "vision.read": {"text", "lines"},
    "web.browse": {"url", "title", "extracted", "text", "preview"},
    "ai.vision": {"answer"},
    "flow.wait": {"waited"},
    "flow.confirm": {"confirmed"},
}

SERVER_STEPS = frozenset(t for t, e in BY_TYPE.items() if e["executor"] == "server")

MAX_FOREACH = 200
MAX_MAIL_SENDS_PER_RUN = 50
MAX_STEPS = 50

TEMPLATE_SYNTAX = """\
Syntaxe des modèles (résolue par l'exécuteur AVANT chaque étape) :
- {{chemin}} avec des chemins pointés et des index numériques : steps.<id>.<champ>, inputs.<clé> (valeur de
  l'entrée ; pour une entrée fichier la valeur est {file_id,name}, donc {{inputs.modele.file_id}}),
  trigger.message.subject (déclencheur mail), item, item.<Nom de colonne>, index, now (ISO), today (AAAA-MM-JJ), run.id.
- Jeu de données (entrée de type « dataset ») : l'automatisation entière est rejouée une fois par ligne, la ligne
  courante étant exposée sous row.<Nom de colonne> (mêmes règles de correspondance que item), avec row_index
  (0…) et row_total. Idéal pour « refaire la même tâche pour chaque utilisateur/ligne avec ses données ».
- Le segment $index est remplacé par l'index de boucle courant : {{steps.s4.$index.file.file_id}} associe la i-ème
  sortie d'une étape foreach précédente à l'élément courant.
- item.<Nom de colonne> accepte espaces et accents (correspondance exacte puis insensible à la casse/aux accents).
- Si un paramètre est EXACTEMENT une expression {{…}}, la valeur brute (objet/liste/nombre) est substituée ;
  sinon les valeurs sont converties en texte et interpolées.
- foreach : "{{steps.s3.rows}}" — l'étape s'exécute une fois par élément (max 200) avec item/index ; sa sortie
  devient la LISTE des sorties de chaque itération. Pas de boucle imbriquée (un seul niveau).
- when : modèle évalué ; valeur fausse (vide, null, false, 0, [], "false") → étape ignorée (sortie null).
- Les sorties sont stockées sous steps.<id> pour les étapes suivantes.
"""

TRIGGER_SYNTAX = """\
Déclencheurs (trigger) :
- {"type":"manual"}
- {"type":"schedule","every":"day"|"week"|"month"|"hours","at":"HH:MM","weekdays":[0..6] (0=lundi, pour week),
   "day_of_month":1..28 (pour month),"interval_hours":N (pour hours),"timezone":"Europe/Paris"}
- {"type":"mail","from_contains":str?,"subject_contains":str?,"has_attachment":bool?} — lance l'automatisation à
  l'arrivée d'un mail correspondant ; le mail est alors disponible sous trigger.message (même forme qu'un élément
  de mail.search : id, subject, from_name, from_email, received, body_preview, attachments).
"""
