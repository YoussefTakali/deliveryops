"""Document ingestion and retrieval (RAG) scoped per perimeter.

Retrieval is a hybrid of three signals so it "understands" a question instead of matching raw
keywords:
  - dense similarity — a real multilingual embedding model when EMBEDDING_PROVIDER is `api`
    (OpenAI-compatible /embeddings) or `fastembed`, so paraphrases and synonyms match; a cheap
    feature-hashing fallback (`hash`) keeps everything working fully offline.
  - lexical relevance — an IDF-weighted term overlap (BM25-style) that stays an *absolute* score,
    so a query with no real match ranks low instead of being force-normalised to the top. This is
    what makes exact app names, ticket refs and codes rank first.
  - boosts — exact phrase, filename and full-coverage bonuses.
An optional LLM reranker (search(..., rerank=True)) reorders the top candidates by real relevance;
it is the strongest "does it actually understand" signal and is used by the interactive endpoints.
"""

import hashlib
import io
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document

log = logging.getLogger(__name__)
settings = get_settings()

_WORD = re.compile(r"[a-zà-ÿ0-9]{2,}", re.IGNORECASE)
# Short but meaningful tokens we keep despite the 2-char floor (codes, refs): handled by _WORD (>=2).
_STOP = set(
    "les des une pour par dans avec sur est sont qui que aux ces cette son ses leur leurs pas plus "
    "the and for with this that are from have not has was mais ont nos vos par ce cet la le un de du "
    "en au et il elle ils elles vous nous".split()
)
# Light domain + generic synonym expansion so a question and a document that say the same thing with
# different words still meet. Keys and values are already accent-stripped, lowercased tokens.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "incident": ("ticket", "anomalie", "bug", "panne"),
    "anomalie": ("incident", "bug", "defaut"),
    "bug": ("incident", "anomalie", "defaut"),
    "sla": ("engagement", "delai", "gtr", "gti"),
    "delai": ("sla", "gtr", "gti"),
    "facturation": ("factur", "billing", "cout", "budget"),
    "cout": ("budget", "facturation", "prix", "tarif"),
    "livraison": ("release", "deploiement", "mise en production", "mep"),
    "release": ("livraison", "deploiement", "mep", "version"),
    "deploiement": ("release", "livraison", "mep"),
    "mep": ("release", "livraison", "deploiement"),
    "reunion": ("meeting", "comite", "cr", "compte rendu"),
    "compte": ("cr", "reunion", "minutes"),
    "procedure": ("processus", "process", "mode operatoire", "runbook"),
    "exploitation": ("run", "ops", "production", "prod"),
    "application": ("app", "logiciel", "outil", "systeme"),
    "utilisateur": ("user", "client", "usager"),
    "equipe": ("team", "ressource", "collaborateur"),
    "risque": ("risk", "alerte", "danger"),
    "contrat": ("contract", "engagement", "accord"),
    "perimetre": ("scope", "projet", "project"),
}


# ---------- parsing ----------

