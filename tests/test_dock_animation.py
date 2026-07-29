"""Slide-Animation: Geometrie und Federkurve.

Die Feder ist kritisch gedaempft - bei randverankerten Panels darf es KEINEN
Ueberschwinger geben.
"""

import itertools
import math

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from deck.dock import controller as ed
from deck.dock import metrics as dockm


def _dock(edge, target):
    """EdgeDock ohne __init__/Tk – reicht fuer die reine Slide-Mathematik."""
    d = object.__new__(ed.EdgeDock)
    d.edge = edge
    d._slide_target = target
    return d


def test_dock_slide_endpoints():
    """v=0 -> genau HANDLE_THICK ragt ueber den Rand (das Fenster startet dort, wo
    der Griff sass), v=1 -> auf dem Ziel, das EDGE_GAP vom Rand weg liegt. Beides
    exakt, sonst springt es. Die Ziele sind die, die _expanded_rect liefert –
    inklusive der EDGE_GAP-Einrueckung, sonst faende der Startstreifen den Rand nicht."""
    t, g = dockm.handle_thick(), dockm.EDGE_GAP
    d = _dock("left", (g, 100, 300, 200))
    assert d._slide_geom(0.0) == (t - 300, 100, 300, 200)
    assert d._slide_geom(1.0) == (g, 100, 300, 200)
    d = _dock("right", (1920 - 300 - g, 100, 300, 200))   # Screen 1920 breit
    assert d._slide_geom(0.0) == (1920 - t, 100, 300, 200)
    assert d._slide_geom(1.0) == (1920 - 300 - g, 100, 300, 200)
    d = _dock("top", (100, g, 300, 200))
    assert d._slide_geom(0.0) == (100, t - 200, 300, 200)
    assert d._slide_geom(1.0) == (100, g, 300, 200)


def test_dock_clip_covers_everything_beyond_the_edge():
    """Weggeschnitten wird MINDESTENS der Teil jenseits der Dockkante – bliebe auch
    nur ein Pixel stehen, faende es sich als Geisterbild auf dem Nachbar-Monitor
    wieder. Gerundet wird in CLIP_QUANT-Stufen (jede Aenderung kostet ein
    SetWindowRgn samt Neuzeichnen), und zwar nach OBEN: hoechstens knapp eine Stufe
    zu viel, nie eine zu wenig. Kante ist der Bildschirmrand (0), nicht das um
    EDGE_GAP eingerueckte Ziel."""
    for edge, target, axis in (("left", (dockm.EDGE_GAP, 100, 300, 200), 0),
                               ("top", (100, dockm.EDGE_GAP, 300, 200), 1)):
        d = _dock(edge, target)
        d._clip_on = True
        for i in range(41):
            v = i / 40.0
            beyond = max(0, -d._slide_geom(v)[axis])     # was links/oberhalb von 0 liegt
            assert beyond <= d._clip_for(v) < beyond + dockm.CLIP_QUANT, (edge, v)
        assert d._clip_for(1.0) == 0                     # aufgeklappt -> Region weg
        d._clip_on = False                               # kein Nachbar-Monitor
        assert all(d._clip_for(i / 10.0) == 0 for i in range(11))


def _dock_rect(edge, win, screen=(1920, 1080), along=300):
    """EdgeDock ohne Tk fuer _expanded_rect: nur Bildschirmmasse + Inhaltsgroesse."""
    sw, sh = screen

    class _Root:
        def update_idletasks(self):
            pass

        def winfo_screenwidth(self):
            return sw

        def winfo_screenheight(self):
            return sh

        def winfo_reqwidth(self):
            return win[0]

        def winfo_reqheight(self):
            return win[1]

    d = object.__new__(ed.EdgeDock)
    d.edge = edge
    d.root = _Root()
    d._anchor = (along, along)
    d._last_size = win
    return d


