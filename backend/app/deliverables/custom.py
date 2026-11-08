"""User-defined deliverables ("mes livrables").

COPIL, matrice de couverture and plan de charge are built-ins, but a company can't be expected to ship
every deliverable type. Any user can define their own:

1. **design** — they describe it in their own words ("à partir des comptes-rendus et du backlog,
   produire un rapport hebdo avec…") and optionally give examples of their house style. The AI turns
   that into a spec — output format, expected inputs, sections (title + guidance), style rules — and
   asks clarifying questions when something is ambiguous. Every question comes with options and ONE
   recommended option.
2. **prepare** — for a given run, the AI reads the provided inputs and writes the content section by
   section, imitating the examples and grounded in the sources. It asks questions (again with a
   recommended option) instead of inventing what is missing.
3. **render** — the structured content becomes a real .docx / .pptx / .xlsx / .md file.

Every step has a deterministic fallback, so the feature works offline too.
"""

import io
import re
import unicodedata
from datetime import date

from pydantic import BaseModel, Field

from app import llm

OUTPUTS = ("docx", "pptx", "xlsx", "md")
FORMAT_OPTIONS = {
    "Document Word (.docx)": "docx",
    "Présentation PowerPoint (.pptx)": "pptx",
    "Classeur Excel (.xlsx)": "xlsx",
    "Texte Markdown (.md)": "md",
}
Q_FORMAT = "Sous quel format ce livrable doit-il être produit ?"
Q_AUDIENCE = "À qui s'adresse ce livrable ?"
Q_LENGTH = "Quel niveau de détail attendez-vous ?"
Q_PERIOD = "Sur quelle période ou quel jalon porte ce livrable ?"
MAX_QUESTIONS = 3
MAX_ROUNDS = 5  # after this many answers, stop asking and produce with what we have


# ---------------------------------------------------------------- schemas (LLM I/O)

class Question(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list, description="2 to 4 short answers the user can click")
    recommended: str = Field("", description="the ONE option you recommend — must be one of `options`")


class SpecInput(BaseModel):
    key: str = Field(description="snake_case identifier")
    label: str
    kind: str = Field("files", description="file | files | text")
    required: bool = False
    hint: str = ""


class SpecSection(BaseModel):
    title: str
    guidance: str = Field("", description="what this section must contain, in one or two sentences")


class CustomSpec(BaseModel):
    title: str
    output: str = Field(description="docx | pptx | xlsx | md")
    inputs: list[SpecInput]
    sections: list[SpecSection]
    instructions: str = Field("", description="tone, length, audience, formatting rules")
    questions: list[Question] = Field(default_factory=list, description="only when something important is ambiguous")


