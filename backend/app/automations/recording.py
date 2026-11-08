"""Screen-recording analysis: what did the user do? (evidence for the compiler, never replayed).

Keyframes are described by the vision model in batches of ~6, each with the events, window titles
and narration around them; a text model then merges everything into {summary, observed_steps}.
Vision is best-effort: if it fails (model gone, overloaded, rejects multi-image input...), the
analysis degrades to events + window titles + narration, and to a deterministic summary if the
LLM is unavailable altogether. It never blocks the compile.
"""

import base64
import json
import logging
from typing import Any

import openai
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import llm
from app.automations import catalog
from app.config import get_settings
from app.models import AutomationFile, AutomationRecording

log = logging.getLogger(__name__)
settings = get_settings()

BATCH_SIZE = 6
MAX_EVENTS_IN_PROMPT = 400


class ObservedStep(BaseModel):
    t: float = 0
    app: str = ""
    action: str = ""
    detail: str = ""


class RecordingAnalysis(BaseModel):
    summary: str
    observed_steps: list[ObservedStep] = Field(default_factory=list)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _event_line(e: dict) -> str:
    rest = {k: v for k, v in e.items() if k not in ("t", "type") and v not in (None, "", [], {})}
    return f"{_num(e.get('t')):.1f}s {e.get('type', 'event')} {json.dumps(rest, ensure_ascii=False)[:400] if rest else ''}".rstrip()


def _window(items: list[dict], start: float, end: float) -> list[dict]:
    return [i for i in items if isinstance(i, dict) and start <= _num(i.get("t")) <= end]


def _timeline(rec: AutomationRecording, observations: list[dict]) -> str:
    parts = [f"Durée : {rec.duration_s:.0f} s"]
    frames = [f"{_num(k.get('t')):.1f}s fenêtre « {k.get('window_title') or '?'} » ({k.get('process') or '?'})" for k in rec.keyframes or []]
    if frames:
        parts.append("Fenêtres au premier plan (captures) :\n" + "\n".join(frames[:200]))
    events = [_event_line(e) for e in (rec.events or []) if isinstance(e, dict)][:MAX_EVENTS_IN_PROMPT]
    if events:
        parts.append("Événements (clics, raccourcis ; le texte saisi n'est jamais enregistré) :\n" + "\n".join(events))
    narration = [f"{_num(n.get('t')):.1f}s « {n.get('text', '').strip()} »" for n in (rec.narration or []) if isinstance(n, dict) and n.get("text")]
    if narration:
        parts.append("Commentaire oral de l'utilisateur :\n" + "\n".join(narration))
    if observations:
        parts.append("Descriptions des captures d'écran (modèle de vision) :\n"
                     + "\n".join(f"{_num(o.get('t')):.1f}s [{o.get('app', '')}] {o.get('action', '')} — {o.get('detail', '')}" for o in observations))
    return "\n\n".join(parts)


_VISION_PROMPT = (
    "Voici {n} captures d'écran successives d'un utilisateur qui réalise une tâche de bureau répétitive "
    "(instants : {times}). Contexte autour de ces captures :\n{context}\n\n"
    "Pour chaque capture, décris l'application utilisée et l'action métier en cours (ex. ouvre la pièce jointe Excel "
    "« stagiaires.xlsx » dans Outlook, remplit le champ Nom du modèle Word, imprime le document). Cite les noms de "
    "fichiers, colonnes, objets de mail ou boutons lisibles. N'invente rien d'illisible. "
    'Réponds uniquement en JSON : {{"observations":[{{"t":<secondes>,"app":"...","action":"...","detail":"..."}}]}}'
)


class _VisionFailed(Exception):
    pass


