"""binding: Zuordnung Fenster <-> Repo, Platzhalter-Erkennung, Ticket-Helfer.
"""

import helpers  # setzt sys.path und die Deck-Sprache

from deck.domain import binding


def test_is_placeholder_ws():
    assert binding.is_placeholder_ws("") is True
    assert binding.is_placeholder_ws(None) is True
    assert binding.is_placeholder_ws("unknown") is True
    assert binding.is_placeholder_ws("  Unknown  ") is True        # getrimmt/case-insensitiv
    assert binding.is_placeholder_ws("myrepo") is False


def test_repo_from_title():
    assert binding.repo_from_title("main.py - myrepo - Visual Studio Code") == "myrepo"
    assert binding.repo_from_title("● app.tsx - acme-client - Visual Studio Code") == "acme-client"
    assert binding.repo_from_title("Visual Studio Code") == "Visual Studio Code"  # nur Marker -> kein Ordner davor


def test_ticket_slug():
    assert binding.ticket_slug("ABC-123") == "abc-123"
    assert binding.ticket_slug("ABC-123: Login fix") == "abc-123-login-fix"
    assert binding.ticket_slug("  #42  ") == "42"                   # getrimmt, Sonderzeichen -> weg
    assert binding.ticket_slug("a//b__c") == "a-b-c"               # Laeufe zu einem '-'
    assert binding.ticket_slug("") == ""
    assert binding.ticket_slug("---") == ""                        # nur Trenner -> leer


def test_ticket_branch():
    assert binding.ticket_branch("ABC-123") == "ticket/abc-123"
    assert binding.ticket_branch("ABC-123", prefix="feat/") == "feat/abc-123"
    assert binding.ticket_branch("") == ""                          # leerer Slug -> kein Branch
    assert binding.ticket_branch("!!!") == ""


def test_jira_key():
    assert binding.jira_key("2701", project="PROJ") == "PROJ-2701"   # nur Nummer -> Praefix davor
    assert binding.jira_key("  #42 ", project="PROJ") == "PROJ-42"   # getrimmt, '#' weg
    assert binding.jira_key("PROJ-2701", project="PROJ") == "PROJ-2701"  # schon ein Key -> so lassen
    assert binding.jira_key("abc-123", project="PROJ") == "ABC-123"  # Key gewinnt, Projektteil gross
    assert binding.jira_key("2701", project="") == "2701"            # kein Projekt -> Nummer roh
    assert binding.jira_key("", project="PROJ") == ""                # leer -> leer
