"""Generate the real Office files for each deliverable from the user's answers.

Deterministic build straight from the answers (so it's predictable and testable); the LLM is only an
optional enrichment layer the caller may add later. COPIL → .pptx, matrice de couverture → .xlsx.
"""

import io
import re

from app.deliverables import templates as T

REF_LIKE = re.compile(r"[A-Z]{2,6}-\d{1,4}")


def _ans(template: dict, answers: dict, field: str) -> str:
    """The answer to the question mapped to `field` (answers are keyed by question text)."""
    idx = (template.get("map") or {}).get(field)
    if idx is None:
        return ""
    qs = template.get("questions") or []
    if idx >= len(qs):
        return ""
    return str(answers.get(qs[idx]["question"], "") or "").strip()


def _lines(text: str) -> list[str]:
    return [ln.strip(" -•\t") for ln in str(text or "").replace("\r", "\n").split("\n") if ln.strip(" -•\t")]


# ------------------------------------------------------------------ COPIL (.pptx)

def generate_copil(template: dict, answers: dict) -> tuple[str, str, bytes]:
    from pptx import Presentation
    from pptx.util import Pt

    projet = _ans(template, answers, "projet") or "Projet"
    client = _ans(template, answers, "client")
    date = _ans(template, answers, "date")

    sections: list[tuple[str, list[str]]] = [
        ("Faits marquants", [f"Actions réalisées : {b}" for b in _lines(_ans(template, answers, "faits_realise"))]
                            + [f"Alerte / vigilance : {b}" for b in _lines(_ans(template, answers, "faits_alerte"))]
                            + [f"En cours / à initier : {b}" for b in _lines(_ans(template, answers, "faits_encours"))]),
        ("Suivi de l'activité", _lines(_ans(template, answers, "suivi"))),
        ("Plan d'action, décisions et risques", _lines(_ans(template, answers, "plan"))),
        ("Perspectives et divers", _lines(_ans(template, answers, "perspectives"))),
    ]

    prs = Presentation()
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = f"COPIL {projet}" + (f" — {date}" if date else "")
    if title_slide.placeholders and len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = " · ".join(x for x in (client, date) if x)

    for section, bullets in sections:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = section
        body = slide.placeholders[1].text_frame
        body.word_wrap = True
        items = bullets or ["(à compléter)"]
        for i, b in enumerate(items):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = b
            p.font.size = Pt(16)

    buf = io.BytesIO()
    prs.save(buf)
    fname = f"COPIL_{_slug(projet)}.pptx"
    return fname, "application/vnd.openxmlformats-officedocument.presentationml.presentation", buf.getvalue()


# ------------------------------------------------------------------ Matrice de couverture (.xlsx)

def _parse_exigences(text: str, impacts: list[str], probas: list[str], grid: dict) -> list[dict]:
    out = []
    for line in _lines(text):
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        impact = T.match_scale(parts[1], impacts, impacts[1]) if len(parts) > 1 else impacts[1]
        proba = T.match_scale(parts[2], probas, probas[1]) if len(parts) > 2 else probas[1]
        out.append({"name": name, "impact": impact, "proba": proba, "crit": T.criticite(impact, proba, grid)})
    return out


def _parse_coverage(text: str) -> dict[str, list[str]]:
    """'cas d'usage : exigenceA, exigenceB' → {cas: [exigences]} (normalised keys)."""
    cover: dict[str, list[str]] = {}
    for line in _lines(text):
        if ":" not in line:
            continue
        cas, exs = line.split(":", 1)
        cover[T._norm(cas)] = [T._norm(e) for e in exs.split(",") if e.strip()]
    return cover


def generate_matrice(template: dict, answers: dict) -> tuple[str, str, bytes]:
    """Legacy free-text path: answers → draft → workbook (same builder as the extracted path)."""
    impacts = template.get("impacts") or T.CRITICITE_IMPACTS
    probas = template.get("probas") or T.CRITICITE_PROBAS
    grid = template.get("grid") or T.CRITICITE_GRID
    projet = _ans(template, answers, "projet") or "Projet"
    exigences = _parse_exigences(_ans(template, answers, "exigences"), impacts, probas, grid)
    cover = _parse_coverage(_ans(template, answers, "couverture"))
    draft = {
        "exigences": [{"ref": "", "name": e["name"], "impact": e["impact"], "probabilite": e["proba"]} for e in exigences],
        "cas_usage": [{"ref": "", "name": c, "exigences": cover.get(T._norm(c), [])} for c in _lines(_ans(template, answers, "cas"))],
    }
    return generate_matrice_from_draft(template, draft, projet)


