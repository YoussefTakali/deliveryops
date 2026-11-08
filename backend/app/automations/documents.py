"""Pure document helpers used by the server steps: read a table out of a file, and fill a template.

Both work on bytes in / bytes out so they're trivially testable; storage lives in steps.py.
"""

import csv
import datetime as dt
import html
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

MAX_ROWS = 5000


class DocumentError(ValueError):
    """User-facing problem with a file (French message)."""


def normalize_key(value: Any) -> str:
    """Case/accent/separator-insensitive key: "Date de début" == "date_de_debut" == "DATE DE DEBUT"."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[\s_\-]+", " ", text).strip()


def extension(name: str) -> str:
    return PurePath(name or "").suffix.lower()


# ---------------------------------------------------------------- values

def _cell(value: Any) -> Any:
    """JSON-safe cell value: dates -> ISO, integral floats -> int, strings stripped."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0, 0) else value.isoformat(timespec="minutes")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return text


def display(value: Any) -> str:
    """How a data value is written into a document."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        y, m, d = value.split("-")
        return f"{d}/{m}/{y}"  # ISO dates from extract_table read naturally in a French document
    if isinstance(value, (list, tuple)):
        return ", ".join(display(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k} : {display(v)}" for k, v in value.items())
    return str(value)


# ---------------------------------------------------------------- tables

def _columns(header: list[Any]) -> list[str]:
    cols, seen = [], {}
    for i, h in enumerate(header):
        name = str(_cell(h) if h is not None else "").strip() or f"Colonne {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        cols.append(name)
    return cols


def _rows_to_table(raw_rows: list[list[Any]]) -> dict:
    rows = [r for r in raw_rows if any(c not in (None, "") for c in r)]
    if not rows:
        return {"rows": [], "columns": [], "count": 0}
    header, body = rows[0], rows[1:]
    # trim trailing empty header columns that no data row uses
    width = max(len(r) for r in rows)
    while width > 0 and (width > len(header) or header[width - 1] in (None, "")) and all(len(r) < width or r[width - 1] in (None, "") for r in body):
        width -= 1
    columns = _columns(list(header[:width]) + [None] * (width - len(header)))
    out = []
    for r in body[:MAX_ROWS]:
        cells = [_cell(c) for c in list(r[:width]) + [None] * (width - len(r))]
        out.append(dict(zip(columns, cells)))
    return {"rows": out, "columns": columns, "count": len(out)}


def _xlsx_table(data: bytes, sheet: str | None) -> dict:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises many types for corrupt files
        raise DocumentError(f"Classeur Excel illisible : {exc}") from exc
    try:
        if sheet:
            match = next((ws for ws in wb.worksheets if ws.title == sheet), None) or \
                next((ws for ws in wb.worksheets if normalize_key(ws.title) == normalize_key(sheet)), None)
            if match is None:
                raise DocumentError(f"Feuille « {sheet} » introuvable (feuilles : {', '.join(wb.sheetnames)})")
            candidates = [match]
        else:
            candidates = wb.worksheets
        for ws in candidates:
            table = _rows_to_table([list(r) for r in ws.iter_rows(values_only=True)])
            if table["columns"]:
                return table
        return {"rows": [], "columns": [], "count": 0}
    finally:
        wb.close()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _csv_table(data: bytes) -> dict:
    text = _decode(data)
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        first = next((line for line in sample.splitlines() if line.strip()), "")
        delimiter = max(";,\t", key=first.count)
    return _rows_to_table([list(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)])


def _docx_table(data: bytes) -> dict:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Document Word illisible : {exc}") from exc
    if not document.tables:
        return {"rows": [], "columns": [], "count": 0}
    table = document.tables[0]
    return _rows_to_table([[cell.text.strip() for cell in row.cells] for row in table.rows])


def extract_table_structured(name: str, data: bytes, sheet: str | None = None) -> dict | None:
    """Deterministic extraction for tabular formats; None when the format needs the LLM (pdf/text)."""
    ext = extension(name)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_table(data, sheet)
    if ext in (".csv", ".tsv"):
        return _csv_table(data)
    if ext == ".docx":
        return _docx_table(data)
    if ext == ".xls":
        raise DocumentError("Format .xls (Excel 97-2003) non pris en charge : enregistrez le fichier en .xlsx")
    return None


def rows_from_matrix(columns: list[str], matrix: list[list[Any]]) -> dict:
    """Shape an LLM-extracted table like the deterministic ones."""
    return _rows_to_table([list(columns)] + [list(r) for r in matrix])


# ---------------------------------------------------------------- templates

# {{ Key }}, «Key» (Word merge-field look, optional spaces/nbsp), [Key]
PLACEHOLDER = re.compile(r"\{\{\s*([^{}\n]{1,80}?)\s*\}\}|«[\s ]*([^«»\n]{1,80}?)[\s ]*»|\[([^\[\]\n]{1,80})\]")


@dataclass
class _Filler:
    data: dict
    missing: list[str] = field(default_factory=list)
    filled: int = 0

    def __post_init__(self):
        self._by_norm = {}
        for k, v in (self.data or {}).items():
            self._by_norm.setdefault(normalize_key(k), v)

    def lookup(self, key: str) -> tuple[bool, Any]:
        key = key.strip()
        if key in self.data:
            return True, self.data[key]
        norm = normalize_key(key)
        if norm in self._by_norm:
            return True, self._by_norm[norm]
        return False, None

    def replacement(self, match: re.Match, *, escape_html: bool = False) -> str | None:
        """Replacement text for a placeholder match, or None to leave it untouched. `[Key]` is only
        replaced when Key is a known data key (square brackets are common in ordinary text)."""
        curly, guillemet, bracket = match.group(1), match.group(2), match.group(3)
        key = curly or guillemet or bracket
        found, value = self.lookup(key)
        if not found:
            if bracket is None and key.strip() not in self.missing:
                self.missing.append(key.strip())
            return None
        self.filled += 1
        text = display(value)
        return html.escape(text) if escape_html else text

    def fill_string(self, text: str, *, escape_html: bool = False) -> str:
        def sub(m: re.Match) -> str:
            rep = self.replacement(m, escape_html=escape_html)
            return m.group(0) if rep is None else rep
        return PLACEHOLDER.sub(sub, text)


def _docx_paragraph(p, filler: _Filler) -> None:
    """Replace placeholders in one <w:p>, even when Word split them across several runs.

    Works on the paragraph's <w:t> nodes (which also covers runs inside hyperlinks, fields, content
    controls): the concatenated text is matched, and each replacement is written into the <w:t>
    where the placeholder starts (keeping that run's formatting) while the placeholder's remaining
    characters are removed from the following nodes. Newlines in values become <w:br/>."""
    from docx.oxml.ns import qn

    nodes = [t for t in p.iter(qn("w:t"))]
    if not nodes:
        return
    texts = [t.text or "" for t in nodes]
    full = "".join(texts)
    if "{{" not in full and "«" not in full and "[" not in full:
        return
    matches = list(PLACEHOLDER.finditer(full))
    if not matches:
        return
    # node boundaries
    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t)

    def locate(offset: int) -> int:
        for i in range(len(starts) - 1, -1, -1):
            if starts[i] <= offset and (offset < starts[i] + len(texts[i]) or (i == len(starts) - 1)):
                return i
        return 0

    line_breaks: dict[int, str] = {}
    for m in reversed(matches):  # right to left keeps earlier offsets valid
        rep = filler.replacement(m)
        if rep is None:
            continue
        s, e = m.start(), m.end()
        first, last = locate(s), locate(e - 1)
        if first == last:
            local = s - starts[first]
            texts[first] = texts[first][:local] + rep + texts[first][local + (e - s):]
        else:
            local_s = s - starts[first]
            texts[first] = texts[first][:local_s] + rep
            for i in range(first + 1, last):
                texts[i] = ""
            texts[last] = texts[last][e - starts[last]:]
        if "\n" in rep:
            line_breaks[first] = texts[first]
    for node, text in zip(nodes, texts):
        if node.text != text:
            node.text = text
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for i in line_breaks:
        _split_lines(nodes[i])


def _split_lines(t) -> None:
    """Turn "\n" inside a <w:t> into <w:br/> + <w:t> siblings within the same run."""
    from copy import deepcopy

    from docx.oxml import OxmlElement

    lines = (t.text or "").split("\n")
    t.text = lines[0]
    anchor = t
    for line in lines[1:]:
        br = OxmlElement("w:br")
        anchor.addnext(br)
        new_t = deepcopy(t)
        new_t.text = line
        br.addnext(new_t)
        anchor = new_t


def _fill_docx(data: bytes, filler: _Filler) -> bytes:
    import docx
    from docx.opc.part import XmlPart
    from docx.oxml.ns import qn

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Modèle Word illisible : {exc}") from exc
    # Body (incl. tables, nested tables, text boxes), every header/footer, foot/endnotes.
    wanted = re.compile(r"^/word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
    seen = set()
    for part in document.part.package.iter_parts():
        if isinstance(part, XmlPart) and wanted.match(str(part.partname)) and id(part) not in seen:
            seen.add(id(part))
            for p in list(part.element.iter(qn("w:p"))):
                _docx_paragraph(p, filler)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _fill_xlsx(data: bytes, filler: _Filler, keep_vba: bool) -> bytes:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), keep_vba=keep_vba)
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Modèle Excel illisible : {exc}") from exc
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str) or cell.data_type == "f":
                    continue
                whole = PLACEHOLDER.fullmatch(cell.value.strip())
                if whole:
                    found, value = filler.lookup(whole.group(1) or whole.group(2) or whole.group(3))
                    if found and isinstance(value, (int, float)) and not isinstance(value, bool):
                        filler.filled += 1
                        cell.value = value  # keep numbers numeric so formulas/formatting still work
                        continue
                cell.value = filler.fill_string(cell.value)
        if ws.title and PLACEHOLDER.search(ws.title):
            ws.title = filler.fill_string(ws.title)[:31]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


TEMPLATE_EXTENSIONS = (".docx", ".xlsx", ".xlsm", ".txt", ".html", ".htm", ".md", ".csv")


def fill_template(name: str, data: bytes, values: dict) -> tuple[bytes, list[str]]:
    """Returns (filled bytes, placeholders left without a value)."""
    if not isinstance(values, dict):
        raise DocumentError("Les données du modèle doivent être un objet {champ: valeur}")
    filler = _Filler(values)
    ext = extension(name)
    if ext == ".docx":
        out = _fill_docx(data, filler)
    elif ext in (".xlsx", ".xlsm"):
        out = _fill_xlsx(data, filler, keep_vba=ext == ".xlsm")
    elif ext in (".txt", ".md", ".csv"):
        out = filler.fill_string(_decode(data)).encode("utf-8")
    elif ext in (".html", ".htm"):
        out = filler.fill_string(_decode(data), escape_html=True).encode("utf-8")
    elif ext == ".doc":
        raise DocumentError("Format .doc (Word 97-2003) non pris en charge : enregistrez le modèle en .docx")
    else:
        raise DocumentError(f"Type de modèle non pris en charge « {ext or name} » ({', '.join(TEMPLATE_EXTENSIONS)})")
    return out, filler.missing
