"""Client-ready deliverables: COPIL deck (PPTX), pre-invoice with supporting details (XLSX),
meeting minutes and onboarding pack (DOCX). Figures come from the same engines as the UI."""

import io
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.knowledge_transfer import build_map
from app.analytics import capacity as capacity_engine
from app.analytics.billing import prebill
from app.analytics.kpi import service_kpis
from app.models import (Application, BacklogItem, Dependency, Meeting, Perimeter, Recommendation, TeamMember, Ticket,
                        Timesheet)

NAVY = (0x11, 0x1A, 0x2E)
ACCENT = (0x35, 0x53, 0xD6)
STATUS_RGB = {"green": (0x1A, 0x8F, 0x5A), "amber": (0xB8, 0x6E, 0x00), "red": (0xC8, 0x38, 0x3B), "grey": (0x8A, 0x96, 0xA8)}
STATUS_FR = {"green": "Maîtrisé", "amber": "Sous tension", "red": "En dérive", "grey": "N/A"}


def _all(db: Session, model, pid: str):
    return db.scalars(select(model).where(model.perimeter_id == pid)).all()


def _latest(db: Session, pid: str, kind: str) -> Recommendation | None:
    recs = db.scalars(select(Recommendation).where(Recommendation.perimeter_id == pid, Recommendation.kind == kind).order_by(Recommendation.created_at.desc())).all()
    return next((r for r in recs if r.status == "approved"), recs[0] if recs else None)


def _prev_month() -> tuple[int, int]:
    today = date.today()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


# ================================================================ COPIL deck

