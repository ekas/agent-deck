"""Haltefrist nach reveal_for_request: ein Zweitstart klappt das eingeklappte
Deck auf und haelt es kurz offen.
"""

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from deck.dock import controller as ed
from deck.dock import metrics as dockm


def _dock_poll(now_ms, hold_until, pointer_inside=False):
    """EdgeDock ohne Tk, aufgeklappt, fuer _poll_once. Der Zeiger-Stub fliegt auf,
    wenn er waehrend der Haltefrist ueberhaupt befragt wird."""
    class _Pointer:
        def __init__(self, allowed):
            self.allowed = allowed

        def _p(self):
            if not self.allowed:
                raise AssertionError("Zeiger darf in der Haltefrist nicht zaehlen")
            return 0
        winfo_pointerx = winfo_pointery = _p

    d = object.__new__(ed.EdgeDock)
    d.edge = "right"
    d.expanded = True
    d._anim = None
    d._outside_since = 111          # "Zeiger war schon draussen" – darf die Frist nicht kippen
    d._hold_until = hold_until
    d._now_ms = lambda: now_ms
    d.app = None                    # getattr(_modal) -> False
    d._app_dragging = lambda: False
    d._pointer_in_window = lambda px, py: pointer_inside
    d.root = _Pointer(now_ms >= hold_until)
    d._collapsed = []
    d.collapse = lambda: d._collapsed.append(now_ms)
    return d


def test_dock_hold_blocks_collapse():
    """Waehrend der Haltefrist (von aussen aufgeklappt, Zeiger noch woanders) wird
    NICHT eingeklappt – sonst waere das Deck weg, bevor man hinsieht."""
    d = _dock_poll(now_ms=900, hold_until=1000)
    d._poll_once()
    assert d._collapsed == []
    assert d._outside_since is None      # Frist setzt zurueck -> volle Kulanz danach


def test_dock_hold_expires_with_full_delay():
    """Ganze Sequenz: waehrend der Frist gehalten, nach Fristende gilt wieder die
    normale Regel – erst Zeiger-draussen merken, einklappen erst COLLAPSE_DELAY_MS
    spaeter (kein schlagartiges Zuklappen im Moment des Fristendes)."""
    d = _dock_poll(now_ms=900, hold_until=1000)      # noch in der Frist
    d._poll_once()
    assert d._outside_since is None
    d._now_ms = lambda: 1000                          # Frist gerade abgelaufen
    d.root.allowed = True
    d._poll_once()
    assert d._collapsed == [] and d._outside_since == 1000
    d._now_ms = lambda: 1000 + dockm.COLLAPSE_DELAY_MS
    d._poll_once()
    assert len(d._collapsed) == 1


def test_dock_hold_ignored_when_pointer_arrives():
    """Kommt der Zeiger aufs Deck, uebernimmt die normale Logik (kein Einklappen)."""
    d = _dock_poll(now_ms=2000, hold_until=0, pointer_inside=True)
    d._poll_once()
    assert d._collapsed == [] and d._outside_since is None
