"""Hover-Tooltip und Karten-Label: erkanntes Ticket und PR.
"""

import os

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

                # sys.path und nagelt die Deck-Sprache auf Deutsch.


def _tip_deck(cached=None, cached_summary=None, auto=None, bindings=None,
              worktrees=None):
    """Fake-Self mit den echten (ungebundenen) Tooltip-Methoden; chat_summary wird
    gemockt, damit kein Transcript/Cache auf der Platte noetig ist. bindings/worktrees
    speisen die Herkunftszeile (Repo · Fenster · Slot, siehe _origin_lines)."""
    from deck.ui import panel as ad
    f = type("F", (), {})()
    f._auto_refs = dict(auto or {})
    f.bindings = dict(bindings or {})
    f._worktrees = dict(worktrees or {})
    f._tip_refs = ad.AgentDeck._tip_refs.__get__(f)
    f._refs_label = ad.AgentDeck._refs_label
    f._origin_lines = ad.AgentDeck._origin_lines.__get__(f)
    f._tip_text = ad.AgentDeck._tip_text.__get__(f)
    orig = (ad.cs.cached_refs, ad.cs.cached_summary)
    ad.cs.cached_refs = lambda sid: dict(cached or {"ticket": "", "pr": ""})
    ad.cs.cached_summary = lambda sid: cached_summary
    return f, ad, orig


def test_tip_text_shows_detected_ticket_and_pr():
    f, ad, orig = _tip_deck(cached={"ticket": "PROJ-2691", "pr": "62"},
                            cached_summary="Bottom-Bar bauen")
    try:
        txt = f._tip_text({}, "sess-1")
        assert txt.splitlines()[0] == "Ticket: PROJ-2691 · PR #62"   # Bezug steht oben
        assert "Bottom-Bar bauen" in txt
        assert f._auto_refs["sess-1"]["pr"] == "62"    # gemerkt -> auch fuer die Karte
        # noch keine Zusammenfassung -> Bezug trotzdem sofort da, darunter der Platzhalter
        ad.cs.cached_summary = lambda sid: None
        txt = f._tip_text({}, "sess-1")
        assert txt.startswith("Ticket: PROJ-2691 · PR #62\n") and "wird erstellt" in txt
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_text_pr_only_and_none():
    f, ad, orig = _tip_deck(cached={"ticket": "", "pr": "62"}, cached_summary="Review")
    try:
        assert f._tip_text({}, "s") == "PR #62\nWorum es geht:\nReview"
        # gar kein Bezug -> exakt wie vorher, nur die Zusammenfassung
        f._auto_refs.clear()
        ad.cs.cached_refs = lambda sid: {"ticket": "", "pr": ""}
        assert f._tip_text({}, "s") == "Worum es geht:\nReview"
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_text_names_repo_window_and_slot():
    """Die Herkunft steht GANZ oben – sie ist beim Hovern die erste Frage, wenn mehrere
    Repos offen sind. Ohne Slot (Aufruf ohne Kachelbezug) bleibt der Text wie zuvor."""
    wt = os.path.join("C:", os.sep, "code", "agent-deck.wt", "abc-2691")
    f, ad, orig = _tip_deck(cached={"ticket": "", "pr": ""}, cached_summary="Review",
                            bindings={"A": "agent-deck"}, worktrees={"A2": wt})
    try:
        lines = f._tip_text({}, "s", "A2").splitlines()
        assert lines[0] == "agent-deck · Fenster A · A2"
        assert lines[1] == "↳ wt/abc-2691"       # der Agent sitzt NEBEN dem Repo
        # ohne worktree faellt die zweite Zeile weg
        assert f._tip_text({}, "s", "A1").splitlines()[1] == "Worum es geht:"
        # ohne gebundenes Repo bleibt nur der Fensterbuchstabe
        assert f._tip_text({}, "s", "B1").splitlines()[0] == "Fenster B · B1"
        # ohne Slot exakt wie vorher (kein leerer Kopf, keine Trennzeile)
        assert f._tip_text({}, "s") == "Worum es geht:\nReview"
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_refs_prefers_memory_over_cache_file():
    f, ad, orig = _tip_deck(cached={"ticket": "ALT-1", "pr": ""},
                            auto={"sess-3": {"ticket": "NEU-2", "pr": ""}})
    try:
        assert f._tip_refs("sess-3")["ticket"] == "NEU-2"  # frisch gescannt > Cache-Datei
        assert f._tip_refs("")["ticket"] == ""
        # Gepatcht wird in hover, NICHT in panel: _tip_refs lebt im HoverMixin und
        # liest TICKET_AUTO aus dem Namensraum von deck/ui/hover.py. panel hat eine
        # eigene Bindung desselben Werts - ein Patch dort bliebe wirkungslos, und der
        # Test waere still gruen, ohne den Aus-Fall zu pruefen.
        from deck.ui import hover
        prev, hover.TICKET_AUTO = hover.TICKET_AUTO, False
        try:
            assert f._tip_refs("sess-3") == {"ticket": "", "pr": ""}   # Erkennung aus
        finally:
            hover.TICKET_AUTO = prev
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_refs_card_label_fits_the_narrow_line():
    from deck.ui import panel as ad
    L = ad.AgentDeck._refs_card_label
    assert L({"ticket": "PROJ-2691", "pr": "62"}) == "PROJ-2691 #62"   # beides, 13 Z.
    assert L({"ticket": "", "pr": "62"}) == "#62"
    assert L({"ticket": "PROJ-2691", "pr": ""}) == "PROJ-2691"
    assert L(None) == "" and L({}) == ""
    # zu lang fuer beides -> das Ticket gewinnt (dauerhafter als der PR)
    assert L({"ticket": "LONGPROJ-12345", "pr": "62"}) == "LONGPROJ-12345"
    # nur ein (zu langer) PR -> hart gekuerzt statt ueber das Effort zu laufen
    assert L({"ticket": "", "pr": "1234567890123456"}, max_chars=8) == "#123456…"