def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(f"[page {i + 1}]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages))
    if name.endswith(".docx"):
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        # Walk the body in order (paragraphs and tables interleaved) and emit markdown: heading levels are kept
        # ("Heading 2" → "## "), tables become "| a | b |" rows — the shape the deliverable extractors parse.
        document = docx.Document(io.BytesIO(data))
        lines = []
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(child, document)
                text = p.text.strip()
                if not text:
                    continue
                style = (p.style.name if p.style is not None else "") or ""
                style = style.lower()
                if style.startswith("heading"):
                    level = int("".join(ch for ch in style if ch.isdigit()) or 1)
                    lines.append("#" * max(1, min(level, 6)) + " " + text)
                elif style == "title":
                    lines.append("# " + text)
                elif "list" in style:
                    lines.append("- " + text)
                else:
                    lines.append(text)
            elif tag == "tbl":
                for i, row in enumerate(Table(child, document).rows):
                    cells = [" ".join(c.text.split()) for c in row.cells]
                    lines.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        lines.append("|" + " --- |" * len(cells))
                lines.append("")
        return "\n".join(lines)
    if name.endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"# {ws.title}")
            for row in ws.iter_rows(values_only=True):
                if any(c is not None for c in row):
                    lines.append(" | ".join("" if c is None else str(c) for c in row))
        return "\n".join(lines)
    if name.endswith(".pptx"):
        from pptx import Presentation

        deck = Presentation(io.BytesIO(data))
        lines = []
        for i, slide in enumerate(deck.slides, 1):
            lines.append(f"# Slide {i}")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    lines += [p.text for p in shape.text_frame.paragraphs if p.text.strip()]
                elif getattr(shape, "has_table", False) and shape.has_table:
                    lines += [" | ".join(c.text for c in row.cells) for row in shape.table.rows]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                lines.append("Notes : " + slide.notes_slide.notes_text_frame.text.strip())
        return "\n".join(lines)
    if name.endswith((".doc", ".ppt", ".xls")):
        raise ValueError("ancien format Office non pris en charge : enregistrez-le en .docx, .pptx ou .xlsx")
    if b"\x00" in data[:8192]:
        # a binary file (zip, image, executable…) decoded as text is garbage — and PostgreSQL refuses NUL bytes
        raise ValueError("format non pris en charge (fichier binaire) : utilisez PDF, Word, PowerPoint, Excel ou un fichier texte")
    return data.decode("utf-8", errors="replace")


def chunk_text(content: str, max_chars: int = 1200) -> list[tuple[str, str]]:
    """Split on markdown-style headings, then pack paragraphs. Returns (section, text)."""
    chunks: list[tuple[str, str]] = []
    section = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append((section, body))
        buf.clear()

    for para in re.split(r"\n\s*\n|\n(?=#)", content):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            flush()
            section = para.lstrip("# ").split("\n")[0][:300]
            rest = "\n".join(para.split("\n")[1:]).strip()
            if rest:
                buf.append(rest)
            continue
        if sum(len(b) for b in buf) + len(para) > max_chars:
            flush()
        buf.append(para)
    flush()
    return chunks


# ---------- tokenisation ----------

def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return [t for w in _WORD.findall(_strip_accents(text).lower()) if (t := w) not in _STOP]


def _expand(qtokens: list[str]) -> dict[str, float]:
    """Query terms plus lighter-weighted synonyms, so a paraphrase still overlaps the source text.
    Returns {token: weight}; original terms weigh 1.0, expansions 0.5 (and never override an original)."""
    weights: dict[str, float] = {}
    for t in qtokens:
        weights[t] = 1.0
    for t in qtokens:
        for syn in _SYNONYMS.get(t, ()):  # a synonym may itself be a multi-word hint; take its tokens
            for s in tokens(syn):
                weights.setdefault(s, 0.5)
    return weights


# ---------- embeddings ----------

def _hash_embed(text: str) -> list[float]:
    """Feature-hashing bag of words (unigrams + bigrams). Offline and deterministic; a weak semantic
    signal, so the lexical component below carries most of the weight when this provider is used."""
    vec = [0.0] * settings.embedding_dim
    toks = tokens(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % settings.embedding_dim] += 1.0 if (h >> 64) % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


_fastembed_model = None


def _get_embed_client():
    """OpenAI-compatible client for the embeddings endpoint (the org-wide default LLM endpoint / key,
    as set in the admin UI or .env)."""
    from app import llm_config

    return llm_config.client_for(llm_config.resolve()).with_options(timeout=settings.embedding_api_timeout_s, max_retries=1)


def _api_embed(texts: list[str], kind: str) -> list[list[float]]:
    """Real embeddings via an OpenAI-compatible /embeddings endpoint (NVIDIA NIM etc.). NVIDIA models
    want an input_type (query vs passage) and a truncation policy, passed via extra_body — harmlessly
    ignored by plain OpenAI endpoints. Falls back to hash embeddings on any failure so search never
    hard-fails on a missing/limited embedding model, and so a slow endpoint can't hang a query."""
    input_type = "query" if kind == "query" else "passage"
    timeout = settings.embedding_api_query_timeout_s if kind == "query" else settings.embedding_api_timeout_s
    client = _get_embed_client().with_options(timeout=timeout)
    out: list[list[float]] = []
    batch = max(1, settings.embedding_api_batch)
    for i in range(0, len(texts), batch):
        window = [t[:8000] or " " for t in texts[i : i + batch]]
        try:
            resp = client.embeddings.create(
                model=settings.embedding_api_model, input=window,
                extra_body={"input_type": input_type, "truncate": "END"},
            )
            out.extend([list(map(float, d.embedding)) for d in resp.data])
        except Exception as exc:  # noqa: BLE001 — endpoint may 404/400/429/timeout; degrade, don't crash
            log.warning("API embeddings failed (%s), falling back to hash for this batch", type(exc).__name__)
            out.extend(_hash_embed(t) for t in window)
    return out