def copil_pptx(db: Session, perimeter: Perimeter) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Emu, Inches, Pt

    pid = perimeter.id
    tickets = _all(db, Ticket, pid)
    kpis = service_kpis(tickets, perimeter.contract)
    year, month = _prev_month()
    bill = prebill(perimeter.contract, _all(db, TeamMember, pid), tickets, _all(db, Timesheet, pid), year, month)
    cap = capacity_engine.analyze(_all(db, TeamMember, pid), tickets, _all(db, Timesheet, pid), _all(db, BacklogItem, pid))
    copil = _latest(db, pid, "copil")
    pack = copil.payload if copil else {}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def rgb(t):
        return RGBColor(*t)

    def textbox(slide, x, y, w, h, text, size=14, bold=False, color=(0x16, 0x20, 0x2E)):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        lines = text if isinstance(text, list) else [text]
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = line
            p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, rgb(color)
            p.space_after = Pt(6)
        return box

    def header(title, subtitle=""):
        slide = prs.slides.add_slide(blank)
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.9))
        bar.fill.solid()
        bar.fill.fore_color.rgb = rgb(NAVY)
        bar.line.fill.background()
        textbox(slide, 0.5, 0.18, 10, 0.6, title, 24, True, (0xFF, 0xFF, 0xFF))
        textbox(slide, 9.3, 0.28, 3.6, 0.5, f"{perimeter.client.name} · {perimeter.name}", 11, False, (0xC3, 0xCB, 0xDB))
        if subtitle:
            textbox(slide, 0.5, 1.05, 12.3, 0.5, subtitle, 13, False, (0x5D, 0x6B, 0x7E))
        return slide

    def table(slide, x, y, w, rows, col_widths=None, font=11):
        shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(0.4 * len(rows)))
        t = shape.table
        for c, width in enumerate(col_widths or []):
            t.columns[c].width = Emu(int(Inches(width)))
        for r, values in enumerate(rows):
            for c, v in enumerate(values):
                cell = t.cell(r, c)
                cell.text = "" if v is None else str(v)
                para = cell.text_frame.paragraphs[0]
                para.font.size = Pt(font)
                para.font.bold = r == 0
                if r == 0:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = rgb(ACCENT)
                    para.font.color.rgb = rgb((0xFF, 0xFF, 0xFF))
        return t

    # 1. title
    slide = prs.slides.add_slide(blank)
    bg = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = rgb(NAVY)
    bg.line.fill.background()
    textbox(slide, 0.9, 2.3, 11, 1, "Comité de pilotage", 40, True, (0xFF, 0xFF, 0xFF))
    textbox(slide, 0.9, 3.3, 11, 0.8, f"{perimeter.client.name} — {perimeter.name}", 24, False, (0x9F, 0xB2, 0xFF))
    textbox(slide, 0.9, 4.2, 11, 0.6, f"Période de référence : {kpis['reference_month']} · édité le {date.today():%d/%m/%Y}", 14, False, (0xC3, 0xCB, 0xDB))
    status = kpis["overall_status"]
    textbox(slide, 0.9, 5.2, 11, 0.6, f"Statut global : {STATUS_FR[status]}", 18, True, STATUS_RGB[status])

    # 2. executive summary
    slide = header("Synthèse", pack.get("headline", ""))
    textbox(slide, 0.5, 1.7, 6, 0.4, "Points forts", 16, True, STATUS_RGB["green"])
    textbox(slide, 0.5, 2.1, 6, 4.5, [f"• {x}" for x in pack.get("highlights", [])] or ["—"], 13)
    textbox(slide, 6.9, 1.7, 6, 0.4, "Points d'attention", 16, True, STATUS_RGB["red"])
    textbox(slide, 6.9, 2.1, 6, 4.5, [f"• {x}" for x in pack.get("concerns", [])] or ["—"], 13)
    if copil and copil.status != "approved":
        textbox(slide, 0.5, 6.8, 12, 0.4, "Brouillon — support non encore validé par le Delivery Manager", 11, True, STATUS_RGB["amber"])

    # 3. scorecard + trend chart
    slide = header("Indicateurs de service", f"Mois de référence {kpis['reference_month']} — cibles contractuelles")
    rows = [["Indicateur", "Valeur", "Cible", "Statut"]] + [[s["kpi"], f"{s['value']}{s['unit']}" if s["value"] is not None else "—", f"{s['target']}{s['unit']}", STATUS_FR[s["status"]]] for s in kpis["scorecard"]]
    t = table(slide, 0.5, 1.7, 5.8, rows, [2.4, 1.1, 1.1, 1.2], 12)
    for r, s in enumerate(kpis["scorecard"], start=1):
        t.cell(r, 3).text_frame.paragraphs[0].font.color.rgb = rgb(STATUS_RGB[s["status"]])
    sla_rows = [["Priorité", "Résolus", "Taux", "Cible"]] + [[p, c["resolved"], f"{c['pct']} %", f"{c['target_pct']} %"] for p, c in kpis["sla_by_priority_last_month"].items()]
    table(slide, 0.5, 4.5, 5.8, sla_rows, [1.4, 1.4, 1.5, 1.5], 12)
    chart_data = CategoryChartData()
    chart_data.categories = [m["month"] for m in kpis["monthly"]]
    chart_data.add_series("Incidents ouverts", [m["incidents_opened"] for m in kpis["monthly"]])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6.7), Inches(1.7), Inches(6.2), Inches(2.6), chart_data).chart
    chart.has_legend, chart.legend.position, chart.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    sla_data = CategoryChartData()
    sla_data.categories = [m["month"] for m in kpis["monthly"]]
    sla_data.add_series("Respect SLA (%)", [m["sla_pct"] or 0 for m in kpis["monthly"]])
    chart2 = slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(6.7), Inches(4.4), Inches(6.2), Inches(2.6), sla_data).chart
    chart2.has_legend, chart2.legend.position, chart2.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False

    # 4. finance
    slide = header("Facturation", f"Pré-facture {bill['period']} — total HT {bill['total_excl_tax']:,.2f} {bill['currency']}".replace(",", " "))
    by_section = defaultdict(float)
    for line in bill["lines"]:
        by_section[line["section"]] += line["amount"]
    table(slide, 0.5, 1.7, 6, [["Section", "Montant HT"]] + [[k, f"{v:,.2f} €".replace(",", " ")] for k, v in by_section.items()], [3.8, 2.2], 12)
    kinds = defaultdict(int)
    for a in bill["anomalies"]:
        kinds[a["kind"]] += 1
    textbox(slide, 6.9, 1.7, 6, 0.4, "Écarts à arbitrer", 16, True)
    textbox(slide, 6.9, 2.1, 6, 4.5, [f"• {a['message']}" for a in bill["anomalies"] if a["severity"] == "high"][:8] or ["Aucun écart bloquant"], 11)

    # 5. capacity
    slide = header("Capacité et staffing", f"{cap['overload_weeks']} semaine(s) en surcharge sur 8 · écart {cap['capacity_gap_h']} h (≈ {cap['fte_gap']} ETP)")
    cdata = CategoryChartData()
    cdata.categories = [w["week"][5:] for w in cap["forecast"]]
    cdata.add_series("Capacité (h)", [w["capacity_h"] for w in cap["forecast"]])
    cdata.add_series("Demande prévue (h)", [w["demand_h"] for w in cap["forecast"]])
    ch = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.7), Inches(7.3), Inches(5.2), cdata).chart
    ch.has_legend, ch.legend.position, ch.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    risky = [f"• {c['skill']} : {', '.join(c['holders']) or 'aucun détenteur'}" for c in cap["skill_coverage"] if c["at_risk"]]
    textbox(slide, 8.2, 1.7, 4.7, 0.4, "Compétences à sécuriser", 16, True)
    textbox(slide, 8.2, 2.1, 4.7, 4.8, risky or ["Couverture satisfaisante"], 12)

    # 6. decisions + action plan
    slide = header("Décisions et plan d'action")
    textbox(slide, 0.5, 1.2, 12, 0.4, "Décisions attendues du client", 16, True)
    textbox(slide, 0.5, 1.6, 12, 1.6, [f"• {x}" for x in pack.get("decisions_requested", [])] or ["—"], 13)
    actions = pack.get("action_plan", [])
    if actions:
        table(slide, 0.5, 3.4, 12.3, [["Action", "Porteur", "Échéance", "Indicateur"]] + [[a["action"], a["owner"], a["due"], a["kpi_impacted"]] for a in actions[:8]], [6.3, 2.2, 1.8, 2.0], 11)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ================================================================ pre-invoice workbook