def test_dock_expanded_rect_keeps_border_visible_on_all_four_sides():
    """Der selbst gezeichnete Cyan-Rand muss RUNDUM zu sehen sein. Windows 11 legt bei
    runden Ecken seinen eigenen Rand ueber die aeusserste Pixelreihe – buendig am
    Bildschirmrand fiel deshalb genau die Kante an der Dockseite optisch aus. Also
    haelt _expanded_rect an JEDER Kante mindestens EDGE_GAP Abstand: an der Dockkante
    exakt, quer dazu auch dann, wenn das Deck fast so hoch/breit wie der Schirm ist."""
    g, sw, sh = dockm.EDGE_GAP, 1920, 1080
    for edge, win in (("left", (300, 200)), ("right", (300, 200)), ("top", (300, 200)),
                      ("left", (300, sh - 2 * g)),      # so hoch wie eben erlaubt
                      ("top", (sw - 2 * g, 200))):
        x, y, w, h = _dock_rect(edge, win)._expanded_rect()
        assert (x, y, w, h) == (x, y, win[0], win[1])   # Groesse bleibt der Inhalt
        assert x >= g and y >= g, (edge, win, x, y)     # links/oben Luft
        assert x + w <= sw - g and y + h <= sh - g, (edge, win, x + w, y + h)
        # An der Dockkante GENAU EDGE_GAP: mehr waere ein sichtbarer Spalt, weniger
        # verschluckte den Rand wieder.
        assert {"left": x, "right": sw - (x + w), "top": y}[edge] == g


def test_dock_slide_monotone_and_fixed_size():
    """Steigendes v laeuft nie zurueck (kein Zucken) und die GROESSE bleibt fest –
    animiert wird nur die Position, sonst gibt es Reflow-Flackern."""
    d = _dock("left", (0, 0, 300, 200))
    rects = [d._slide_geom(i / 40.0) for i in range(41)]
    xs = [r[0] for r in rects]
    assert all(b >= a for a, b in itertools.pairwise(xs))
    assert {r[2:] for r in rects} == {(300, 200)}


def _spring_track(response_ms, dt_ms=10.0, steps=200, d0=-1.0, v0=0.0):
    """Eine Feder von d0 (Abstand zum Ziel) aus laufen lassen; liefert den Weg-
    Anteil 0..1 je Frame. Genau der Rechenweg, den _anim_step geht."""
    omega = 2.0 * math.pi / (response_ms / 1000.0)
    d, v, out = d0, v0, []
    for _ in range(steps):
        d, v = ed.EdgeDock._spring_at(d, v, omega, dt_ms / 1000.0)
        out.append(1.0 + d)                       # Ziel ist 1.0
    return out


def test_dock_spring_is_exact_regardless_of_step_size():
    """Die Feder wird ANALYTISCH gerechnet, nicht Schritt fuer Schritt integriert.
    Deshalb liefert sie nach derselben Zeit dasselbe Ergebnis, egal in wie vielen
    Frames man dort hinkommt – ein integrierendes Verfahren wuerde hier auseinander-
    laufen und bei grossem dt sogar explodieren. Genau das macht sie robust gegen
    ausgefallene Frames (Standby, blockiertes Tk)."""
    fein = _spring_track(220.0, dt_ms=1.0, steps=200)[-1]      # 200 ms in 200 Schritten
    grob = _spring_track(220.0, dt_ms=50.0, steps=4)[-1]       # 200 ms in 4 Schritten
    einer = _spring_track(220.0, dt_ms=200.0, steps=1)[-1]     # 200 ms in EINEM Schritt
    assert abs(fein - grob) < 1e-9 and abs(fein - einer) < 1e-9
    # Ein absurd grosses dt (eingeschlafener Rechner) landet sauber am Ziel.
    assert abs(_spring_track(220.0, dt_ms=60000.0, steps=1)[-1] - 1.0) < 1e-9


def test_dock_spring_never_overshoots():
    """Kritische Daempfung = kein Ueberschwingen. Ein Randpanel, das ueber sein Ziel
    hinausschiesst, wirkt wackelig – Overshoot gehoert zu Bewegungen, die der Nutzer
    mit Schwung angestossen hat, nicht zu einem Hover-Panel."""
    for response in (dockm.COLLAPSE_RESPONSE_MS, dockm.REVEAL_RESPONSE_MS):
        track = _spring_track(response)
        assert max(track) <= 1.0 + 1e-12, response
        assert all(b >= a - 1e-12 for a, b in itertools.pairwise(track))   # nie zurueck


