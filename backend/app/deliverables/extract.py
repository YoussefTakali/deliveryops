"""Turn a deliverable's input files/data into the structured content its generator needs.

Like the companion, the extractor asks clarifying questions ONLY when something genuinely changes the
result (e.g. requirements without criticité, whether bugs count as use cases). It always produces a
deterministic draft from the documents (tables, bullets, user stories, ticket labels); the LLM is an
optional refinement on top with the deterministic draft as fallback.

Generic by design: each template declares typed `inputs` (file / files / text / connector) and an
extractor; adding a deliverable with different inputs = a new extractor here.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app import llm
from app.deliverables import templates as T

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
REF_RE = re.compile(r"\b([A-Z]{2,6}-\d{1,4})\b")
_BULLET_RE = re.compile(r"^\s*[-*•]?\s*([A-Z]{2,6}-\d{1,4})\s*[:—–-]\s*(.+?)\s*$")
_HEADING_RE = re.compile(r"^\s*#{2,3}\s+(.+?)\s*$")
_COVERS_RE = re.compile(r"exigences?\s+couvertes?\s*:\s*(.+)", re.IGNORECASE)
_US_TITLE_RE = re.compile(r"^(?:US|CU|UC)-\d+\s*[—–-]\s*(.+)$", re.IGNORECASE)


# ---------------------------------------------------------------- schema (also the LLM output shape)

class Exigence(BaseModel):
    ref: str = ""
    name: str
    impact: str = ""
    probabilite: str = ""
    source: str = ""


class CasUsage(BaseModel):
    ref: str = ""
    name: str
    exigences: list[str] = Field(default_factory=list)   # refs (or names) it covers
    source: str = ""
    kind: str = ""                                        # Story | Bug | Task | spec | …


class MatriceDraft(BaseModel):
    exigences: list[Exigence] = Field(default_factory=list)
    cas_usage: list[CasUsage] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------- samples (test data shipped with the app)

def sample_sets() -> list[dict]:
    out = []
    if not SAMPLES_DIR.exists():
        return out
    for d in sorted(p for p in SAMPLES_DIR.iterdir() if p.is_dir()):
        files = [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()]
        out.append({"set": d.name, "label": d.name.replace("_", " ").title(), "files": files})
    return out


def sample_file(set_name: str, name: str) -> Path | None:
    p = (SAMPLES_DIR / set_name / name).resolve()
    if not p.is_file() or SAMPLES_DIR.resolve() not in p.parents:
        return None
    return p


def load_sample_inputs(set_name: str) -> tuple[dict[str, str], list[dict]]:
    """(texts by filename, tickets) for a sample set — lets the user test without uploading anything."""
    texts: dict[str, str] = {}
    tickets: list[dict] = []
    d = SAMPLES_DIR / set_name
    if not d.is_dir():
        raise ValueError(f"Jeu d'exemple inconnu : {set_name}")
    for f in sorted(d.iterdir()):
        if f.suffix == ".json":
            try:
                tickets.extend(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                continue
        elif f.suffix in (".md", ".txt"):
            texts[f.name] = f.read_text(encoding="utf-8")
    return texts, tickets


# ---------------------------------------------------------------- deterministic parsing

def _parse_requirements(text: str, source: str, impacts: list[str], probas: list[str]) -> list[Exigence]:
    out: list[Exigence] = []
    seen: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") or " | " in s:          # markdown rows, or the pipe-less rows some converters emit
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2 and REF_RE.fullmatch(cells[0]) and cells[0] not in seen:
                impact = T.match_scale(cells[2], impacts, "") if len(cells) > 2 and cells[2] else ""
                proba = T.match_scale(cells[3], probas, "") if len(cells) > 3 and cells[3] else ""
                out.append(Exigence(ref=cells[0], name=cells[1], impact=impact, probabilite=proba, source=source))
                seen.add(cells[0])
            continue
        m = _BULLET_RE.match(s)
        if m and m.group(1) not in seen:
            out.append(Exigence(ref=m.group(1), name=m.group(2), source=source))
            seen.add(m.group(1))
    return out


def _parse_user_stories(text: str, source: str) -> list[CasUsage]:
    """'## US-01 — Titre' sections; coverage from 'Exigences couvertes : …' and any refs in the body."""
    out: list[CasUsage] = []
    current: CasUsage | None = None
    for line in text.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            title = h.group(1).strip()
            if current:
                out.append(current)
            m = _US_TITLE_RE.match(title)
            ref = title.split()[0] if m else ""
            current = CasUsage(ref=ref, name=(m.group(1) if m else title).strip(), source=source, kind="spec")
            continue
        if current is None:
            continue
        c = _COVERS_RE.search(line)
        refs = REF_RE.findall(c.group(1)) if c else []
        for r in refs:
            if r not in current.exigences and not r.upper().startswith(("US-", "CU-", "UC-")):
                current.exigences.append(r)
    if current:
        out.append(current)
    return [c for c in out if c.name]


def _tickets_to_cases(tickets: list[dict]) -> list[CasUsage]:
    out: list[CasUsage] = []
    for t in tickets:
        summary = str(t.get("summary") or t.get("title") or "").strip()
        if not summary:
            continue
        refs = {str(x) for x in (t.get("labels") or []) if REF_RE.fullmatch(str(x))}
        refs |= set(REF_RE.findall(str(t.get("description") or ""))) | set(REF_RE.findall(summary))
        key = str(t.get("key") or t.get("ref") or "")
        refs.discard(key)
        out.append(CasUsage(ref=key, name=summary, exigences=sorted(refs), source="jira", kind=str(t.get("type") or "")))
    return out


def deterministic_draft(texts: dict[str, str], tickets: list[dict], impacts: list[str], probas: list[str]) -> MatriceDraft:
    exigences: list[Exigence] = []
    cases: list[CasUsage] = []
    for name, text in texts.items():
        exigences.extend(e for e in _parse_requirements(text, name, impacts, probas) if e.ref not in {x.ref for x in exigences})
        cases.extend(_parse_user_stories(text, name))
    cases.extend(_tickets_to_cases(tickets))
    # de-duplicate use cases by normalised name (a spec story and its Jira ticket often share a title)
    dedup: dict[str, CasUsage] = {}
    for c in cases:
        k = T._norm(c.name)
        if k in dedup:
            for r in c.exigences:
                if r not in dedup[k].exigences:
                    dedup[k].exigences.append(r)
        else:
            dedup[k] = c
    return MatriceDraft(exigences=exigences, cas_usage=list(dedup.values()))


# ---------------------------------------------------------------- clarifying questions + answers

Q_IMPACT = "Certaines exigences n'ont pas d'impact renseigné. Quel impact par défaut leur attribuer ?"
Q_PROBA = "Certaines exigences n'ont pas de probabilité d'apparition. Quelle probabilité par défaut ?"
Q_BUGS = "Des tickets de type Bug sont présents. Faut-il les compter comme des cas d'usage à couvrir ?"


def clarifying_questions(draft: MatriceDraft, answers: dict, impacts: list[str], probas: list[str]) -> list[dict]:
    qs: list[dict] = []
    missing_impact = sum(1 for e in draft.exigences if not e.impact)
    missing_proba = sum(1 for e in draft.exigences if not e.probabilite)
    if draft.exigences and missing_impact and Q_IMPACT not in answers:
        qs.append({"question": Q_IMPACT, "options": list(impacts), "recommended": impacts[1] if len(impacts) > 1 else (impacts[0] if impacts else "")})
    if draft.exigences and missing_proba and Q_PROBA not in answers:
        qs.append({"question": Q_PROBA, "options": list(probas), "recommended": probas[1] if len(probas) > 1 else (probas[0] if probas else "")})
    if any(c.kind.lower() == "bug" for c in draft.cas_usage) and Q_BUGS not in answers:
        # a Bug documents a defect, not a functional scenario → recommended: keep them out of the coverage
        qs.append({"question": Q_BUGS, "options": ["Non, uniquement les stories et tâches", "Oui, les inclure"], "recommended": "Non, uniquement les stories et tâches"})
    return qs[:5]


def apply_answers(draft: MatriceDraft, answers: dict, impacts: list[str], probas: list[str]) -> MatriceDraft:
    imp_default = T.match_scale(answers.get(Q_IMPACT, ""), impacts, "") if answers.get(Q_IMPACT) else ""
    pro_default = T.match_scale(answers.get(Q_PROBA, ""), probas, "") if answers.get(Q_PROBA) else ""
    for e in draft.exigences:
        if not e.impact and imp_default:
            e.impact = imp_default
        if not e.probabilite and pro_default:
            e.probabilite = pro_default
    if answers.get(Q_BUGS, "").lower().startswith("non"):
        draft.cas_usage = [c for c in draft.cas_usage if c.kind.lower() != "bug"]
    return draft


# ---------------------------------------------------------------- the extractor

def extract_matrice(template: dict, texts: dict[str, str], tickets: list[dict], answers: dict | None = None) -> dict:
    """Documents + tickets → {draft, questions, used_llm, sources}. Deterministic first; the LLM may
    refine (better names, inferred coverage) but never removes what the documents state."""
    answers = answers or {}
    impacts = template.get("impacts") or T.CRITICITE_IMPACTS
    probas = template.get("probas") or T.CRITICITE_PROBAS
    base = deterministic_draft(texts, tickets, impacts, probas)

    used = False
    if base.exigences or base.cas_usage:
        corpus = "\n\n".join(f"<document name=\"{n}\">\n{t[:6000]}\n</document>" for n, t in texts.items())
        if tickets:
            corpus += "\n\n<tickets>\n" + json.dumps(tickets[:80], ensure_ascii=False) + "\n</tickets>"
        result, used = llm.structured(
            schema=MatriceDraft, tier="agent", thinking=False, max_tokens=3000,
            system=("Tu prépares une matrice de couverture de test. À partir du cahier des charges, des spécifications "
                    "et des tickets, liste les EXIGENCES métier (ref, intitulé court, impact et probabilité d'apparition "
                    f"si le document les donne — échelles : impacts {impacts}, probabilités {probas} — sinon laisse vide) "
                    "et les CAS D'USAGE / user stories (ref, intitulé, kind, et les refs d'exigences qu'ils couvrent). "
                    "N'invente aucune exigence ni couverture absente des documents. Réponds en français."),
            context=corpus + "\n\n<brouillon_deterministe>\n" + base.model_dump_json() + "\n</brouillon_deterministe>",
            task="Renvoie la matrice structurée (exigences, cas_usage), en complétant le brouillon sans le contredire.",
            fallback=lambda: base,
        )
        draft = result if (used and result.exigences) else base
        if used:  # never lose deterministic facts: union coverage + keep any exigence the LLM dropped
            known = {e.ref for e in draft.exigences if e.ref}
            for e in base.exigences:
                if e.ref and e.ref not in known:
                    draft.exigences.append(e)
            by_name = {T._norm(c.name): c for c in draft.cas_usage}
            for c in base.cas_usage:
                tgt = by_name.get(T._norm(c.name))
                if tgt is None:
                    draft.cas_usage.append(c)
                else:
                    for r in c.exigences:
                        if r not in tgt.exigences:
                            tgt.exigences.append(r)
    else:
        draft = base

    draft = apply_answers(draft, answers, impacts, probas)
    questions = clarifying_questions(draft, answers, impacts, probas)
    return {
        "draft": draft.model_dump(),
        "questions": questions,
        "used_llm": used,
        "sources": list(texts.keys()) + (["tickets"] if tickets else []),
    }