def _covers(case_refs: list[str], ex: dict) -> bool:
    """A use case covers a requirement when it lists its ref, or its name (fuzzy, either direction)."""
    exref, exn = T._norm(ex.get("ref", "")), T._norm(ex.get("name", ""))
    for w in case_refs:
        wn = T._norm(w)
        if not wn:
            continue
        if exref and wn == exref:
            return True
        if not exref and exn and (wn in exn or exn in wn):
            return True
        if exref and not REF_LIKE.fullmatch(w.strip()) and exn and (wn in exn or exn in wn):
            return True
    return False


def generate_matrice_from_draft(template: dict, draft: dict, projet: str = "Projet") -> tuple[str, str, bytes]:
    """The workbook: sheet 'Matrice' = exigences (columns) × cas d'usage (rows) with Impact /
    Probabilité / Criticité per requirement and an 'X' where a use case covers it; sheet 'Criticité'
    = the coloured grid. Missing impact/probabilité → default (middle of scale) and flagged."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    impacts = template.get("impacts") or T.CRITICITE_IMPACTS
    probas = template.get("probas") or T.CRITICITE_PROBAS
    grid = template.get("grid") or T.CRITICITE_GRID

    exigences = []
    for e in draft.get("exigences") or []:
        impact = T.match_scale(e.get("impact") or "", impacts, impacts[1])
        proba = T.match_scale(e.get("probabilite") or e.get("proba") or "", probas, probas[1])
        exigences.append({"ref": str(e.get("ref") or ""), "name": str(e.get("name") or ""), "impact": impact, "proba": proba,
                          "crit": T.criticite(impact, proba, grid), "guessed": not (e.get("impact") and (e.get("probabilite") or e.get("proba")))})
    cases = [{"ref": str(c.get("ref") or ""), "name": str(c.get("name") or ""), "refs": list(c.get("exigences") or [])}
             for c in (draft.get("cas_usage") or []) if c.get("name")]

    wb = Workbook()
    ws = wb.active
    ws.title = "Matrice"
    head = Font(bold=True)
    ws.cell(1, 1, "Cas d'usage \\ Exigences").font = head
    for j, ex in enumerate(exigences):
        label = f"{ex['ref']} — {ex['name']}" if ex["ref"] else ex["name"]
        c = ws.cell(1, 2 + j, label); c.font = head; c.alignment = Alignment(text_rotation=45, wrap_text=True)
    for r, label, key in ((2, "Impact", "impact"), (3, "Probabilité", "proba"), (4, "Criticité", "crit")):
        ws.cell(r, 1, label).font = head
        for j, ex in enumerate(exigences):
            cell = ws.cell(r, 2 + j, ex[key])
            if ex["guessed"] and key != "crit":
                cell.font = Font(italic=True, color="888888")   # default value, not from the documents
    for i, c in enumerate(cases):
        ws.cell(5 + i, 1, f"{c['ref']} — {c['name']}" if c["ref"] else c["name"])
        for j, ex in enumerate(exigences):
            if _covers(c["refs"], ex):
                ws.cell(5 + i, 2 + j, "X").alignment = Alignment(horizontal="center")
    ws.column_dimensions["A"].width = 44

    cs = wb.create_sheet("Criticité")
    cs.cell(1, 1, "Impact \\ Probabilité").font = head
    for j, p in enumerate(probas):
        cs.cell(1, 2 + j, p).font = head
    fills = {3: "F4C0C0", 2: "F7E1B5", 1: "CFE8C6"}
    for i, imp in enumerate(impacts):
        cs.cell(2 + i, 1, imp).font = head
        for j, p in enumerate(probas):
            score = int((grid.get(imp) or {}).get(p, 1))
            cell = cs.cell(2 + i, 2 + j, score)
            cell.alignment = Alignment(horizontal="center")
            cell.fill = PatternFill("solid", fgColor=fills.get(score, "FFFFFF"))
    cs.column_dimensions["A"].width = 20

    buf = io.BytesIO()
    wb.save(buf)
    fname = f"Matrice_couverture_{_slug(projet)}.xlsx"
    return fname, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue()


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in T._norm(s))[:40].strip("_") or "projet"


def generate(key: str, template: dict, answers: dict, draft: dict | None = None, projet: str = "") -> tuple[str, str, bytes]:
    """`draft` (the extracted/confirmed structure) wins over free-text `answers` when given."""
    if key == "copil":
        return generate_copil(template, answers)
    if key == "matrice_couverture":
        if draft:
            return generate_matrice_from_draft(template, draft, projet or _ans(template, answers, "projet") or "Projet")
        return generate_matrice(template, answers)
    if key == "plan_de_charge":
        from app.deliverables import plan as P

        if not draft or not draft.get("weeks"):
            raise ValueError("Le plan de charge se génère à partir d'un brouillon préparé (matrice + équipe)")
        return P.generate_plan_from_draft(template, draft, projet or "Projet")
    raise ValueError(f"Livrable inconnu : {key}")
