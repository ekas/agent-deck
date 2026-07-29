"""Aufklappen auch ohne Maus-Ereignis: Tk kann ein <Enter> verschlucken, darum
fragt der Poll zusaetzlich nach.
"""

import helpers  # setzt sys.path und die Deck-Sprache

from deck.dock import controller as ed
from deck.dock import metrics as dockm


def _dock_hover(pointer, along=300, shown=True, lock=0, now=1000.0):
    """EdgeDock ohne Tk fuer _poll_reveal: Griff links, Zeiger frei setzbar."""
    class _Root:
        def winfo_pointerx(self):
            return pointer[0]

        def winfo_pointery(self):
            return pointer[1]

        def winfo_screenwidth(self):
            return 1920

        def winfo_screenheight(self):
            return 1080

    d = object.__new__(ed.EdgeDock)
    d.edge = "left"
    d.root = _Root()
    d._drag = None
    d.handle = object()
    d._handle_shown = shown
    d._reveal_job = None
    d._reveal_lock = lock
    d._now_ms = lambda: now
    d._anchor = (0, along)
    d._last_size = (300, 200)
    d._handle_drawn = (dockm.handle_thick(), 0)
    d.revealed = []
    d.reveal = lambda: d.revealed.append(True)
    return d


def test_dock_poll_reveals_without_mouse_event():
    """Der Griff ist ein rahmenloses Topmost-Fenster, das beim Ein-/Ausklappen durch
    withdraw/deiconify geht. Taucht er unter einem STEHENDEN Zeiger auf, schickt
    Windows kein Mausereignis – Tk feuert weder <Enter> noch <Motion>, und frueher
    tat sich dann gar nichts ('klappt nicht auf'). Der Poll muss das auffangen."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    d = _dock_hover((hx + hw // 2, hy + 4))      # oberes Ende, ausserhalb der Zieh-Zone
    d._poll_reveal()
    assert d.revealed == [True]


def test_dock_poll_leaves_grip_zone_alone():
    """Im unsichtbaren POLSTER neben der Kapsel wird gegriffen, NICHT aufgeklappt –
    sonst waere der Griff im Moment des Zufassens schon weg. Die Mitte der Laenge ist
    dagegen ganz normale Kapsel: dort MUSS es aufklappen (frueher war es umgekehrt)."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    im_polster = _dock_hover((hx + hw - 2, hy + hh // 2))
    im_polster._poll_reveal()
    assert im_polster.revealed == []
    auf_kapsel = _dock_hover((hx + 2, hy + hh // 2))     # Mitte der Laenge, an der Kante
    auf_kapsel._poll_reveal()
    assert auf_kapsel.revealed == [True]


def test_dock_grip_zone_is_the_invisible_pad_on_every_edge():
    """Die Zonengrenze laeuft QUER zum Griff und haengt an capsule_extent(): bis dahin
    Kapsel (Hover klappt auf), dahinter Polster (Greifen). Bei „rechts" liegt die
    Dockkante am ANDEREN Ende, dort muss gespiegelt gerechnet werden – sonst laege die
    Greif-Zone auf dem Leuchten und das Aufklappen im Unsichtbaren."""
    thick, ext = dockm.handle_thick(), dockm.capsule_extent()
    assert 0 < ext < thick                    # es gibt ueberhaupt ein Polster

    class _Ev:
        def __init__(self, x, y):
            self.x, self.y = x, y

    def dock(edge):
        d = object.__new__(ed.EdgeDock)
        d.edge = edge
        d._handle_drawn = (thick, 200) if edge != "top" else (200, thick)
        return d

    links = dock("left")
    assert links._in_grip(_Ev(thick - 1, 100)) is True      # innen = Polster
    assert links._in_grip(_Ev(1, 100)) is False             # an der Kante = Kapsel
    rechts = dock("right")
    assert rechts._in_grip(_Ev(0, 100)) is True             # bei rechts ist innen LINKS
    assert rechts._in_grip(_Ev(thick - 2, 100)) is False
    oben = dock("top")
    assert oben._in_grip(_Ev(100, thick - 1)) is True       # quer laeuft hier ueber y
    assert oben._in_grip(_Ev(100, 1)) is False


def test_dock_poll_reveal_off_handle_and_locked():
    """Neben dem Griff passiert nichts – und direkt nach dem Einklappen sperrt die
    Anti-Flatter-Frist den Poll-Weg, damit ein zufaellig dort liegender Zeiger das
    Deck nicht im selben Atemzug wieder aufreisst."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    off = _dock_hover((hx + hw + 50, hy + 4))
    off._poll_reveal()
    assert off.revealed == []
    locked = _dock_hover((hx + hw // 2, hy + 4), lock=2000.0, now=1000.0)
    locked._poll_reveal()
    assert locked.revealed == []
    hidden = _dock_hover((hx + hw // 2, hy + 4), shown=False)   # aufgeklappt
    hidden._poll_reveal()
    assert hidden.revealed == []
