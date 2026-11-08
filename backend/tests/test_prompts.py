"""System-prompt rules that change model behaviour."""
from app import companion
from app.agents import copilot
from app.models import CompanionProfile


def test_debug_rules_only_in_debug_mode():
    # Stating the [DÉBOGAGE] rule in every prompt made the model echo the tag in ordinary answers.
    prof = CompanionProfile(owner="sarah", display_name="Sarah")
    assert "débogage" not in companion.system_prompt(prof).lower()
    assert "Mode débogage" in companion.system_prompt(prof, debug=True)
    assert "recommandé" in companion.system_prompt(prof)          # the one-recommended rule always applies
    assert companion.is_debug("  [DÉBOGAGE] Erreur : 500") and not companion.is_debug("Bonjour")
    assert copilot._is_debug("[DÉBOGAGE] x") and not copilot._is_debug("x")