class Table(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class Block(BaseModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    table: Table | None = None


class CustomContent(BaseModel):
    title: str
    sections: list[Block]
    gaps: list[str] = Field(default_factory=list, description="information that was missing from the sources")
    questions: list[Question] = Field(default_factory=list, description="ask instead of inventing when something critical is missing")


# ---------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def clean_questions(raw: list, answers: dict[str, str]) -> list[dict]:
    """≤3 unanswered questions, ≤4 options each, a valid `recommended` placed first."""
    answered = {_norm(q) for q in (answers or {})}
    out = []
    for q in raw or []:
        q = q if isinstance(q, dict) else q.model_dump()
        text = str(q.get("question") or "").strip()
        if not text or _norm(text) in answered:
            continue
        opts = [str(o).strip()[:80] for o in (q.get("options") or []) if str(o).strip()]
        opts = list(dict.fromkeys(opts))[:4]
        rec = str(q.get("recommended") or "").strip()
        if opts:
            rec = next((o for o in opts if _norm(o) == _norm(rec)), opts[0])
            opts = [rec] + [o for o in opts if o != rec]
        else:
            rec = ""
        out.append({"question": text, "options": opts, "recommended": rec})
        if len(out) >= MAX_QUESTIONS:
            break
    return out


def _detect_output(text: str) -> str | None:
    t = _norm(text)
    if re.search(r"\b(excel|xlsx|classeur|tableur|matrice|tableau de bord chiffre)\b", t):
        return "xlsx"
    if re.search(r"\b(powerpoint|pptx|presentation|slides?|diapos?|copil|comite)\b", t):
        return "pptx"
    if re.search(r"\b(markdown|\.md)\b", t):
        return "md"
    if re.search(r"\b(word|docx|document|rapport|note|compte.rendu)\b", t):
        return "docx"
    return None


def _sections_from_examples(examples: list[dict]) -> list[dict]:
    seen, out = set(), []
    for ex in examples or []:
        for line in str(ex.get("text") or "").splitlines():
            m = re.match(r"^\s*(?:#{1,3}\s+|\d+[.)]\s+)(.{3,80})$", line)
            if m:
                title = m.group(1).strip(" :#*")
                if _norm(title) not in seen:
                    seen.add(_norm(title))
                    out.append({"title": title, "guidance": ""})
    return out[:10]


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "livrable"


def _apply_design_answers(spec: dict, answers: dict[str, str]) -> dict:
    notes = []
    for q, a in (answers or {}).items():
        if not a:
            continue
        if _norm(q) == _norm(Q_FORMAT):
            spec["output"] = FORMAT_OPTIONS.get(a) or _detect_output(a) or spec.get("output") or "docx"
        else:
            notes.append(f"- {q} → {a}")
    if notes:
        block = "Précisions du concepteur :\n" + "\n".join(notes)
        instr = (spec.get("instructions") or "").split("Précisions du concepteur :")[0].rstrip()
        spec["instructions"] = (instr + "\n\n" + block).strip()
    return spec


def normalize_spec(spec: dict) -> dict:
    """Coerce a spec (from the LLM, the fallback or the client) into a safe, complete shape."""
    out = {
        "title": (str(spec.get("title") or "").strip() or "Mon livrable")[:200],
        "description": str(spec.get("description") or "").strip(),
        "output": spec.get("output") if spec.get("output") in OUTPUTS else "docx",
        "instructions": str(spec.get("instructions") or "").strip(),
        "examples": [{"name": str(e.get("name") or "exemple")[:120], "text": str(e.get("text") or "")[:8000]}
                     for e in (spec.get("examples") or []) if str(e.get("text") or "").strip()][:3],
    }
    inputs, keys = [], set()
    for i in spec.get("inputs") or []:
        i = i if isinstance(i, dict) else i.model_dump()
        key = re.sub(r"[^a-z0-9_]+", "_", _norm(str(i.get("key") or i.get("label") or "entree"))).strip("_")[:40] or "entree"
        while key in keys:
            key += "_2"
        keys.add(key)
        kind = i.get("kind") if i.get("kind") in ("file", "files", "text") else "files"
        inputs.append({"key": key, "label": str(i.get("label") or key)[:120], "kind": kind,
                       "required": bool(i.get("required")), "hint": str(i.get("hint") or "")[:200]})
    if not inputs:
        inputs = [{"key": "sources", "label": "Documents sources", "kind": "files", "required": True, "hint": "Les documents à partir desquels rédiger le livrable"}]
    out["inputs"] = inputs[:8]
    sections = []
    for s in spec.get("sections") or []:
        s = s if isinstance(s, dict) else s.model_dump()
        if str(s.get("title") or "").strip():
            sections.append({"title": str(s["title"]).strip()[:120], "guidance": str(s.get("guidance") or "").strip()[:400]})
    out["sections"] = sections[:15] or [{"title": "Contexte", "guidance": ""}, {"title": "Points clés", "guidance": ""},
                                        {"title": "Actions et prochaines étapes", "guidance": ""}]
    return out


# ---------------------------------------------------------------- 1. design

def _deterministic_spec(description: str, examples: list[dict], current: dict | None) -> dict:
    base = dict(current or {})
    first = re.split(r"[.\n:]", description.strip())[0][:80] if description.strip() else ""
    base.setdefault("title", first or "Mon livrable")
    base.setdefault("output", _detect_output(description) or "docx")
    base.setdefault("inputs", [
        {"key": "sources", "label": "Documents sources", "kind": "files", "required": True, "hint": "Comptes-rendus, specs, exports…"},
        {"key": "notes", "label": "Éléments complémentaires", "kind": "text", "required": False, "hint": "Ce que les documents ne disent pas"},
    ])
    base.setdefault("sections", _sections_from_examples(examples))
    base["description"] = description
    base["examples"] = examples
    return base


def _design_questions(description: str, answers: dict) -> list[dict]:
    qs = []
    detected = _detect_output(description)
    if not detected:
        qs.append({"question": Q_FORMAT, "options": list(FORMAT_OPTIONS), "recommended": "Document Word (.docx)"})
    t = _norm(description)
    if not re.search(r"\b(client|copil|direction|comite|equipe|interne|manager)\b", t):
        qs.append({"question": Q_AUDIENCE, "options": ["Client / COPIL", "Direction", "Équipe interne"], "recommended": "Client / COPIL"})
    if not re.search(r"\b(synthe|court|detail|page|resume|bref)\w*", t):
        qs.append({"question": Q_LENGTH, "options": ["Synthétique (1 à 2 pages)", "Standard", "Détaillé"], "recommended": "Synthétique (1 à 2 pages)"})
    return clean_questions(qs, answers)


def design(description: str, examples: list[dict], answers: dict[str, str] | None = None, current: dict | None = None) -> dict:
    answers = {k: v for k, v in (answers or {}).items() if v}
    base = _deterministic_spec(description, examples, current)
    det_questions = _design_questions(description, answers)

    ex_block = "\n\n".join(f'<exemple name="{e["name"]}">\n{e["text"][:4000]}\n</exemple>' for e in examples[:3])
    answered = "\n".join(f"- {q} → {a}" for q, a in answers.items()) or "(aucune)"
    context = (f"<description_utilisateur>\n{description}\n</description_utilisateur>\n\n"
               f"<reponses_deja_donnees>\n{answered}\n</reponses_deja_donnees>\n\n{ex_block}")
    task = (
        "Tu conçois le MODÈLE d'un livrable projet pour un cabinet de conseil (AMS/TMA). À partir de la description "
        "de l'utilisateur et de ses exemples de style, propose : un titre, le format de sortie (docx, pptx, xlsx ou md), "
        "les entrées attendues (fichiers ou texte, avec un libellé clair), les sections du livrable (titre + consigne "
        "de contenu, dans l'ordre des exemples s'il y en a) et les règles de style (ton, longueur, public, mise en forme). "
        f"Si un point important reste ambigu ET n'a pas déjà reçu de réponse, pose au plus {MAX_QUESTIONS} questions : "
        "chacune avec 2 à 4 options courtes et UNE option recommandée (`recommended`, la plus probable ou la meilleure "
        "pratique). Sinon, `questions` doit être vide. Ne repose jamais une question déjà répondue."
    )

    def fallback() -> CustomSpec:
        spec = normalize_spec(base)
        return CustomSpec(title=spec["title"], output=spec["output"], inputs=spec["inputs"], sections=spec["sections"],
                          instructions=spec["instructions"], questions=det_questions)

    result, used = llm.structured(schema=CustomSpec, context=context, task=task, fallback=fallback, tier="agent")
    spec = result.model_dump()
    spec.update({"description": description, "examples": examples})
    if not spec.get("sections") and base.get("sections"):
        spec["sections"] = base["sections"]
    spec = normalize_spec(_apply_design_answers(spec, answers))
    questions = [] if len(answers) >= MAX_ROUNDS else clean_questions(result.questions, answers)
    return {"status": "questions" if questions else "ready", "spec": spec, "questions": questions, "used_llm": used}


# ---------------------------------------------------------------- 2. prepare (write the content)

def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-*•]\s)", text) if len(p.strip()) > 30]


