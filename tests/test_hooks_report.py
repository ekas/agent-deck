"""report-Hook: letzte User-Frage aus dem stdin, und welche Notification eine
echte Rueckfrage ist (der Hook feuert fuer acht Faelle, nur drei zaehlen).
"""

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from deck.claude.hooks import report


def test_prompt_of():
    assert report._prompt_of({"prompt": "  Hallo Welt  "}) == "Hallo Welt"   # getrimmt
    assert report._prompt_of({"prompt": "   "}) is None                      # nur Whitespace
    assert report._prompt_of({}) is None                                     # kein Feld
    assert report._prompt_of({"prompt": 123}) is None                        # kein String
    long = "x" * 600
    got = report._prompt_of({"prompt": long})
    assert got.endswith("…") and len(got) == 501                             # gekuerzt auf 500 + …


# ── report: Notification -> echte Rueckfrage vs. blosser Leerlauf ─────────
def test_is_real_query():
    # notification_type ist das verlaessliche Kriterium.
    assert report._is_real_query({"notification_type": "permission_prompt"}) is True
    assert report._is_real_query({"notification_type": "elicitation_dialog"}) is True
    assert report._is_real_query({"notification_type": "agent_needs_input"}) is True
    assert report._is_real_query({"notification_type": "idle_prompt"}) is False   # nur Leerlauf
    assert report._is_real_query({"notification_type": "agent_completed"}) is False
    assert report._is_real_query({"notification_type": "auth_success"}) is False
    # Fallback ohne notification_type (aeltere Version): Permission-Meldung erkennen.
    assert report._is_real_query({"message": "Bash: Allow this command?"}) is True
    assert report._is_real_query({"message": "Claude needs your permission to use Bash"}) is True
    assert report._is_real_query({"message": "Claude is waiting for your input"}) is False
    assert report._is_real_query({}) is False                                    # nichts -> keine Rueckfrage
