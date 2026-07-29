"""chat_summary: Hover-Zusammenfassung, plus Ticket- und PR-Nummer aus dem Chat.

Bei der Erkennung sind Falsch-Positive das Problem, nicht das Finden.
"""

import json

import helpers  # setzt sys.path und die Deck-Sprache

from deck import i18n
from deck.claude import summarize as cs
from deck.claude import refs


def _line(rec):
    return json.dumps(rec)


def test_extract_turns():
    lines = [
        # echte getippte User-Frage
        _line({"type": "user", "message": {"role": "user", "content": "Bau Feature X"}}),
        # Assistant: nur die Text-Bloecke zaehlen, thinking/tool_use raus
        _line({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "Mache ich."},
            {"type": "tool_use", "name": "Bash", "input": {}}]}}),
        # User-Zug, dessen content eine LISTE ist = Tool-Ergebnis -> kein echter Text
        _line({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "total 5"}]}}),
        # Slash-Kommando-/System-Einschub (beginnt mit '<') -> raus
        _line({"type": "user", "message": {"role": "user", "content": "<command-name>/x"}}),
        # Caveat-Einschub -> raus
        _line({"type": "user", "message": {"role": "user", "content": "Caveat: nope"}}),
        # System-Zeile ohne message-dict -> ignoriert
        _line({"type": "system", "subtype": "info"}),
        "   ",            # Leerzeile
        "{kaputt",        # nicht-JSON -> uebersprungen
    ]
    turns = cs.extract_turns(lines)
    assert turns == [("user", "Bau Feature X"), ("assistant", "Mache ich.")]


def test_extract_turns_accepts_dicts():
    # extract_turns darf auch schon geparste dicts bekommen (nicht nur Roh-Strings)
    turns = cs.extract_turns([
        {"type": "user", "message": {"role": "user", "content": "Hallo"}}])
    assert turns == [("user", "Hallo")]


def test_build_digest_empty():
    assert cs.build_digest([]) == ""


def test_build_digest_topic_plus_recent():
    turns = [("user", "T" + "a" * 5)] + [("user", "u%d" % i) for i in range(1, 6)] \
            + [("assistant", "letzte Antwort")]
    d = cs.build_digest(turns, max_chars=60, per_turn=600)
    lines = d.splitlines()
    assert lines[0].startswith("User: T")          # erster Zug (Thema) IMMER dabei
    assert "…" in lines                             # Luecke, weil nicht alles reinpasst
    assert lines[-1] == "Assistant: letzte Antwort"  # juengster Zug dabei


def test_build_digest_no_gap_when_all_fit():
    turns = [("user", "Thema"), ("assistant", "ok"), ("user", "weiter")]
    d = cs.build_digest(turns, max_chars=3500)
    assert "\n…\n" not in d and not d.startswith("…")  # nichts ausgelassen -> keine Luecke
    assert d.splitlines() == ["User: Thema", "Assistant: ok", "User: weiter"]


def test_build_digest_per_turn_cap():
    d = cs.build_digest([("user", "x" * 900)], per_turn=100)
    assert d.startswith("User: ") and d.endswith("…") and len(d) <= 120


def test_clean_summary():
    assert cs.clean_summary("  Bottom-Bar bauen  ") == "Bottom-Bar bauen"
    assert cs.clean_summary('"Feature X umsetzen"') == "Feature X umsetzen"   # Quotes weg
    assert cs.clean_summary("„Deutsches Zitat“") == "Deutsches Zitat"          # dt. Quotes
    assert cs.clean_summary("Zusammenfassung: Y machen") == "Y machen"         # Praefix weg
    assert cs.clean_summary("erste Zeile\nzweite") == "erste Zeile"            # nur 1. Absatz
    assert cs.clean_summary("a\n\n  b") == "a"
    assert cs.clean_summary("") == "" and cs.clean_summary(None) == ""
    long = cs.clean_summary("z" * 300, max_len=50)
    assert long.endswith("…") and len(long) == 51


