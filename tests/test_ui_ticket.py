"""Ticket-Prompts: EINZEILIG und alle Platzhalter gefuellt.

Ein mehrzeiliger Prompt wird von der Extension als mehrere Eingaben
abgeschickt - der Agent bekommt dann Bruchstuecke.
"""

import helpers  # setzt sys.path und die Deck-Sprache

from deck.domain import config as cfg


# Der Text geht per sendText(execute=True) in den pty; ein \n wuerde ihn zerreissen,
# ein uebrig gebliebenes {…} wuerde der Agent woertlich sehen.
def _assert_clean_prompt(p, must_contain):
    assert "\n" not in p and "\r" not in p, "Prompt ist mehrzeilig"
    assert "{" not in p and "}" not in p, "unaufgeloester Platzhalter"
    for s in must_contain:
        assert s in p, f"fehlt im Prompt: {s}"


def test_ticket_prompt_single_line_and_filled():
    wt = "C:/Users/x/AppData/Local/claude-agent-deck/state/A1.worktree"
    p = cfg.TICKET_PROMPT.format(ticket="ABC-123", jira_key="ABC-123",
                                 branch="ticket/abc-123", slug="abc-123",
                                 wt_marker=wt, task="mach was")
    # jira_key + Jira-Lookup/Kurzvorstellung muessen drinstehen.
    _assert_clean_prompt(p, ["ABC-123", "ticket/abc-123", wt, "mach was", "Jira"])


def test_ticket_search_prompt_single_line_and_filled():
    marker = "C:/x/state/A1.ticket"
    wt = "C:/x/state/A1.worktree"
    s = cfg.TICKET_SEARCH_PROMPT.format(prefix="ticket/", marker=marker,
                                        wt_marker=wt, task="mach was")
    _assert_clean_prompt(s, ["ticket/", marker, wt, "mach was", "Jira"])
