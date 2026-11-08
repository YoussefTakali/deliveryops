"""Contract rules extracted from a document must land on the platform's codes (profiles, UO, priorities)."""
from app.contract_extraction import ContractRules, Rate, SlaRule, WorkUnit, currency_code, profile_code, to_contract


def test_profile_labels_become_codes():
    assert profile_code("Confirmé") == "confirmed"
    assert profile_code(" Sénior ") == "senior"
    assert profile_code("Architecte") == "architect"
    assert profile_code("junior") == "junior"
    assert profile_code("Data engineer") == "data_engineer"     # unknown labels keep a stable slug
    assert currency_code("€") == "EUR" and currency_code("") == "EUR" and currency_code("chf") == "CHF"


def test_to_contract_normalises_keys_and_keeps_base_settings():
    rules = ContractRules(currency="€", run_fixed_monthly_fee=21500, hours_per_day=7,
                          rates=[Rate(profile="Junior", daily_rate=430), Rate(profile="Confirmé", daily_rate=580), Rate(profile="Architecte", daily_rate=880)],
                          work_units=[WorkUnit(code="uo-s", label="Petite évolution", price=1250, max_h=14)],
                          monthly_cap_time_material=15000, sla=[SlaRule(priority="p2", resolution_h=8, target_pct=95)],
                          penalty_per_point_below_target_pct=1, penalty_cap_pct=10, out_of_scope=["migration"], clauses=[])
    base = {"billable_activity_codes": {"RUN": "fixed"}, "scope": {"in_scope": ["run"]}}
    c = to_contract(rules, base)
    assert c["currency"] == "EUR"
    assert c["rate_card"] == {"junior": 430, "confirmed": 580, "architect": 880}
    assert "UO-S" in c["work_units"] and "P2" in c["sla"]
    assert c["billable_activity_codes"] == {"RUN": "fixed"}            # base settings survive
    assert c["scope"] == {"in_scope": ["run"], "out_of_scope": ["migration"]}
