"""Contract rule extraction: the LLM turns a contract document into structured billing/SLA rules,
a human validates them (recommendation kind=contract_rules), then only code applies them."""

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import knowledge, llm
from app.agents.base import AgentContext, recommend
from app.models import Document, Perimeter


class Rate(BaseModel):
    profile: str = Field(description="junior, confirmed, senior, architect...")
    daily_rate: float


class WorkUnit(BaseModel):
    code: str = Field(description="e.g. UO-S")
    label: str
    price: float
    max_h: float


class SlaRule(BaseModel):
    priority: str = Field(description="P1..P4")
    resolution_h: float
    target_pct: float


class ContractRules(BaseModel):
    currency: str
    run_fixed_monthly_fee: float
    hours_per_day: float
    rates: list[Rate]
    work_units: list[WorkUnit]
    monthly_cap_time_material: float
    sla: list[SlaRule]
    penalty_per_point_below_target_pct: float
    penalty_cap_pct: float
    out_of_scope: list[str] = Field(description="activities explicitly excluded from the contract scope")
    clauses: list[str] = Field(description="short quotes of the clauses each rule was taken from")


# Contracts name profiles in their own words ("Confirmé", "Sénior", "Architecte") while team members carry the
# platform's codes (junior / confirmed / senior / architect): the rate card must use the codes, or nobody's day
# rate would ever be found at pre-billing time.
PROFILE_CODES = {
    "junior": "junior", "débutant": "junior", "debutant": "junior",
    "confirmé": "confirmed", "confirme": "confirmed", "confirmed": "confirmed", "intermédiaire": "confirmed", "intermediaire": "confirmed",
    "senior": "senior", "sénior": "senior",
    "architecte": "architect", "architect": "architect",
    "expert": "expert", "lead": "lead", "chef de projet": "manager", "manager": "manager",
}


CURRENCY_CODES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "chf": "CHF"}


def currency_code(label: str) -> str:
    key = str(label or "").strip().lower()
    return CURRENCY_CODES.get(key, key.upper() or "EUR")


def profile_code(label: str) -> str:
    key = " ".join(str(label or "").strip().lower().split())
    return PROFILE_CODES.get(key, key.replace(" ", "_"))


def to_contract(rules: ContractRules, base: dict) -> dict:
    return {
        **base,
        "currency": currency_code(rules.currency),
        "run_fixed_monthly_fee": rules.run_fixed_monthly_fee,
        "hours_per_day": rules.hours_per_day,
        "rate_card": {profile_code(r.profile): r.daily_rate for r in rules.rates},
        "work_units": {w.code.strip().upper(): {"label": w.label, "price": w.price, "max_h": w.max_h} for w in rules.work_units},
        "monthly_cap_time_material": rules.monthly_cap_time_material,
        "sla": {s.priority.strip().upper(): {"resolution_h": s.resolution_h, "target_pct": s.target_pct} for s in rules.sla},
        "penalties": {"per_point_below_target_pct": rules.penalty_per_point_below_target_pct, "cap_pct": rules.penalty_cap_pct},
        "scope": {**base.get("scope", {}), "out_of_scope": rules.out_of_scope},
    }


def extract(db: Session, perimeter: Perimeter, run_by: str):
    docs = db.scalars(select(Document).where(Document.perimeter_id == perimeter.id, Document.doc_type == "contract")).all()
    if not docs:
        return None
    content = "\n\n".join(f"<document name=\"{d.filename}\">\n{d.content}\n</document>" for d in docs)
    c = perimeter.contract

    def fallback() -> ContractRules:  # offline: echo current rules so the validation flow can still be demoed
        return ContractRules(
            currency=c.get("currency", "EUR"), run_fixed_monthly_fee=c.get("run_fixed_monthly_fee", 0), hours_per_day=c.get("hours_per_day", 7),
            rates=[Rate(profile=k, daily_rate=v) for k, v in c.get("rate_card", {}).items()],
            work_units=[WorkUnit(code=k, **v) for k, v in c.get("work_units", {}).items()],
            monthly_cap_time_material=c.get("monthly_cap_time_material", 0),
            sla=[SlaRule(priority=k, resolution_h=v["resolution_h"], target_pct=v["target_pct"]) for k, v in c.get("sla", {}).items()],
            penalty_per_point_below_target_pct=c.get("penalties", {}).get("per_point_below_target_pct", 0), penalty_cap_pct=c.get("penalties", {}).get("cap_pct", 0),
            out_of_scope=c.get("scope", {}).get("out_of_scope", []), clauses=["(mode hors ligne : règles actuelles reprises à l'identique)"],
        )

    rules, used = llm.structured(schema=ContractRules, context=content, task="Extract the billing and service-level rules from this AMS contract. Use 0 when a rule is absent. Quote the clause for each rule.", fallback=fallback, effort="high")
    ctx = AgentContext(db=db, perimeter_id=perimeter.id, run_id=None, params={})
    rec = recommend(ctx, "billing", "contract_rules", f"Valider les règles contractuelles extraites — {perimeter.name}", "\n".join(f"• {q}" for q in rules.clauses[:12]), impact="high", confidence=0.8 if used else 0.4,
                    sources=[{"type": "document", "ref": d.id, "label": d.filename, "excerpt": ""} for d in docs], payload={"contract": to_contract(rules, c)})
    db.commit()
    return rec