def embed(texts: list[str], kind: str = "passage") -> list[list[float]]:
    """Embed a batch. `kind` ("query"/"passage") only matters for the `api` provider."""
    global _fastembed_model
    if not texts:
        return []
    provider = settings.embedding_provider
    if provider == "api":
        from app import llm_config

        if llm_config.resolve().api_key:
            return _api_embed(texts, kind)
    if provider == "fastembed":
        from fastembed import TextEmbedding

        if _fastembed_model is None:
            # Cache under the persisted, user-writable HF volume (FASTEMBED_CACHE_PATH) so the ~450MB
            # model survives image rebuilds and isn't re-downloaded on every deploy. Defaults to
            # fastembed's own location when the env is unset (local dev).
            _fastembed_model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                                             cache_dir=os.environ.get("FASTEMBED_CACHE_PATH") or None)
        return [list(map(float, v)) for v in _fastembed_model.embed(texts)]
    return [_hash_embed(t) for t in texts]


def warm_up() -> None:
    """Load the local embedding model in the background at startup (like stt.warm_up), so the first
    search doesn't pay the one-time model load. No-op for the hash/api providers."""
    if settings.embedding_provider != "fastembed":
        return

    def _load():
        try:
            embed(["warm up"], kind="passage")
            log.info("fastembed model ready")
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding warm-up failed: %s", exc)

    threading.Thread(target=_load, name="embed-warmup", daemon=True).start()


