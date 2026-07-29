"""Robustheit der Slide-Zustandsmaschine.

Hier haengen die drei Sicherungen gegen das halb ausgefahrene Deck: genau ein
Ausgang, die Deadline und der Watchdog gegen den ausgefallenen Frame-Timer.
"""

import tkinter as tk

import helpers  # setzt sys.path und die Deck-Sprache

from deck.dock import controller as ed


# Der eine Zustand, den es nicht geben darf, ist ein halb ausgefahrenes Deck. Die
# Tests hier stellen genau die Stoerungen nach, die frueher dazu fuehrten: eine
# Bewegung, deren Frames ausbleiben, und eine, deren geometry() nicht durchgeht.
_ANIM_TARGET = (2, 100, 300, 200)


def _dock_anim(edge="left"):
    """EdgeDock ohne Tk fuer die Slide-Zustandsmaschine. Uhr, geometry() und after()
    sind von aussen steuerbar, damit sich Frame-Ausfaelle und Fehler nachstellen
    lassen; die Haltegriffe (1-ms-Timer, Kachel-Animator) werden nur GEZAEHLT –
    getestet wird ihre Paarigkeit, nicht ihre Wirkung."""
    clock = [1000.0]

    class _Root:
        def __init__(self):
            self.geoms = []
            self.jobs = []
            self.fail = False

        def geometry(self, spec):
            if self.fail:
                raise tk.TclError("Fenster gerade weg")
            self.geoms.append(spec)

        def after(self, ms, fn):
            self.jobs.append(fn)
            return "job%d" % len(self.jobs)

        def after_cancel(self, job):
            pass

        def update_idletasks(self):
            pass

        # _settle_expanded misst hier immer "steht am Ziel"
        def winfo_rootx(self):
            return _ANIM_TARGET[0]

        def winfo_rooty(self):
            return _ANIM_TARGET[1]

        def winfo_width(self):
            return _ANIM_TARGET[2]

        def winfo_height(self):
            return _ANIM_TARGET[3]

    d = object.__new__(ed.EdgeDock)
    d.app = None
    d.edge = edge
    d.root = _Root()
    d.handle = None                 # -> _flash_border haelt sich raus
    d.expanded = False
    d._anim = None
    d._slide_target = _ANIM_TARGET
    d._retarget = False
    d._clip_on = False
    d._clip_px = 0
    d._outside_since = None
    d._reveal_lock = 0
    d._last_size = _ANIM_TARGET[2:]
    d.clock = clock
    d._now_ms = lambda: clock[0]
    d.held = [0]
    d._anim_hold = lambda: d.held.__setitem__(0, d.held[0] + 1)
    d._anim_release = lambda: d.held.__setitem__(0, d.held[0] - 1)
    d._reassert_topmost = lambda: None
    d.collapsed = []
    d._collapse_now = lambda: d.collapsed.append(True)
    return d


def _run_frames(d, step_ms=10.0, limit=200):
    """Die eingeplanten Frames abarbeiten und dabei die Uhr weiterdrehen."""
    n = 0
    while d.root.jobs and n < limit:
        d.clock[0] += step_ms
        d.root.jobs.pop()()
        n += 1
    return n


def test_dock_anim_reaches_target_and_releases():
    """Regulaerer Durchlauf: das Deck landet EXAKT auf dem Ziel, die Animation ist
    danach beendet und beide Haltegriffe (1-ms-Timer, Kachel-Animator) sind wieder
    freigegeben. Ein nicht freigegebener Haltegriff liesse den Prozess im schnellen
    Timer-Takt und die Kacheln fuer immer eingefroren zurueck."""
    d = _dock_anim()
    d._anim_to(+1)
    assert d.held[0] == 1                      # gehalten, solange es laeuft
    _run_frames(d)
    assert d._anim is None and d.expanded is True
    assert d.held[0] == 0
    x, y, w, h = _ANIM_TARGET
    assert d.root.geoms[-1].endswith(f"+{x}+{y}")


def test_dock_anim_only_first_frame_carries_size():
    """Die Groesse steht waehrend des Slides fest und geht nur in den ERSTEN Frame.
    Jedes weitere WxH+X+Y triebe Tk je Frame durch seinen Geometry-Manager – das ist
    Arbeit zwischen zwei Frames, und die sieht man als Ruckeln."""
    d = _dock_anim()
    d._anim_to(+1)
    _run_frames(d)
    assert "x" in d.root.geoms[0].split("+")[0]          # "300x200+..."
    assert all(g.startswith("+") for g in d.root.geoms[1:])
    assert len(d.root.geoms) > 3                          # es lief wirklich animiert