def prebilling_xlsx(db: Session, perimeter: Perimeter, year: int, month: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    pid = perimeter.id
    members = _all(db, TeamMember, pid)
    tickets = _all(db, Ticket, pid)
    sheets = _all(db, Timesheet, pid)
    result = prebill(perimeter.contract, members, tickets, sheets, year, month)
    names = {m.id: m for m in members}
    titles = {t.ref: t for t in tickets}

    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="3553D6")
    money = '#,##0.00 "€"'

    def sheet(ws, headers, rows, widths):
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Synthèse"
    ws["A1"], ws["A1"].font = f"Pré-facture {result['period']} — {perimeter.client.name} / {perimeter.name}", Font(bold=True, size=14)
    ws["A3"], ws["B3"] = "Total HT", result["total_excl_tax"]
    ws["B3"].number_format, ws["A3"].font = money, Font(bold=True)
    ws["A4"], ws["B4"] = "Lignes", len(result["lines"])
    ws["A5"], ws["B5"] = "Écarts bloquants", sum(1 for a in result["anomalies"] if a["severity"] == "high")
    ws["A6"], ws["B6"] = "Généré le", datetime.now().strftime("%d/%m/%Y %H:%M")
    ws["A8"], ws["A8"].font = "Heures par code activité", Font(bold=True)
    for i, (code, h) in enumerate(result["hours_by_code"].items(), start=9):
        ws.cell(i, 1, code)
        ws.cell(i, 2, h)
    ws["A20"] = "Document de travail — montants calculés par le moteur de règles contractuelles, à valider avant émission."
    ws["A20"].font = Font(italic=True, color="8A96A8")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 18

    lines = wb.create_sheet("Lignes")
    sheet(lines, ["Section", "Libellé", "Quantité", "Prix unitaire", "Montant HT", "Justification", "Références"],
          [[l["section"], l["label"], l["quantity"], l["unit_price"], l["amount"], l["justification"], ", ".join(l["refs"])] for l in result["lines"]],
          [22, 60, 11, 15, 15, 40, 40])
    for row in lines.iter_rows(min_row=2):
        row[3].number_format = row[4].number_format = money
    lines.append(["", "Total HT", "", "", result["total_excl_tax"]])
    lines.cell(lines.max_row, 5).number_format = money
    lines.cell(lines.max_row, 2).font = lines.cell(lines.max_row, 5).font = Font(bold=True)

    just = wb.create_sheet("Justificatifs")
    month_sheets = sorted([s for s in sheets if s.day.year == year and s.day.month == month], key=lambda s: (s.day, names[s.member_id].name if s.member_id in names else ""))
    sheet(just, ["Date", "Collaborateur", "Profil", "Code activité", "Ticket", "Titre du ticket", "Heures", "Commentaire"],
          [[s.day, names[s.member_id].name if s.member_id in names else s.member_id, names[s.member_id].profile if s.member_id in names else "", s.activity_code, s.ticket_ref or "",
            titles[s.ticket_ref].title if s.ticket_ref in titles else "", s.hours, s.comment] for s in month_sheets],
          [12, 22, 12, 14, 12, 50, 9, 30])
    for row in just.iter_rows(min_row=2):
        row[0].number_format = "DD/MM/YYYY"

    gaps = wb.create_sheet("Écarts")
    sheet(gaps, ["Gravité", "Type", "Description", "Montant", "Références"],
          [[a["severity"], a["kind"], a["message"], a["amount"], ", ".join(a["refs"])] for a in sorted(result["anomalies"], key=lambda a: a["severity"] != "high")],
          [10, 28, 90, 14, 30])
    red = PatternFill("solid", fgColor="FDE8E8")
    for row in gaps.iter_rows(min_row=2):
        row[3].number_format = money
        row[2].alignment = Alignment(wrap_text=True)
        if row[0].value == "high":
            for c in row:
                c.fill = red

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================ Word documents

def _doc():
    import docx
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(11)
    for level in (1, 2):
        d.styles[f"Heading {level}"].font.color.rgb = RGBColor(*ACCENT)
    return d


