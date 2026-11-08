"""The BRAIN of vision-driven automation steps (vision.click / vision.type / vision.wait).

The desktop app is the eyes: it perceives the screen locally (Windows OCR + accessibility tree) and
sends a *description* of what is visible — label, control type, position — plus tiny crops of
icon-only buttons. It never sends the screenshot. This module decides which perceived element
fulfils the step's intent.

Why text and not a vision model on the screenshot: measured on this deployment's hosted models, a
VLM asked to pick a numbered box on a marked-up screenshot was right 1 time in 5 (11B) or timed out
(90B); the text model choosing from the perceived description was right 6/6 in ~0.7 s. The small
VLM is still useful for one easy job — captioning a single cropped icon — so icon buttons get a
label too (cached by image hash, so each icon is paid for once).
"""

import base64
import hashlib
import io
import logging
import re
import threading
import unicodedata

from pydantic import BaseModel, Field

from app import llm
from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_icon_captions: dict[str, str] = {}
_icon_lock = threading.Lock()
MAX_ICON_BYTES = 60_000


class Decision(BaseModel):
    index: int = Field(ge=-1, description="numéro de l'élément à actionner, -1 si aucun ne convient")
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)).replace("&", "")).strip().casefold()


def caption_icon(png_b64: str) -> str:
    """One or two words for what an icon-only button does. Cached by content hash."""
    try:
        data = base64.b64decode(png_b64, validate=True)
    except Exception:  # noqa: BLE001
        return ""
    if not data or len(data) > MAX_ICON_BYTES:
        return ""
    key = hashlib.sha1(data).hexdigest()
    with _icon_lock:
        if key in _icon_captions:
            return _icon_captions[key]
    caption = ""
    if settings.use_llm:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(data)).convert("RGB")
            scale = max(1, 96 // max(1, max(img.size)))  # small icons caption far better when enlarged
            if scale > 1:
                img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            response = llm.chat("automation_icon_caption", model=settings.llm_model_vision, max_tokens=12, temperature=0.0, messages=[{"role": "user", "content": [
                {"type": "text", "text": "This is a toolbar icon from a desktop application. What action does it represent? Answer with 1-3 words."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}},
            ]}])
            caption = llm.clean_text(response.choices[0].message.content).strip(" .\"'")[:40]
        except Exception as exc:  # noqa: BLE001 — a missing caption only means the icon stays unlabeled
            log.info("icon caption failed: %s", type(exc).__name__)
    with _icon_lock:
        if len(_icon_captions) > 2000:
            _icon_captions.clear()
        _icon_captions[key] = caption
    return caption


def _fuzzy(goal: str, candidates: list[dict]) -> Decision:
    """No LLM available: best token overlap between the goal and a label, if it is unambiguous."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", _norm(goal))}
    scored = []
    for c in candidates:
        label = {w for w in re.findall(r"[a-z0-9]{3,}", _norm(c.get("label")))}
        if words and label:
            scored.append((len(words & label) / len(label | words), c["i"]))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.34 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return Decision(index=scored[0][1], confidence=round(min(0.8, scored[0][0] + 0.2), 2), reason="correspondance de libellé")
    return Decision(index=-1, confidence=0.0, reason="aucun libellé proche")


def decide(*, goal: str, action: str, window: str, candidates: list[dict], icons: list[dict], text: str = "") -> dict:
    """Pick the perceived element to act on. `candidates` = [{i,label,control_type,source,x,y,w,h}];
    `icons` = [{i, png_b64}] for label-less buttons."""
    captions = {int(ic["i"]): caption_icon(str(ic.get("png_b64") or "")) for ic in icons[:12] if "i" in ic}
    usable = []
    for c in candidates[:260]:
        label = str(c.get("label") or "").strip()
        if not label and captions.get(int(c["i"])):
            label = f"[icône : {captions[int(c['i'])]}]"
        if not label:
            continue
        usable.append({**c, "label": label})
    if not usable:
        return {"index": -1, "confidence": 0.0, "reason": "rien de lisible à l'écran", "captions": captions}

    if not settings.use_llm:
        d = _fuzzy(goal, usable)
        return {**d.model_dump(), "captions": captions}

    listing = "\n".join(
        f"[{c['i']}] {c.get('control_type') or 'Text'} « {c['label'][:90]} » @({int(c.get('x', 0)) + int(c.get('w', 0)) // 2},{int(c.get('y', 0)) + int(c.get('h', 0)) // 2})"
        + ("" if c.get("source") == "uia" else " (lu à l'écran)")
        for c in usable
    )
    verb = {"click": "cliquer", "type": f"saisir le texte « {text[:80]} » (choisis le CHAMP de saisie, pas son libellé si un champ Edit est listé à côté)", "wait": "vérifier la présence de"}.get(action, "cliquer")
    result, used = llm.structured(
        schema=Decision, tier="agent", thinking=False, max_tokens=200,
        system=("Tu pilotes une application Windows à la place d'un utilisateur. On te donne ce qui est visible à l'écran : pour "
                "chaque élément son numéro, son type, son libellé (lu par OCR ou fourni par l'application ; « [icône : …] » = "
                "bouton sans texte décrit par son pictogramme) et la position de son centre en pixels (x vers la droite, y vers le "
                "bas). Choisis l'élément à actionner pour atteindre l'objectif. Raisonne par le SENS (synonymes, autre langue, "
                "abréviations), le type attendu et la position (barre d'outils en haut, boutons de validation en bas à droite, "
                "libellé d'un champ à gauche ou au-dessus du champ). Si plusieurs éléments portent le même libellé, préfère un "
                "contrôle interactif (Button, MenuItem, Edit…) à un simple texte. Réponds index=-1 si rien ne correspond "
                "vraiment : ne devine jamais — un mauvais clic est pire qu'aucun clic."),
        context=f"Fenêtre : « {window or 'écran entier'} »\nÉléments visibles :\n{listing}",
        task=f"Objectif : {verb} → « {goal} ». Donne le numéro (index) de l'élément, ta confiance (0-1) et une raison en quelques mots.",
        fallback=lambda: _fuzzy(goal, usable),
    )
    valid = {c["i"] for c in usable}
    if result.index not in valid:
        result = Decision(index=-1, confidence=0.0, reason=result.reason or "aucun élément ne convient")
    return {**result.model_dump(), "captions": captions, "llm_used": used}