def _deterministic_content(spec: dict, texts: dict[str, str], notes: str) -> list[dict]:
    paras = [p for t in texts.values() for p in _paragraphs(t)] + _paragraphs(notes or "")
    used: set[int] = set()
    blocks = []
    for s in spec["sections"]:
        words = [w for w in re.findall(r"\w{4,}", _norm(s["title"] + " " + s.get("guidance", "")))]
        picks = [i for i, p in enumerate(paras) if i not in used and any(w in _norm(p) for w in words)][:3]
        if not picks:
            picks = [i for i in range(len(paras)) if i not in used][:2]
        used.update(picks)
        bullets = [re.sub(r"^[-*•]\s*", "", paras[i])[:400] for i in picks]
        blocks.append({"heading": s["title"], "paragraphs": [], "bullets": bullets or ["À compléter."], "table": None})
    return blocks


def prepare(spec: dict, texts: dict[str, str], notes: str = "", answers: dict[str, str] | None = None, projet: str = "") -> dict:
    spec = normalize_spec(spec)
    answers = {k: v for k, v in (answers or {}).items() if v}
    det_questions = [] if answers else clean_questions(
        [{"question": Q_PERIOD, "options": ["Semaine en cours", "Mois en cours", "Depuis le dernier livrable"], "recommended": "Semaine en cours"}], answers)

    sources = "\n\n".join(f'<source name="{n}">\n{t[:12000]}\n</source>' for n, t in texts.items() if t.strip())
    sections = "\n".join(f"- {s['title']} : {s.get('guidance') or '—'}" for s in spec["sections"])
    ex_block = "\n\n".join(f'<exemple_de_style name="{e["name"]}">\n{e["text"][:4000]}\n</exemple_de_style>' for e in spec["examples"])
    answered = "\n".join(f"- {q} → {a}" for q, a in answers.items()) or "(aucune)"
    context = (f"<livrable titre=\"{spec['title']}\" format=\"{spec['output']}\" projet=\"{projet}\">\n"
               f"Sections attendues :\n{sections}\n\nRègles :\n{spec['instructions'] or '—'}\n</livrable>\n\n"
               f"<precisions_utilisateur>\n{answered}\n</precisions_utilisateur>\n\n"
               f"<notes_utilisateur>\n{notes or '—'}\n</notes_utilisateur>\n\n{ex_block}\n\n{sources}")
    task = (
        "Rédige le contenu de ce livrable, section par section, dans l'ordre et avec les titres attendus. Imite le style, "
        "le ton et la structure des exemples de style (sans jamais en recopier les faits). Appuie chaque affirmation sur "
        "les sources ; mets les chiffres et listes dans `bullets` ou `table` quand c'est plus lisible. Ce que les sources ne "
        "disent pas va dans `gaps`, jamais inventé. Si une information CRITIQUE manque et n'a pas déjà été précisée, pose "
        f"au plus {MAX_QUESTIONS} questions (2 à 4 options + UNE `recommended`) ; sinon `questions` est vide."
    )

    def fallback() -> CustomContent:
        return CustomContent(title=spec["title"], sections=_deterministic_content(spec, texts, notes),
                             gaps=["Brouillon généré sans IA (mode hors ligne) : relisez et complétez chaque section."],
                             questions=det_questions)

    content, used = llm.structured(schema=CustomContent, context=context, task=task, fallback=fallback, tier="agent")
    questions = [] if len(answers) >= MAX_ROUNDS else clean_questions(content.questions, answers)
    if questions:
        return {"status": "questions", "questions": questions, "draft": None, "used_llm": used}
    draft = content.model_dump(exclude={"questions"})
    draft["title"] = draft.get("title") or spec["title"]
    if not draft.get("sections"):
        draft["sections"] = _deterministic_content(spec, texts, notes)
    return {"status": "ready", "questions": [], "draft": draft, "used_llm": used}


