"""Document Generator Agent — the user provides an *example* of the output they need (a template,
a past deliverable, a format) plus what they want, and the agent produces the same document filled
from everything it can read: the perimeter knowledge base, and (when connected) the user's Microsoft
365 and local files indexed during onboarding.

Deterministic layer: it gathers and cites real sources. LLM layer: it writes the document mimicking
the example's structure. The result is a *gated deliverable* — a human validates before it is used.
"""

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import knowledge, llm, skills
from app.context import scope_perimeter_ids
from app.agents.base import AgentContext, AgentOutput, recommend
from app.models import Perimeter

NAME = "doc_generator"


class GeneratedDoc(BaseModel):
    title: str
    content: str = Field(description="the full document in Markdown, mimicking the example's structure")
    used_sources: list[str] = Field(default_factory=list, description="source identifiers actually used")
    gaps: list[str] = Field(default_factory=list, description="information the document needs but that was not found")


def generate(ctx: AgentContext) -> tuple[GeneratedDoc, list, bool]:
    """Core generation, reusable by the /api doc endpoint outside a full workflow."""
    db, pid = ctx.db, ctx.perimeter_id
    perimeter = db.get(Perimeter, pid)
    p = ctx.params
    title = (p.get("title") or "Document").strip()
    instructions = (p.get("instructions") or "").strip()
    example = (p.get("example") or "").strip()
    # Pull the most relevant material for this document. Onboarding-indexed MS/local chunks live in
    # the same knowledge store, so this reaches them too when present.
    query = (p.get("sources_query") or f"{title}\n{instructions}").strip()
    hits = knowledge.search(db, scope_perimeter_ids(db, perimeter), query, k=8) if query else []
    context = knowledge.format_context(hits)

    example_block = f"\n\n<example_format>\n{example[:6000]}\n</example_format>" if example else ""

    def fallback() -> GeneratedDoc:
        body = [f"# {title}", "", f"*Périmètre : {perimeter.name}*", ""]
        if instructions:
            body += ["## Objet", instructions, ""]
        if hits:
            body += ["## Éléments issus de la base documentaire"]
            body += [f"- {h.cite()} : {h.text[:300].strip()}" for h in hits[:6]]
            body += [""]
        else:
            body += ["> Aucune source documentaire pertinente trouvée — document à compléter manuellement.", ""]
        return GeneratedDoc(title=title, content="\n".join(body),
                            used_sources=[h.chunk_id for h in hits[:6]],
                            gaps=[] if hits else ["Aucune donnée indexée ne correspond à la demande."])

    sk = skills.prompt_block(db, NAME, pid)
    doc, used = llm.structured(
        schema=GeneratedDoc,
        context=f"{sk}\nPerimeter: {perimeter.name}\n\n<knowledge>\n{context}\n</knowledge>{example_block}",
        task=(f"Rédige le document « {title} ». Demande de l'utilisateur : {instructions or 'produire le document'}. "
              "Si un <example_format> est fourni, reproduis fidèlement sa structure, ses sections et son ton, "
              "mais remplis-le avec les données réelles du périmètre issues de <knowledge> (cite les sources). "
              "Liste dans `gaps` toute information manquante que tu n'as pas pu sourcer plutôt que de l'inventer."),
        fallback=fallback, tier="reasoning", effort="high",
    )
    return doc, hits, used


def run(ctx: AgentContext) -> AgentOutput:
    doc, hits, used = generate(ctx)
    by_id = {h.chunk_id: h.as_source() for h in hits}
    sources = [by_id[i] for i in doc.used_sources if i in by_id] or [h.as_source() for h in hits[:5]]
    detail = doc.content[:400] + ("…" if len(doc.content) > 400 else "")
    if doc.gaps:
        detail += "\n\nÀ compléter : " + " ; ".join(doc.gaps)
    rec = recommend(ctx, NAME, "document", f"Document généré : {doc.title}", detail,
                    impact="high", confidence=0.7, sources=sources,
                    payload={"title": doc.title, "content": doc.content, "gaps": doc.gaps})
    return AgentOutput(summary=f"Document « {doc.title} » généré ({len(doc.content)} caractères, {len(doc.gaps)} lacune(s)).",
                       data={"title": doc.title, "content": doc.content, "gaps": doc.gaps, "sources": sources},
                       recommendations=[rec], llm_used=used)