def _vision_call(images: list[tuple[float, bytes]], context: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": _VISION_PROMPT.format(
        n=len(images), times=", ".join(f"{t:.1f}s" for t, _ in images), context=context or "(aucun)")}]
    for _, data in images:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}})
    response = llm.chat("automation_recording_vision", model=settings.llm_model_vision,
                        messages=[{"role": "user", "content": content}], max_tokens=1024, temperature=0.1)
    body = llm.extract_json(response.choices[0].message.content or "")
    items = body.get("observations") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for i, o in enumerate(items):
        if isinstance(o, dict):
            t = _num(o.get("t")) if o.get("t") is not None else images[min(i, len(images) - 1)][0]
            out.append({"t": t, "app": str(o.get("app") or ""), "action": str(o.get("action") or ""), "detail": str(o.get("detail") or "")})
    return out


def _describe_batch(images: list[tuple[float, bytes]], context: str, state: dict) -> list[dict]:
    """One batch; falls back to one image per call when the model rejects multi-image requests."""
    if not state.get("single_image") and len(images) > 1:
        try:
            return _vision_call(images, context)
        except openai.BadRequestError as exc:
            log.info("vision model rejected a %d-image request (%s), switching to one image per call", len(images), exc)
            state["single_image"] = True
    out = []
    for image in images:
        out.extend(_vision_call([image], context))
    return out


def _vision_observations(db: Session, rec: AutomationRecording) -> tuple[list[dict], bool]:
    frames = []
    for k in rec.keyframes or []:
        if not isinstance(k, dict) or not k.get("file_id"):
            continue
        f = db.get(AutomationFile, k["file_id"])
        if f is not None and f.owner == rec.owner:
            frames.append((_num(k.get("t")), f.data))
    if not frames or not settings.use_llm:
        return [], False
    frames.sort(key=lambda x: x[0])
    observations: list[dict] = []
    state: dict = {}
    for start in range(0, len(frames), BATCH_SIZE):
        batch = frames[start:start + BATCH_SIZE]
        lo = batch[0][0] - 3
        hi = frames[start + BATCH_SIZE][0] if start + BATCH_SIZE < len(frames) else batch[-1][0] + 5
        around = [_event_line(e) for e in _window(rec.events or [], lo, hi)][:60]
        around += [f"{_num(k.get('t')):.1f}s fenêtre « {k.get('window_title') or '?'} » ({k.get('process') or '?'})" for k in _window(rec.keyframes or [], lo, hi)]
        around += [f"{_num(n.get('t')):.1f}s l'utilisateur dit : « {n.get('text', '')} »" for n in _window(rec.narration or [], lo, hi)]
        try:
            observations.extend(_describe_batch(batch, "\n".join(around), state))
        except (llm.API_ERRORS + (json.JSONDecodeError, ValueError)) as exc:
            log.warning("recording %s: vision analysis failed (%s), continuing text-only", rec.id, exc)
            return observations, bool(observations)
    return observations, bool(observations)