# ---------------------------------------------------------------- 3. render

def render(spec: dict, draft: dict) -> tuple[str, str, bytes]:
    spec = normalize_spec(spec)
    title = str(draft.get("title") or spec["title"])
    blocks = [b for b in (draft.get("sections") or []) if isinstance(b, dict)]
    gaps = [str(g) for g in draft.get("gaps") or [] if str(g).strip()]
    base = f"{_slug(title)}_{date.today().isoformat()}"
    out = spec["output"]
    if out == "pptx":
        return base + ".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", _pptx(title, blocks, gaps)
    if out == "xlsx":
        return base + ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _xlsx(title, blocks, gaps)
    if out == "md":
        return base + ".md", "text/markdown; charset=utf-8", _md(title, blocks, gaps).encode("utf-8")
    return base + ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _docx(title, blocks, gaps)


def _md(title: str, blocks: list[dict], gaps: list[str]) -> str:
    lines = [f"# {title}", ""]
    for b in blocks:
        lines += [f"## {b.get('heading', '')}", ""]
        lines += [p for p in b.get("paragraphs") or []] + ([""] if b.get("paragraphs") else [])
        lines += [f"- {x}" for x in b.get("bullets") or []]
        t = b.get("table") or {}
        if t.get("headers"):
            lines += ["", "| " + " | ".join(t["headers"]) + " |", "|" + "---|" * len(t["headers"])]
            lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in t.get("rows") or []]
        lines.append("")
    if gaps:
        lines += ["## Points à compléter", ""] + [f"- {g}" for g in gaps]
    return "\n".join(lines)


