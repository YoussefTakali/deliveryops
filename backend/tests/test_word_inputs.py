"""A Word cahier des charges / spec must feed the matrice extractor exactly like the markdown samples."""
import io

import docx

from app.deliverables.extract import deterministic_draft
from app.deliverables import templates as T
from app.knowledge import extract_text


def _cahier() -> bytes:
    d = docx.Document()
    d.add_heading("Cahier des charges", 0)
    d.add_heading("4. Exigences", 1)
    t = d.add_table(rows=1, cols=4)
    for i, h in enumerate(["Réf.", "Exigence", "Impact", "Probabilité"]):
        t.rows[0].cells[i].text = h
    for row in [["EXF-01", "Authentification forte", "Catastrophique", "Inévitable"], ["EXF-02", "Consultation des contrats", "Grave", ""]]:
        c = t.add_row().cells
        for i, v in enumerate(row):
            c[i].text = v
    d.add_paragraph("Après le tableau.")
    b = io.BytesIO(); d.save(b); return b.getvalue()


def _spec() -> bytes:
    d = docx.Document()
    d.add_heading("Spécifications", 0)
    d.add_heading("US-01 — Se connecter avec un code SMS", 2)
    d.add_paragraph("Exigences couvertes : EXF-01.")
    d.add_heading("US-02 — Voir mes contrats", 2)
    d.add_paragraph("Exigences couvertes : EXF-02, EXF-01.")
    b = io.BytesIO(); d.save(b); return b.getvalue()


def test_docx_becomes_markdown_in_document_order():
    text = extract_text("cdc.docx", _cahier())
    lines = text.splitlines()
    assert lines[0] == "# Cahier des charges" and "# 4. Exigences" in lines
    assert "| EXF-01 | Authentification forte | Catastrophique | Inévitable |" in lines
    assert lines.index("| EXF-01 | Authentification forte | Catastrophique | Inévitable |") < lines.index("Après le tableau.")
    assert extract_text("spec.docx", _spec()).splitlines()[1] == "## US-01 — Se connecter avec un code SMS"


def test_word_cahier_and_spec_feed_the_matrice():
    texts = {"cdc.docx": extract_text("cdc.docx", _cahier()), "spec.docx": extract_text("spec.docx", _spec())}
    draft = deterministic_draft(texts, [], T.CRITICITE_IMPACTS, T.CRITICITE_PROBAS)
    assert [e.ref for e in draft.exigences] == ["EXF-01", "EXF-02"]
    assert draft.exigences[0].impact and draft.exigences[0].probabilite and not draft.exigences[1].probabilite
    by_ref = {c.ref: c for c in draft.cas_usage}
    assert by_ref["US-01"].exigences == ["EXF-01"] and sorted(by_ref["US-02"].exigences) == ["EXF-01", "EXF-02"]


def test_pipe_less_rows_are_parsed_too():
    draft = deterministic_draft({"x.txt": "EXF-07 | Modifier le RIB | Grave | Fréquent"}, [], T.CRITICITE_IMPACTS, T.CRITICITE_PROBAS)
    assert [e.ref for e in draft.exigences] == ["EXF-07"]