def test_i18n_normalize_and_L():
    assert i18n.normalize("english") == i18n.ENGLISH
    assert i18n.normalize("EN-us") == i18n.ENGLISH
    assert i18n.normalize("german") == i18n.GERMAN
    assert i18n.normalize(None) == i18n.GERMAN          # fehlend -> Deutsch
    assert i18n.normalize("klingon") == i18n.GERMAN     # unbekannt -> Deutsch
    prev = i18n._lang
    try:
        i18n._lang = i18n.ENGLISH
        assert i18n.L("Speichern", "Save") == "Save" and i18n.is_english()
        i18n._lang = i18n.GERMAN
        assert i18n.L("Speichern", "Save") == "Speichern" and not i18n.is_english()
    finally:
        i18n._lang = prev


def test_summary_instruction_language():
    # Die Modell-Anweisung folgt der Sprache; nur "english" -> englische Fassung.
    assert cs.instruction("english") is cs._INSTRUCTION_EN
    assert cs.instruction("german") is cs._INSTRUCTION_DE
    assert cs.instruction("klingon") is cs._INSTRUCTION_DE   # Fallback Deutsch
    assert "English sentence" in cs._INSTRUCTION_EN
    assert "deutschen Satz" in cs._INSTRUCTION_DE


def test_enc_cwd():
    # Umlaute/ß werden zu je einem '-' (darum "M--ig" aus "Müßig") – genau die
    # Kodierung, die Claude Code fuer ~/.claude/projects/<ordner> verwendet.
    assert cs.enc_cwd(r"C:\Users\Max Müßig\Projekte\agent-deck") \
        == "C--Users-Max-M--ig-Projekte-agent-deck"
    assert cs.enc_cwd("") == "" and cs.enc_cwd(None) == ""


# ── chat_summary: Ticketnummer aus dem Chat lesen ────────
def test_find_ticket_plain_key():
    assert refs.find_ticket([("user", "Wir machen PROJ-2691 fertig")], "PROJ") == "PROJ-2691"
    # ohne konfiguriertes Projekt trotzdem: jeder Key in Jira-Form zaehlt
    assert refs.find_ticket([("user", "bitte ABC-123 anschauen")]) == "ABC-123"
    assert refs.find_ticket([]) == "" and refs.find_ticket("kein Ticket hier") == ""


def test_find_ticket_ignores_tech_lookalikes():
    """UTF-8 & Co. sehen aus wie ein Key, sind aber keiner – sonst steht Muell im Hover."""
    noise = "UTF-8, SHA-256, ISO-8601, RFC-2119, CVE-2021, AES-256, GPT-5, python-3, top-10"
    assert refs.find_ticket([("user", noise)], "PROJ") == ""


def test_find_ticket_project_lowercase_and_bare_number():
    # das konfigurierte Projekt wird auch klein erkannt (Branch-/Pfadnamen) …
    assert refs.find_ticket([("user", "schau in ticket/proj-2691")], "PROJ") == "PROJ-2691"
    # … und eine blosse Nummer nach 'Ticket'/'Issue' bekommt den Projekt-Praefix
    assert refs.find_ticket([("user", "Ticket 2701 bitte")], "PROJ") == "PROJ-2701"
    assert refs.find_ticket([("user", "Issue #42 ist offen")], "PROJ") == "PROJ-42"
    # ohne Projekt-Key gibt es aus einer blossen Nummer nichts zu machen
    assert refs.find_ticket([("user", "Ticket 2701 bitte")]) == ""


def test_find_ticket_context_allows_single_digit():
    # einstellige Nummer nur mit 'Ticket …' davor (sonst waere UTF-8 wieder drin)
    assert refs.find_ticket([("user", "Ticket PROJ-1 bitte")]) == "PROJ-1"
    assert refs.find_ticket([("user", "der Wert PROJ-1 steht da")]) == ""