def test_dock_spring_is_front_loaded_but_starts_from_rest():
    """Der Charakter der Bewegung: bei halber Zeit schon deutlich ueber halbem Weg
    (das ist der Unterschied zwischen 'reagiert' und 'laeuft ab' – smoothstep steht
    dort exakt bei 50 %), aber der erste Frame legt nur wenig zurueck. Genau daran
    war hier schon einmal ein cubic-ease-out gescheitert: sein Vollgas-Start ruckte
    sichtbar. Die Feder startet aus dem Stand."""
    track = _spring_track(dockm.REVEAL_RESPONSE_MS)
    ende = next(i for i, x in enumerate(track) if x > 0.99)
    assert track[ende // 2] > 0.75                     # front-loaded
    assert track[0] < 0.05                             # kein Sprung im ersten Frame
    # Und sie ist in brauchbarer Zeit durch (nicht: kriecht ewig ans Ziel).
    assert 15 <= ende <= 35, ende                      # Frames a 10 ms


def test_dock_spring_is_not_slower_than_the_curve_it_replaced():
    """Die Feder darf sich nicht als Verlangsamung anfuehlen: nach 120 ms muss sie
    dort sein, wo die alte smoothstep-Kurve ueber 170 ms auch war (~90 %). Ihr
    Gewinn liegt DAVOR – die Halbzeit-Marke muss deutlich weiter sein."""
    def smoothstep(p):
        p = max(0.0, min(1.0, p))
        return p * p * (3.0 - 2.0 * p)

    feder = _spring_track(dockm.REVEAL_RESPONSE_MS)
    assert feder[11] >= smoothstep(120 / 170.0) - 0.02      # 12 Frames = 120 ms
    assert feder[7] > smoothstep(80 / 170.0) + 0.20         # nach 80 ms klar voraus
    assert dockm.COLLAPSE_RESPONSE_MS < dockm.REVEAL_RESPONSE_MS  # Wegräumen zügiger


def test_dock_spring_reversal_is_velocity_continuous():
    """Beim Richtungswechsel wird nur das Ziel getauscht – die Geschwindigkeit laeuft
    weiter. Das Deck bremst also aus voller Fahrt ab, statt seine Kurve rueckwaerts
    abzuspulen: die Bewegung kehrt weich um und braucht dafuer nur so lange, wie der
    Restweg hergibt."""
    omega_auf = 2.0 * math.pi / (dockm.REVEAL_RESPONSE_MS / 1000.0)
    omega_zu = 2.0 * math.pi / (dockm.COLLAPSE_RESPONSE_MS / 1000.0)
    d, v = -1.0, 0.0
    for _ in range(8):                                  # 80 ms aufklappen
        d, v = ed.EdgeDock._spring_at(d, v, omega_auf, 0.010)
    pos_wechsel, v_wechsel = 1.0 + d, v
    assert 0.2 < pos_wechsel < 0.95 and v_wechsel > 0   # mitten in der Fahrt, nach aussen
    # Ziel jetzt 0.0 -> Abstand ist die Position selbst, Geschwindigkeit bleibt.
    # Fein abgetastet, denn die Traegheit spielt sich in wenigen Millisekunden ab:
    # die Feder laeuft noch ein STUECK weiter nach aussen, statt die Richtung im
    # selben Moment umzuklappen. Sichtbar ist das kaum (das Maximum liegt vor dem
    # ersten 10-ms-Frame) – gemeint ist es auch nicht als Effekt, sondern als Beleg,
    # dass die Geschwindigkeit stetig durch den Wechsel laeuft.
    d, v = pos_wechsel, v_wechsel
    fein = []
    for _ in range(600):
        d, v = ed.EdgeDock._spring_at(d, v, omega_zu, 0.001)
        fein.append(d)
    assert max(fein) > pos_wechsel                      # kein Vorzeichensprung
    assert max(fein) - pos_wechsel < 0.05               # aber auch kein Ausschlag
    # ... und danach sauber zurueck auf null, ohne durchzuschlagen.
    assert fein[-1] < 0.005 and min(fein) > -1e-9


def test_monitorwechsel_verwirft_den_gemerkten_frame_takt():
    """apply_ui_scale() muss den gemessenen Bildtakt wegwerfen - der neue Monitor kann
    eine andere Bildrate haben.

    Dieser Test existiert wegen eines Bugs, den das Aufteilen von edge_dock.py erzeugt
    hat: controller.py machte `global _tick_ms; _tick_ms = None`, aber die Variable lebt
    in metrics.py. Das global erzeugte eine EIGENE Modulvariable in controller - die
    gemerkte in metrics blieb stehen, und die Animation lief nach einem Monitorwechsel
    mit dem Takt des alten Schirms weiter. Nichts meldete das; gefunden hat es erst die
    Typprüfung (Name "_tick_ms" is not defined).
    """
    dockm._tick_ms = 42                     # gemessener Takt des "alten" Monitors
    dockm.forget_tick()
    assert dockm._tick_ms is None, "der gemerkte Takt muss weg sein"
    # Und der nächste Aufruf misst wieder (Wert kommt vom Rechner, darum nur die Grenzen)
    tick = dockm.frame_tick_ms()
    assert dockm.ANIM_TICK_MIN_MS <= tick <= dockm.ANIM_TICK_MAX_MS, tick