def _bullets(d, items):
    for x in items or ["—"]:
        d.add_paragraph(str(x), style="List Bullet")


def minutes_docx(db: Session, meeting: Meeting) -> bytes:
    perimeter = db.get(Perimeter, meeting.perimeter_id)
    m = meeting.minutes or {}
    d = _doc()
    d.add_heading(f"Compte-rendu — {meeting.title}", 0)
    d.add_paragraph(f"{perimeter.client.name} / {perimeter.name} — réunion du {meeting.held_at:%d/%m/%Y %H:%M}")
    d.add_heading("Synthèse", 1)
    d.add_paragraph(m.get("summary", "Compte-rendu non encore généré."))
    d.add_heading("Décisions", 1)
    _bullets(d, m.get("decisions"))
    d.add_heading("Actions", 1)
    actions = m.get("actions") or []
    if actions:
        t = d.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(("Action", "Responsable", "Échéance")):
            t.rows[0].cells[i].text = h
        for a in actions:
            cells = t.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = a["action"], a["owner"], a["due"] or "—"
    else:
        d.add_paragraph("—")
    d.add_heading("Risques évoqués", 1)
    _bullets(d, [f"{r['title']} — {r['description']}" for r in m.get("risks", [])])
    d.add_heading("Nouvelles demandes du client", 1)
    _bullets(d, m.get("client_requests"))
    d.add_heading("Transcription", 2)
    for line in meeting.transcript.splitlines():
        if line.strip():
            d.add_paragraph(line.strip())
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def onboarding_docx(db: Session, perimeter: Perimeter) -> bytes:
    pid = perimeter.id
    plan_rec = _latest(db, pid, "onboarding_plan")
    plan = plan_rec.payload if plan_rec else {}
    carto = build_map(_all(db, Application, pid), _all(db, Dependency, pid), _all(db, Ticket, pid))
    risks = db.scalars(select(Recommendation).where(Recommendation.perimeter_id == pid, Recommendation.kind.in_(("risk", "knowledge")), Recommendation.status.in_(("pending", "approved")))
                       .order_by(Recommendation.created_at.desc())).all()

    d = _doc()
    d.add_heading(f"Dossier d'onboarding — {perimeter.name}", 0)
    d.add_paragraph(f"Client : {perimeter.client.name} · édité le {date.today():%d/%m/%Y}" + ("" if not plan_rec or plan_rec.status == "approved" else " · BROUILLON non validé"))
    d.add_heading("Synthèse exécutive", 1)
    d.add_paragraph(plan.get("executive_summary") or plan_rec.detail if plan_rec else "Lancer l'analyse d'onboarding pour générer la synthèse.")

    d.add_heading("Cartographie applicative", 1)
    t = d.add_table(rows=1, cols=6)
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(("Application", "Technologie", "Criticité", "Incidents", "Dépendants", "Score risque")):
        t.rows[0].cells[i].text = h
    for n in carto["nodes"]:
        cells = t.add_row().cells
        values = (n["name"] + (" (SPOF)" if n["single_point_of_failure"] else ""), n["technology"], n["criticality"], n["incidents"], n["dependents"], n["risk_score"])
        for i, v in enumerate(values):
            cells[i].text = str(v)
    d.add_heading("Flux inter-applicatifs", 2)
    _bullets(d, [f"{e['source_name']} → {e['target_name']} ({e['kind']}) {e['label']}" for e in carto["edges"]])

    d.add_heading("Risques identifiés", 1)
    seen = set()
    for r in risks:
        if r.title in seen:
            continue
        seen.add(r.title)
        p = d.add_paragraph(style="List Bullet")
        p.add_run(f"[{r.impact}] {r.title}").bold = True
        p.add_run(f" — {r.detail}")
        if r.sources:
            p.add_run(f" (sources : {', '.join(s['label'] for s in r.sources[:3])})").italic = True

    if plan.get("app_cards"):
        d.add_heading("Fiches applicatives", 1)
        for card in plan["app_cards"]:
            d.add_heading(card["application"], 2)
            d.add_paragraph(card["purpose"] or "—")
            for label, key in (("Flux principaux", "key_flows"), ("Exploitation", "operations"), ("Incidents connus", "known_issues"), ("Points de vigilance", "watch_points")):
                if card.get(key):
                    d.add_paragraph().add_run(label).bold = True
                    _bullets(d, card[key])

    d.add_heading("Plan d'onboarding", 1)
    for w in plan.get("weeks", []):
        d.add_heading(f"Semaine {w['week']} — {w['focus']}", 2)
        _bullets(d, w["activities"])
        d.add_paragraph(f"Livrables : {', '.join(w['deliverables'])}")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