def _fallback(rec: AutomationRecording) -> RecordingAnalysis:
    steps: list[ObservedStep] = []
    last = None
    for k in sorted((k for k in rec.keyframes or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t"))):
        title = k.get("window_title") or ""
        if title and title != last:
            steps.append(ObservedStep(t=_num(k.get("t")), app=k.get("process") or "", action="Fenêtre active", detail=title))
            last = title
    said = " ".join(n.get("text", "").strip() for n in rec.narration or [] if isinstance(n, dict)).strip()
    apps = sorted({s.app for s in steps if s.app})
    summary = f"Enregistrement de {rec.duration_s:.0f} s" + (f" dans : {', '.join(apps)}" if apps else "") + "."
    if said:
        summary += f" Commentaire : « {said[:600]} »"
    return RecordingAnalysis(summary=summary, observed_steps=steps[:100])


import re
import unicodedata

_MODS = {"ctrl": "^", "control": "^", "alt": "%", "maj": "+", "shift": "+", "win": "", "meta": ""}
_SENDKEYS = {"entree": "{ENTER}", "enter": "{ENTER}", "echap": "{ESC}", "esc": "{ESC}", "escape": "{ESC}",
             "tab": "{TAB}", "suppr": "{DEL}", "del": "{DEL}", "delete": "{DEL}", "espace": " ", "space": " ",
             "retour arriere": "{BACKSPACE}", "backspace": "{BACKSPACE}", "inser": "{INSERT}"}


def _ascii(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c)).lower().strip()


def _sendkeys(token: str) -> str | None:
    """A recorded key/shortcut label ("Ctrl+Maj+S", "Entrée", "F2", "Maj+Tab") -> a SendKeys string,
    or None when it can't be reproduced this way (e.g. involves the Windows key)."""
    parts = [p for p in str(token or "").split("+") if p.strip()]
    if not parts:
        return None
    mods, key = "", ""
    for p in parts:
        n = _ascii(p)
        if n in _MODS:
            if n in ("win", "meta"):
                return None  # SendKeys can't press the Windows key
            mods += _MODS[n]
        else:
            key = p
    kn = _ascii(key)
    if kn in _SENDKEYS:
        code = _SENDKEYS[kn]
    elif re.fullmatch(r"f\d{1,2}", kn):
        code = "{" + kn.upper() + "}"
    elif len(key) == 1:
        code = key.lower()
    else:
        return None
    return (mods + code) if code else None


def _window_at(rec: AutomationRecording, t: float) -> str:
    """Foreground window title in effect at time t (nearest preceding keyframe), for steps that don't
    carry their own window_title."""
    title = ""
    for k in sorted((k for k in rec.keyframes or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t"))):
        if _num(k.get("t")) <= t + 0.1 and k.get("window_title"):
            title = k["window_title"]
    return title


def build_faithful(rec: AutomationRecording) -> dict:
    """Deterministic record-and-replay: turn the recording's real events into concrete steps, in order,
    preserving exactly what happened — no LLM re-invention. Each click keeps its captured ground truth
    (the clicked element's identity, an image patch of what it looked like, the window) so the strict
    executor can find the same target again by pixels, by accessibility identity, or by AI, and verify
    it before acting. Typed *content* is never recorded (privacy), so each typing run becomes a text
    input the user fills once. This is the reliable, identical replay; the LLM `analyze`+compile path
    stays available for turning a recording into a smarter, data-driven workflow."""
    events = sorted((e for e in rec.events or [] if isinstance(e, dict)), key=lambda e: _num(e.get("t")))
    steps: list[dict] = []
    inputs: dict[str, dict] = {}
    questions: list[str] = []
    warnings: list[str] = []
    last_window = None
    n = 0
    truncated = False

    def add(step: dict, window: str):
        nonlocal last_window
        if window and window != last_window and steps:
            steps.append({"id": f"s{len(steps) + 1}", "type": "flow.wait", "title": "Attendre le chargement",
                          "params": {"seconds": 1}, "on_error": "continue"})
            last_window = window
        elif window:
            last_window = window
        step["id"] = f"s{len(steps) + 1}"
        step.setdefault("on_error", "stop")
        steps.append(step)

    for e in events:
        if len(steps) >= catalog.MAX_STEPS - 1:
            truncated = True
            break
        kind = e.get("type")
        window = e.get("window_title") or _window_at(rec, _num(e.get("t")))
        if kind == "click" and e.get("button") != "middle":
            el = e.get("element") if isinstance(e.get("element"), dict) else {}
            label = str(el.get("name") or "").strip()
            ctype = str(el.get("control_type") or "").strip()
            target = (f"« {label} »" + (f" ({ctype})" if ctype else "")) if label else \
                     (f"l'élément {ctype}" if ctype else "l'élément à la position enregistrée")
            params: dict[str, Any] = {"target": target, "window": window}
            gt: dict[str, Any] = {}
            ident = {k: v for k, v in (("name", label), ("control_type", ctype), ("automation_id", el.get("automation_id"))) if v}
            if ident:
                gt["element"] = ident
            if e.get("patch"):
                gt["patch"] = e["patch"]  # image ground truth (SikuliX-style), if the recorder captured it
            if e.get("rel"):
                gt["rel"] = e["rel"]
            if e.get("x") is not None and e.get("y") is not None:
                gt["point"] = {"x": int(_num(e.get("x"))), "y": int(_num(e.get("y")))}
            if gt:
                params["ground_truth"] = gt
            if _num(e.get("clicks")) >= 2:
                params["double"] = True
            if e.get("button") == "right":
                params["right"] = True
            n += 1
            add({"type": "vision.click", "title": f"Cliquer {target}"[:120], "params": params}, window)
        elif kind == "typing":
            key = f"saisie_{sum(1 for s in steps if s['type'] == 'vision.type') + 1}"
            inputs[key] = {"label": f"Texte à saisir (étape {len(steps) + 1})", "type": "text"}
            chars = int(_num(e.get("chars")))
            questions.append(f"Quel texte faut-il saisir à l'étape « {len(steps) + 1} » "
                             f"(environ {chars} caractère(s) tapés ; le contenu saisi n'est jamais enregistré) ?")
            add({"type": "vision.type", "title": "Saisir le texte enregistré",
                 "params": {"target": "le champ actif", "window": window, "text": "{{inputs." + key + "}}", "clear": False}}, window)
        elif kind in ("shortcut", "key"):
            code = _sendkeys(e.get("keys") or e.get("key"))
            if code is None:
                warnings.append(f"Raccourci « {e.get('keys') or e.get('key')} » non reproductible automatiquement — à vérifier.")
                continue
            add({"type": "desktop.keys", "title": f"Touches {e.get('keys') or e.get('key')}"[:120],
                 "params": {"keys": code, "window": window}, "on_error": "continue"}, window)

    if truncated:
        warnings.append(f"Enregistrement long : seules les {catalog.MAX_STEPS - 1} premières actions ont été reprises.")
    if any(s["type"] == "vision.click" for s in steps):
        warnings.append("Rejeu fidèle : les cibles sont retrouvées à l'écran (image, accessibilité, IA) puis vérifiées. "
                        "Gardez l'application visible ; la première exécution peut être plus lente le temps d'apprendre chaque cible.")
    said = " ".join(str(nn.get("text", "")).strip() for nn in rec.narration or [] if isinstance(nn, dict)).strip()
    name = f"Tâche enregistrée ({rec.duration_s:.0f} s)"
    return {
        "name": name[:200],
        "description": (said[:300] or f"Rejeu fidèle de {len([s for s in steps if s['type'].startswith('vision.')])} action(s) enregistrée(s)."),
        "trigger": {"type": "manual"},
        "steps": steps,
        "inputs": inputs,
        "questions": questions[:5],
        "warnings": warnings,
        "explanation": ("Cette automatisation rejoue fidèlement les actions que vous avez enregistrées, dans le même ordre. "
                        "Chaque cible est reconnue à l'écran (empreinte visuelle, accessibilité Windows, puis IA en dernier recours) "
                        "et vérifiée avant le clic. Les textes que vous aviez saisis vous sont redemandés (jamais enregistrés)."),
    }


def analyze(db: Session, rec: AutomationRecording) -> dict:
    observations, vision_used = _vision_observations(db, rec)
    timeline = _timeline(rec, observations)
    result, _used = llm.structured(
        schema=RecordingAnalysis, tier="agent", thinking=False, max_tokens=4096,
        system="Tu analyses l'enregistrement d'écran d'un utilisateur pour comprendre la tâche répétitive qu'il veut automatiser.",
        context=timeline,
        task=("Résume en français l'objectif métier de la tâche (summary, 2 à 5 phrases : quelles données, d'où elles viennent, "
              "ce qui est produit, pour qui) puis liste les étapes observées dans l'ordre (observed_steps : t en secondes, "
              "app, action métier, detail avec les noms de fichiers/colonnes/objets visibles). Regroupe les actions répétées "
              "(ex. « répète pour chaque ligne du tableau »). N'invente rien qui ne soit pas dans les éléments fournis."),
        fallback=lambda: _fallback(rec),
    )
    return {**result.model_dump(), "vision_used": vision_used}