def test_dock_anim_deadline_snaps_to_target():
    """Notbremse: kommen die Frames nicht mehr (Tk blockiert, Timer verschluckt),
    springt die Bewegung ans Ziel statt auf halber Strecke stehenzubleiben."""
    d = _dock_anim()
    d._anim_to(+1)
    d._anim["deadline"] = d.clock[0] + 1        # Frist laeuft sofort ab
    d.clock[0] += 2
    d.root.jobs.pop()()                          # ein einziger, verspaeteter Frame
    assert d._anim is None and d.expanded is True
    assert d.held[0] == 0
    x, y, _w, _h = _ANIM_TARGET
    assert d.root.geoms[-1].endswith(f"+{x}+{y}")
    assert not d.root.jobs                       # kein Frame mehr eingeplant


def test_dock_anim_geometry_error_still_finishes():
    """Nimmt Tk die Geometrie nicht an (Fenster gerade weg oder neu gebaut), endete
    die Animation frueher einfach – das Deck blieb sichtbar auf halber Strecke
    stehen. Jetzt wird der Endzustand trotzdem hergestellt."""
    d = _dock_anim()
    d._anim_to(+1)
    d.root.fail = True
    d.clock[0] += 10
    d.root.jobs.pop()()
    assert d._anim is None and d.expanded is True and d.held[0] == 0


def test_dock_anim_watchdog_recovers_lost_frame_timer():
    """Verschluckt Tk den eingeplanten Frame (modaler Dialog, fremdes update()), kaeme
    nie wieder einer. Der Poll laeuft unabhaengig weiter und ist die einzige Instanz,
    die das bemerken kann – er holt die Bewegung ans Ziel."""
    d = _dock_anim()
    d._anim_to(+1)
    d.root.jobs.clear()                          # der Frame ist weg
    d._anim["job"] = None
    d._anim_watchdog()
    assert d._anim is None and d.expanded is True and d.held[0] == 0


def test_dock_anim_reverse_keeps_hold_and_motion():
    """Richtungswechsel mitten in der Bewegung: Position UND Geschwindigkeit werden
    uebernommen (sonst springt das Deck bzw. knickt seine Bewegung ab), und die
    Haltegriffe fallen dabei NICHT auf null – sonst gaebe es ein
    timeEndPeriod/timeBeginPeriod-Pingpong mitten im Slide."""
    d = _dock_anim()
    d._anim_to(+1)
    d.clock[0] += 60
    d.root.jobs.pop()()
    pos_mid, vel_mid = d._anim["pos"], d._anim["vel"]
    assert 0.0 < pos_mid < 1.0 and vel_mid > 0
    d._anim_to(-1)
    assert d._anim["pos"] == pos_mid and d._anim["vel"] == vel_mid
    assert d._anim["dir"] == -1 and d._anim["target"] == 0.0
    assert d.held[0] == 1
    _run_frames(d)
    assert d._anim is None and d.collapsed == [True] and d.held[0] == 0


def test_dock_edge_switch_during_slide_leaves_defined_state():
    """Rand wechseln, waehrend das Deck gerade herausgleitet: den Slide nur
    abzubrechen liesse das Fenster auf halber Strecke stehen – und ZWAR OHNE GRIFF,
    denn den hat reveal() beim Losfahren versteckt. Das Deck waere weder zu sehen
    noch hervorzuholen (angedockt gibt es keine Titelleiste). Also muss ein
    definierter Zustand herauskommen."""
    d = _dock_anim()
    d.app = type("A", (), {"settings": {}, "store": None})()
    d.app.settings = {}
    d._save_settings = lambda: None
    d._reposition_expanded = lambda: d.collapsed.append("repos")
    d._position_handle = lambda: None
    d._clear_clip = lambda: None
    d._anim_to(+1)
    d.clock[0] += 60
    d.root.jobs.pop()()                          # mitten in der Bewegung
    assert d._anim is not None
    d.set_edge("top")
    assert d._anim is None and d.held[0] == 0    # Haltegriff freigegeben
    assert d.expanded is True                    # war am Aufklappen -> gilt als offen
    assert d.collapsed == ["repos"]              # und wurde neu ausgerichtet


def test_dock_resize_during_slide_is_deferred():
    """Ein Inhalts-Resize (Agent kommt/geht) darf das Ziel nicht MITTEN in der
    Bewegung verschieben – das Deck spraenge sichtbar. Gemerkt und danach nachgezogen."""
    d = _dock_anim()
    d._anim_to(+1)
    before = d._slide_target
    d.on_resized()
    assert d._slide_target == before and d._retarget is True
