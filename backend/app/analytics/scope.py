"""Contract scope checks (out-of-scope request detection)."""

import unicodedata


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def is_out_of_scope_text(text: str, contract: dict) -> tuple[bool, list[str]]:
    terms = contract.get("scope", {}).get("out_of_scope", [])
    body = _norm(text)
    matched = [t for t in terms if _norm(t) in body]
    return bool(matched), matched


def is_out_of_scope(ticket, contract: dict) -> tuple[bool, list[str]]:
    return is_out_of_scope_text(f"{ticket.title} {ticket.description}", contract)