def _docx(title: str, blocks: list[dict], gaps: list[str]) -> bytes:
    import docx

    d = docx.Document()
    d.add_heading(title, level=0)
    for b in blocks:
        d.add_heading(str(b.get("heading") or ""), level=1)
        for p in b.get("paragraphs") or []:
            d.add_paragraph(str(p))
        for x in b.get("bullets") or []:
            d.add_paragraph(str(x), style="List Bullet")
        t = b.get("table") or {}
        if t.get("headers"):
            table = d.add_table(rows=1, cols=len(t["headers"]))
            table.style = "Table Grid"
            for i, h in enumerate(t["headers"]):
                table.rows[0].cells[i].text = str(h)
            for r in t.get("rows") or []:
                cells = table.add_row().cells
                for i, c in enumerate(r[: len(t["headers"])]):
                    cells[i].text = str(c)
    if gaps:
        d.add_heading("Points à compléter", level=1)
        for g in gaps:
            d.add_paragraph(g, style="List Bullet")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pptx(title: str, blocks: list[dict], gaps: list[str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = title
    s.placeholders[1].text = date.today().strftime("%d/%m/%Y")
    for b in blocks + ([{"heading": "Points à compléter", "bullets": gaps}] if gaps else []):
        t = b.get("table") or {}
        slide = prs.slides.add_slide(prs.slide_layouts[5 if t.get("headers") else 1])
        slide.shapes.title.text = str(b.get("heading") or "")
        if t.get("headers"):
            rows = (t.get("rows") or [])[:12]
            shape = slide.shapes.add_table(len(rows) + 1, len(t["headers"]), Inches(0.5), Inches(1.6), Inches(9), Inches(0.4) * (len(rows) + 1))
            for i, h in enumerate(t["headers"]):
                shape.table.cell(0, i).text = str(h)
            for r, row in enumerate(rows, start=1):
                for c, val in enumerate(row[: len(t["headers"])]):
                    shape.table.cell(r, c).text = str(val)
            continue
        body = slide.placeholders[1].text_frame
        items = [str(p) for p in b.get("paragraphs") or []] + [str(x) for x in b.get("bullets") or []]
        body.text = items[0] if items else ""
        for item in items[1:8]:
            para = body.add_paragraph()
            para.text = item
        for para in body.paragraphs:
            for run in para.runs:
                run.font.size = Pt(16)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _xlsx(title: str, blocks: list[dict], gaps: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 110
    head_fill = PatternFill("solid", fgColor="DDE7F7")
    used_names = {"Synthèse"}
    for b in blocks:
        ws.append([])
        ws.append([str(b.get("heading") or "")])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.cell(ws.max_row, 1).fill = head_fill
        for p in b.get("paragraphs") or []:
            ws.append([str(p)])
        for x in b.get("bullets") or []:
            ws.append([f"• {x}"])
        t = b.get("table") or {}
        if t.get("headers"):
            name = re.sub(r"[\[\]:*?/\\]", "", str(b.get("heading") or "Tableau"))[:28] or "Tableau"
            while name in used_names:
                name = name[:26] + "_2"
            used_names.add(name)
            sheet = wb.create_sheet(name)
            sheet.append([str(h) for h in t["headers"]])
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                cell.fill = head_fill
            for r in t.get("rows") or []:
                sheet.append([str(c) for c in r[: len(t["headers"])]])
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width = min(60, max(12, *(len(str(c.value or "")) + 2 for c in col)))
            ws.append([f"→ voir l'onglet « {name} »"])
    if gaps:
        ws.append([])
        ws.append(["Points à compléter"])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        for g in gaps:
            ws.append([f"• {g}"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
