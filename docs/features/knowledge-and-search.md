# Feature — Knowledge & search (RAG)

Retrieval‑Augmented Generation over the user's documents and Microsoft/local data, with citations.
Runs fully offline by default.

- Backend: `backend/app/knowledge.py` (parse, chunk, embed, search, format context),
  `backend/app/reindex.py` (recompute all embeddings), `backend/app/importer.py` (imports).
- Tables: `Document`, `Chunk` (pgvector).
- Config: `EMBEDDING_*`, `KNOWLEDGE_*` in `config.py`.

## Pipeline
1. **Parse**: PDF / DOCX / XLSX / Markdown → text.
2. **Chunk**: split into overlapping passages, one embedding per chunk.
3. **Embed**: provider is `EMBEDDING_PROVIDER`:
   - `hash` — deterministic offline default (no semantics, but always works),
   - `fastembed` — local ONNX sentence‑transformer (real semantics, ~ms, the recommended default),
   - `api` — an OpenAI‑compatible embeddings endpoint (best quality; needs a key).
   `EMBEDDING_DIM` must match the model; changing it requires `python -m app.reindex`.
4. **Hybrid search**: IDF‑weighted lexical + dense cosine + boosts, with an **exact‑word guarantee**
   (a word that appears in a document is never missed) and an optional LLM rerank (`KNOWLEDGE_RERANK`).
5. **Citations**: results carry a source label + excerpt; `format_context` builds the LLM context and
   answers cite `[source]`.

## Where it's used
- The delivery **Copilot** and per‑perimeter knowledge tab.
- The **companion** chat (search over the user's mails/files), and its `context_help` nudge.
- Onboarding knowledge cards (approved cards are indexed back into the base).

## Operational notes
- Reindex after changing the embedding model/dim, or after a large import.
- The `api` provider can be slow/cold; query embedding has a tight timeout and falls back so
  interactive search stays responsive (passage embedding waits longer for correctness).
- Microsoft data can be searched live (with a Graph token) in addition to indexed content.