def test_find_ticket_picks_the_one_it_is_about():
    """Haeufigkeit schlaegt Reihenfolge: ein nebenbei erwaehnter Key gewinnt nicht."""
    turns = [("user", "nebenbei ABC-12"), ("assistant", "ok"),
             ("user", "eigentlich geht es um XYZ-77"), ("assistant", "XYZ-77, verstanden")]
    assert refs.find_ticket(turns) == "XYZ-77"
    # gleiche Punktzahl -> der ZULETZT erwaehnte gewinnt (das Gespraech ist weitergezogen)
    assert refs.find_ticket([("user", "erst ABC-11"), ("user", "jetzt ABC-22")]) == "ABC-22"
    # das konfigurierte Projekt sticht einen fremden Key derselben Haeufigkeit
    assert refs.find_ticket([("user", "PROJ-2691 vs FOO-2692")], "PROJ") == "PROJ-2691"


def test_find_ticket_needs_more_than_a_side_remark():
    """Eine EINMALIGE Nebenbei-Nennung des Agenten reicht nicht (min_score): lieber
    keine ID im Hover als eine falsche. Nennt der Nutzer sie (oder faellt sie mehrfach),
    steht sie da."""
    side = [("assistant", "das behebt uebrigens ABC-99")]
    assert refs.find_ticket(side) == ""
    assert refs.find_ticket(side + [("assistant", "ABC-99 ist damit durch")]) == "ABC-99"
    assert refs.find_ticket([("user", "mach ABC-99")]) == "ABC-99"


def test_find_ticket_no_key_inside_longer_code():
    # Regel-/Normkennungen wie Dockle "CIS-DI-0006" duerfen nicht als "DI-0006" durch
    assert refs.find_ticket([("user", "Dockle meldet CIS-DI-0006"),
                           ("user", "CIS-DI-0006 behoben")]) == ""


def test_find_ticket_robust_against_junk_turns():
    assert refs.find_ticket([None, ("user",), ("user", None), 42,
                           ("user", "ABC-123")]) == "ABC-123"


# ── chat_summary: PR-Nummer aus dem Chat lesen ───────────
def test_find_pr_keyword_and_url():
    assert refs.find_pr([("user", "Fix die Bugs aus PR #62")]) == "62"
    assert refs.find_pr([("user", "siehe https://github.com/acme/webapp/pull/128")]) == "128"
    assert refs.find_pr([("user", "pull request 903 reviewen")]) == "903"
    assert refs.find_pr([("user", "merge request 12 anschauen")]) == "12"
    assert refs.find_pr([("user", "nichts davon hier")]) == "" and refs.find_pr([]) == ""


def test_find_pr_bare_hash_needs_two_mentions():
    """Ein blosses '#62' kann alles sein (Issue, Kommentar) -> erst ab der zweiten
    Nennung glauben wir es; mit 'PR' davor reicht eine."""
    once = [("user", "schau dir #62 an")]
    assert refs.find_pr(once) == ""
    assert refs.find_pr(once + [("user", "#62 ist noch offen")]) == "62"
    assert refs.find_pr([("user", "schau dir PR #62 an")]) == "62"


def test_find_pr_ignores_non_pr_hashes():
    # 'Issue #42'/'Zeile #42' ist kein Pull Request; Hex-Farben schon gar nicht
    assert refs.find_pr([("user", "Issue #42 offen"), ("user", "Issue #42 noch offen")]) == ""
    assert refs.find_pr([("user", "in Zeile #42"), ("user", "Zeile #42 nochmal")]) == ""
    assert refs.find_pr([("user", "Farbe #6289ab"), ("user", "wieder #6289ab")]) == ""


def test_find_refs_can_return_both():
    got = refs.find_refs([("user", "Bugs aus PR #62 zum Ticket PROJ-2651 fixen")], "PROJ")
    assert got == {"ticket": "PROJ-2651", "pr": "62"}
    assert refs.find_refs([("user", "nur reden")], "PROJ") == {"ticket": "", "pr": ""}
