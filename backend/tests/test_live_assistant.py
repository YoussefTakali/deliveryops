"""Live call copilot: filler never produces a fabricated question, language stays pinned."""

from app import live_assistant as la


def test_filler_is_recognised_and_real_content_is_not():
    for f in ["euh", "euuuh", "hmm", "hmmm", "bah...", "ok", "ok ok", "d'accord", "ouais", "voilà",
              "um", "uh huh", "so...", "  euh, hmm  ", "mhm", "non non"]:
        assert la.is_filler(f), f
    for real in ["quel est le budget ?", "on livre quand", "euh je pense qu'on devrait reporter la réunion",
                 "bonjour", "merci beaucoup", "le ticket INC42 est bloqué"]:
        assert not la.is_filler(real), real


def test_stream_stays_silent_on_filler_without_calling_the_llm():
    prepared = {"project_ctx": "", "sources": [], "route": "chat", "wants_web": False}
    events = list(la.stream(utterance="euh...", speaker="Interlocuteur", transcript=[], user_context=None,
                            prepared=prepared, mode="reply", language="fr"))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["noop"] is True and done["answer"] == ""
    # and no web/project sources were attached for a filler turn
    meta = [e for e in events if e["type"] == "meta"][0]
    assert meta["sources"] == []


def test_language_directive_pins_the_chosen_language():
    d = la._language_directive("fr", [], "whatever")
    assert "French" in d and "Do NOT switch" in d


def test_project_words_cover_contract_and_operations_vocabulary():
    from app.live_assistant import _PROJECT_WORDS
    for s in ["Vous pouvez le prendre dans le forfait ?", "Le flux a encore été rejeté ce matin", "Le lot de liquidation est en erreur",
              "Il faut le chiffrage avant le COPIL", "C'est hors périmètre contractuel"]:
        assert _PROJECT_WORDS.search(s), s
    assert not _PROJECT_WORDS.search("Il fait beau à Lyon aujourd'hui")