@lru_cache(maxsize=1024)
def _query_vector(text: str) -> tuple[float, ...]:
    """Cached single-query embedding. The `api` provider can be slow on free tiers; a repeated query
    (e.g. the live copilot re-suggesting the same turn) then costs nothing."""
    return tuple(embed([text], kind="query")[0])


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. `a` is assumed unit-normalised (query vector); `b` may not be (API models
    don't always return normalised vectors), so normalise on the fly."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        nb += y * y
    return dot / math.sqrt(nb) if nb else 0.0


# ---------- ingestion ----------

def ingest_document(db: Session, perimeter_id: str, filename: str, doc_type: str, content: str) -> Document:
    content = (content or "").replace("\x00", "")          # PostgreSQL text columns reject NUL bytes
    doc = Document(perimeter_id=perimeter_id, filename=filename, doc_type=doc_type, content=content)
    db.add(doc)
    db.flush()
    pieces = chunk_text(content)
    vectors = embed([f"{s}\n{t}" for s, t in pieces], kind="passage") if pieces else []
    for i, ((section, body), vec) in enumerate(zip(pieces, vectors)):
        db.add(Chunk(document_id=doc.id, perimeter_id=perimeter_id, idx=i, section=section, text=body, embedding=vec))
    db.flush()
    return doc


def reindex(db: Session, perimeter_id: str | None = None, batch: int = 64) -> int:
    """Recompute every chunk's embedding with the current provider. Run this after switching
    EMBEDDING_PROVIDER (e.g. hash -> api) so the stored vectors match what search() now produces.
    Returns the number of chunks re-embedded."""
    stmt = select(Chunk)
    if perimeter_id:
        stmt = stmt.where(Chunk.perimeter_id == perimeter_id)
    chunks = list(db.scalars(stmt))
    for i in range(0, len(chunks), batch):
        window = chunks[i : i + batch]
        vectors = embed([f"{c.section}\n{c.text}" for c in window], kind="passage")
        for c, vec in zip(window, vectors):
            c.embedding = vec
        db.flush()
    db.commit()
    log.info("reindexed %d chunks (provider=%s)", len(chunks), settings.embedding_provider)
    return len(chunks)


# ---------- search ----------

@dataclass
class Hit:
    chunk_id: str
    document_id: str
    filename: str
    doc_type: str
    section: str
    text: str
    score: float
    perimeter_id: str = ""      # which périmètre this hit came from (matters for project-wide search)

    def cite(self) -> str:
        return f"[{self.filename} § {self.section or 'intro'}]"

    def as_source(self) -> dict:
        return {"type": "document", "ref": self.chunk_id, "label": self.cite(), "excerpt": self.text[:300]}


def _candidates(db: Session, perimeter_ids: list[str], qvec: list[float] | None, doc_types: list[str] | None, limit: int,
                q_tokens: list[str] | None = None):
    base = select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(Chunk.perimeter_id.in_(perimeter_ids))
    if doc_types:
        base = base.where(Document.doc_type.in_(doc_types))
    if not settings.is_postgres:
        return db.execute(base).all()  # SQLite: small corpora, score everything
    from sqlalchemy import or_

    if qvec is not None:
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import literal

        # pgvector pre-selects nearest candidates by the dense vector; the hybrid score below re-ranks.
        stmt = base.order_by(Chunk.embedding.op("<=>")(literal(qvec, Vector(settings.embedding_dim)))).limit(limit)
    else:  # lexical-only: no query vector to sort by, cap the scan instead
        stmt = base.limit(max(limit, 400))
    rows = db.execute(stmt).all()
    # Vector pre-selection can drop the one chunk that literally contains a rare query word; pull in
    # literal matches too so "search by word" is never at the mercy of embedding similarity.
    words = [t for t in (q_tokens or []) if len(t) >= 3][:6]
    if words:
        seen = {c.id for c, _ in rows}
        lit = base.where(or_(*[Chunk.text.ilike(f"%{w}%") for w in words])).limit(150)
        rows += [r for r in db.execute(lit).all() if r[0].id not in seen]
    return rows


# Weights depend on how trustworthy the dense signal is: real embeddings lead, hash embeddings defer
# to the lexical score (which is a genuine semantic-ish match, unlike hashed bag-of-words).
_WEIGHTS = {"api": (0.68, 0.32), "fastembed": (0.62, 0.38), "hash": (0.30, 0.70)}


def search(db: Session, perimeter_id: "str | list[str]", query: str, k: int = 5, doc_types: list[str] | None = None,
           *, rerank: bool = False, dense: bool = True) -> list[Hit]:
    """Hybrid retrieval. `perimeter_id` may be a single id or a list — pass the whole project's
    périmètres to search project-wide (the shared 'delivery brain'). `dense=False` skips the embedding
    call entirely and ranks lexically only — used by the live call copilot, where a slow embeddings API
    would stall every turn; the IDF-weighted lexical score is fast and still good for relevant turns."""
    query = (query or "").strip()
    if not query:
        return []
    perimeter_ids = [perimeter_id] if isinstance(perimeter_id, str) else list(perimeter_id)
    qvec = list(_query_vector(query)) if dense else None
    q_tokens = tokens(query)
    q_weights = _expand(q_tokens)
    q_norm = " ".join(tokens(query))  # accent-stripped phrase for the exact-phrase boost

    rows = _candidates(db, perimeter_ids, qvec, doc_types, limit=max(48, k * 10), q_tokens=q_tokens)
    if not rows:
        return []

    # Document frequencies over the candidate set → IDF (rare, specific terms matter most).
    per_chunk_tokens: list[set[str]] = []
    df: dict[str, int] = {}
    for chunk, _doc in rows:
        toks = set(tokens(chunk.section + " " + chunk.text))
        per_chunk_tokens.append(toks)
        for t in q_weights:
            if t in toks:
                df[t] = df.get(t, 0) + 1
    n = len(rows)
    idf = {t: math.log(1 + (n + 0.5) / (df.get(t, 0) + 0.5)) for t in q_weights}
    total_idf_mass = sum(idf[t] * w for t, w in q_weights.items()) or 1.0
    w_dense, w_lex = _WEIGHTS.get(settings.embedding_provider, _WEIGHTS["hash"]) if qvec is not None else (0.0, 1.0)

    hits: list[Hit] = []
    for (chunk, doc), ctoks in zip(rows, per_chunk_tokens):
        dense = max(0.0, cosine(qvec, chunk.embedding or [])) if qvec is not None else 0.0
        # Absolute lexical relevance: fraction of the query's IDF mass that this chunk matches, with a
        # tf saturation so one repeated word can't dominate. Stays in [0,1] regardless of other rows.
        text_l = _strip_accents(chunk.text).lower()
        matched = 0.0
        for t, w in q_weights.items():
            if t in ctoks:
                tf = text_l.count(t)
                matched += idf[t] * w * (tf / (tf + 1.2))
        lex = matched / total_idf_mass

        score = w_dense * dense + w_lex * lex
        # Boosts (small, additive): exact phrase, filename match.
        hay = _strip_accents(chunk.section + " " + chunk.text).lower()
        if len(q_tokens) >= 2 and q_norm and q_norm in hay:
            score += 0.18
        fname = _strip_accents(doc.filename).lower()
        if any(t in fname for t in q_tokens):
            score += 0.06
        # "Search by word" guarantee: a chunk that literally contains every query word must outrank
        # any chunk that only *resembles* the query. Without this, dense similarity could push a
        # fuzzy neighbour above the one document that actually says the word the user typed.
        if q_tokens and all(t in ctoks for t in q_tokens):
            score = max(score + 0.08, 0.60 + 0.30 * lex + 0.10 * dense)
        hits.append(Hit(chunk.id, doc.id, doc.filename, doc.doc_type, chunk.section, chunk.text, min(1.0, score), perimeter_id=chunk.perimeter_id))

    hits.sort(key=lambda h: h.score, reverse=True)
    if rerank and settings.use_llm and len(hits) > 1:
        hits = _llm_rerank(query, hits[: max(k * 3, 8)], k) + hits[max(k * 3, 8):]
    return [h for h in hits[:k] if h.score > 0.08]


def _llm_rerank(query: str, hits: list[Hit], k: int) -> list[Hit]:
    """Ask a fast model to score each candidate's relevance to the query (0-3). Reorders in place;
    on any failure returns the input order unchanged. Cheap: one call, capped candidate pool."""
    from pydantic import BaseModel, Field

    from app import llm

    class _Score(BaseModel):
        id: int
        relevance: int = Field(ge=0, le=3)

    class _Ranked(BaseModel):
        scores: list[_Score] = Field(default_factory=list)

    catalogue = "\n".join(f"[{i}] {h.cite()}\n{h.text[:500]}" for i, h in enumerate(hits))
    result, used = llm.structured(
        schema=_Ranked, tier="fast", thinking=False, max_tokens=512,
        system=("Tu es un moteur de reclassement de recherche documentaire. Pour chaque extrait, note sa "
                "pertinence pour répondre à la question : 3 = répond directement, 2 = utile, 1 = vaguement "
                "lié, 0 = hors sujet. Ne juge que la pertinence sémantique, pas la présence de mots-clés."),
        context=f"Question : « {query} »\n\nExtraits :\n{catalogue}",
        task="Renvoie un score de pertinence (0-3) pour chaque extrait par son id.",
        fallback=lambda: _Ranked(),
    )
    if not used or not result.scores:
        return hits
    by_id = {s.id: s.relevance for s in result.scores}
    order = sorted(range(len(hits)), key=lambda i: (by_id.get(i, 0), hits[i].score), reverse=True)
    ranked = []
    for rank, i in enumerate(order):
        h = hits[i]
        rel = by_id.get(i, 0)
        # Blend the LLM judgement into the score so the caller's threshold/strong-hit gates still work.
        ranked.append(Hit(h.chunk_id, h.document_id, h.filename, h.doc_type, h.section, h.text,
                          min(1.0, 0.5 * h.score + 0.5 * (rel / 3.0))))
    return ranked


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"<source id=\"{h.chunk_id}\" cite=\"{h.cite()}\">\n{h.text}\n</source>" for h in hits)
