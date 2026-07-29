"""Das Schwappen im Kern der Kapsel (fluid + capsule.WAVE_*).
"""

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from helpers import HR_LEN, HR_TUBE, HR_W

from deck.dock import controller as ed
from deck.dock import metrics as dockm
from deck.dock import wave as dockwave
from deck.render import capsule as hrender
from deck.render import capsule_masks as cmask
from deck.render import fluid as hwave

# Variante 09 der Fluid-Vorlage: das helle Mittelstueck kippt zur einen Seite, zurueck
# zur anderen, und kommt zur Ruhe. Die Zusage dahinter ist, dass der ausgewaehlte
# Entwurf der NULLPUNKT bleibt – darum steht dieser Test zuerst.
WAVE_PEAK = 0.4        # s nach dem Anstoss: erste Kippbewegung am Umkehrpunkt


def _wave_light(img, at):
    """Mittlere Helligkeit der Kapsel auf einem Bruchteil `at` ihrer Laenge."""
    x = int(HR_TUBE * cmask.OUT) + HR_TUBE // 2
    y = int(img.size[1] * at)
    return sum(img.getpixel((x, y))[:3])


def test_wave_is_only_a_deviation_from_the_selected_design():
    """Ein Profil aus Nullen muss Pixel fuer Pixel dasselbe Bild ergeben wie GAR KEIN
    Profil. Das ist die Zusage, auf der der ganze Umbau steht: das Schwappen ist eine
    Auslenkung aus dem ausgewaehlten Entwurf, kein neuer Entwurf. Wer es abschaltet
    (wave.WAVE_ON), bekommt exakt den alten Griff zurueck – und niemand muss die
    Kapselform, den Bloom oder die Glaskante nachmessen."""
    if not hrender.AVAILABLE:
        return
    ruhe = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    null = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0,
                               prof=[0.0] * HR_LEN)
    assert list(null.getdata()) == list(ruhe.getdata())


def test_wave_tips_the_capsule_to_one_side():
    """Im Ruhezustand ist die Roehre laengs SYMMETRISCH (der Verlauf faellt zu beiden
    Enden gleich ab). Genau das bricht das Schwappen: am Umkehrpunkt ist die eine
    Haelfte hell und die andere zurueckgenommen. Deshalb wird hier die Asymmetrie
    gemessen – sie kann im alten Bild gar nicht vorkommen."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)
    assert max(prof) > 0.5 and min(prof) < -0.5, "Welle ist am Umkehrpunkt ausgelenkt"
    ruhe = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    welle = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert abs(_wave_light(ruhe, 0.15) - _wave_light(ruhe, 0.85)) < 12   # vorher gleich
    hell, dunkel = _wave_light(welle, 0.15), _wave_light(welle, 0.85)
    assert hell > dunkel + 90, (hell, dunkel)


def test_wave_needs_all_three_levers_to_be_visible():
    """Warum die Welle nicht allein an der WARM-Schicht haengt.

    Der erste Anlauf tat genau das – und war kaum zu sehen. Der Grund steht bei
    WARM_WHITE_HOT: die Schicht mischt 16 % Weiss ueber einen Koerper, der schon
    Vollfarbe ist, und ihre Deckung klemmt in der Kapselmitte ohnehin auf 255. Gemessen
    bewegte dieselbe Welle darueber 21 von 255 Graustufen, ueber Koerper + Weissglut +
    Leuchthof 88. Der Test haelt das Verhaeltnis fest, damit niemand die drei Hebel
    „aufraeumt" und sich hinterher wundert, dass man nichts mehr sieht."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)

    def spanne(**aus):
        # Gepatcht wird in capsule_masks, NICHT in capsule: die drei Hebel werden in
        # _wave_layers gelesen, und das liegt dort. Ein Patch auf capsule bliebe
        # wirkungslos - und weil hier per setattr gepatcht wird, findet das keine
        # statische Suche, nur der Testlauf.
        alt = {k: getattr(cmask, k) for k in aus}
        try:
            for k, v in aus.items():
                setattr(cmask, k, v)
            img = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0,
                                      prof=prof)
            return (_wave_light(img, 0.15) - _wave_light(img, 0.85)) / 3.0
        finally:
            for k, v in alt.items():
                setattr(cmask, k, v)

    voll = spanne()
    nur_warm = spanne(WAVE_DARK=0.0, WAVE_WHITE=0.0, WAVE_BLOOM=0.0)
    assert voll > 60, voll                        # deutlich sichtbar
    assert voll > nur_warm * 2.5, (voll, nur_warm)


def test_wave_keeps_the_frame_invisible_and_grabbable():
    """Die zwei Zusagen des Rahmens gelten auch im Schwappen: um die Kapsel herum ist
    NICHTS zu sehen (kein Kasten), und kein Pixel faellt auf Alpha 0 (sonst klickt man
    durch das Polster hindurch und der Griff waere nicht mehr zu greifen).

    Der Leuchthof zieht in der Welle auf – genau dann koennte er den Fensterrand
    erreichen. Dass er es nicht tut, liegt an BLOOM_FLOOR: dort ist er auf 0 gekappt,
    und 0 bleibt es auch, wenn eine zweite Schicht daruebergeht.

    Geprueft an JEDER Dockkante, und das ist kein Selbstzweck: am oberen Rand liegt der
    Griff quer, dort sind Breite und Hoehe getauscht (_canon) und das Profil muss sich
    mitdrehen. Eine Verwechslung faellt hier sofort auf – die Masken haetten dann nicht
    mehr dieselbe Groesse."""
    if not hrender.AVAILABLE:
        return
    floor = hrender.HIT_ALPHA
    for edge in ("left", "right", "top"):
        w0, h0 = (HR_LEN, HR_W) if edge == "top" else (HR_W, HR_LEN)
        for at in (WAVE_PEAK, WAVE_PEAK + 0.8, 2.0):
            prof = hwave.profile(HR_LEN, at)          # Profil laeuft immer LAENGS
            img = hrender.handle_rgba(w0, h0, edge, HR_TUBE, "#ffc48a", 1.0, prof=prof)
            w, h = img.size
            assert (w, h) == (w0, h0), edge
            # Die drei freien Raender; der vierte ist die Dockkante, dort klebt die Kapsel.
            frei = ([(w - 1, y) for y in range(h)] if edge == "left" else
                    [(0, y) for y in range(h)] if edge == "right" else
                    [(x, h - 1) for x in range(w)])
            assert all(img.getpixel(p)[3] <= floor for p in frei), (edge, at)
            assert min(a for _r, _g, _b, a in img.getdata()) >= floor, (edge, at)


def test_wave_bits_stay_premultiplied_bgra():
    """Was in das Fenster geschoben wird, muss AUCH im Schwappen vormultipliziertes
    BGRA sein – kein Kanal ueber dem Alpha.

    Das ist hier keine Formalie: die Welle legt eine WEISSE Schicht ueber den Koerper,
    und Weiss ist der Fall, in dem ein Kanal am ehesten ueber das Alpha steigt. Ginge
    das schief, bekaeme die Kapsel genau dort einen hellen Saum, wo sie am hellsten ist
    – und der Fehler saehe nach einem Fehler im Entwurf aus, nicht nach einem in der
    Bytefolge."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)
    bits = hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert len(bits) == HR_W * HR_LEN * 4
    for i in range(0, len(bits), 4):
        b, g, r, a = bits[i:i + 4]
        assert max(b, g, r) <= a, i // 4
    assert bits[0:3] == b"\x00\x00\x00" and bits[3] == hrender.HIT_ALPHA
    # Und weiterhin BGRA, nicht RGBA: Amber hat mehr Rot als Blau.
    x = int(HR_TUBE * cmask.OUT) + HR_TUBE // 2
    mid = ((HR_LEN // 2) * HR_W + x) * 4
    assert bits[mid + 2] > bits[mid]
    # Ein Wellenbild darf NICHT im Cache landen: es traefe nie wieder und wuerde nur
    # die Ruhezustaende hinausdruecken, von denen die Ruhephase lebt.
    hrender.clear_cache()
    hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert not hrender._bits_cache
    hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    assert len(hrender._bits_cache) == 1


def test_wave_is_a_damped_swing_that_returns_to_rest():
    """Die Bewegung selbst: ein Anstoss, ein Kippen hin und zurueck, dann Ruhe – und
    nach PERIOD faengt es von vorn an. Dass sie zur Ruhe kommt, ist der Grund, warum
    der Bild-Cache noch etwas taugt (quiet() -> der Griff nimmt sein gecachtes
    Ruhebild), und dass sie exakt zyklisch ist, macht sie zur reinen Funktion der Zeit:
    nach einer Pause muss nichts nachgerechnet werden."""
    assert hwave.quiet(0.0)                                   # im Anstoss selbst noch still
    assert not hwave.quiet(WAVE_PEAK)                         # kurz danach voll ausgelenkt
    assert hwave.quiet(hwave.PERIOD - 0.05)                   # vor dem naechsten Stoss Ruhe
    # Vorzeichenwechsel = es kippt zurueck, statt nur einmal auszuschlagen.
    oben = [hwave.profile(16, t)[0] for t in (0.4, 1.2)]
    assert oben[0] > 0 > oben[1], oben
    # Zyklisch (auf Rundung, nicht auf das Bit: fmod laesst in der letzten Stelle
    # Rest) – das ist es, was die Bewegung zur reinen Funktion der Zeit macht.
    a, b = hwave.profile(24, 0.7), hwave.profile(24, 0.7 + hwave.PERIOD)
    assert max(abs(x - y) for x, y in zip(a, b, strict=False)) < 1e-9
    p = hwave.profile(101, WAVE_PEAK)
    assert len(p) == 101 and all(hwave.LO <= v <= hwave.HI for v in p)
    assert abs(p[50]) < 0.35 < abs(p[0]), (p[0], p[50])       # Knoten in der Mitte


def test_wave_profile_is_off_without_alpha_or_in_rest():
    """Wann der Griff KEIN Wellen-Profil bekommt – drei Faelle, jeder mit Grund:
    abgeschaltet, kein Alpha-Pfad (im Linien-Rueckfall gibt es keine Schicht, in der
    eine Welle Platz haette) und Ruhephase. In allen drei Faellen zeichnet er sein
    gecachtes Ruhebild, und das ist genau der alte Griff."""
    d = object.__new__(ed.EdgeDock)
    d._layered = True
    d._wave_t0 = d._now_ms() - WAVE_PEAK * 1000.0
    assert d._wave_profile(HR_LEN) is not None
    d._layered = False
    assert d._wave_profile(HR_LEN) is None, "Linien-Rueckfall traegt keine Welle"
    d._layered = True
    assert d._wave_profile(4) is None, "zu kurz fuer ein Profil"
    d._wave_t0 = d._now_ms() - (hwave.PERIOD - 0.05) * 1000.0
    assert d._wave_profile(HR_LEN) is None, "Ruhephase -> gecachtes Ruhebild"
    real, dockwave.WAVE_ON = dockwave.WAVE_ON, False
    try:
        d._wave_t0 = d._now_ms() - WAVE_PEAK * 1000.0
        assert d._wave_profile(HR_LEN) is None, "abgeschaltet"
    finally:
        dockwave.WAVE_ON = real


def test_wave_kick_and_timer_and_sliding():
    """Die drei Verdrahtungen im Dock.

    (1) Ein dringlicher werdender Status stoesst neu an: der Blitz sagt „jetzt", die
        Welle danach sagt „gerade passiert" – aus einem Aufblitzen wird eine Spur.
    (2) Der Puls-Timer muss fuer die Welle laufen, auch wenn nichts atmet und kein
        Blitz abklingt; vorher hing er allein an diesen beiden.
    (3) Waehrend das Deck GLEITET, wird nicht gemalt: die Feder laeuft im selben Thread
        und braucht jeden Frame. Die Schwingung selbst laeuft an der Uhr weiter und ist
        danach an der richtigen Stelle – genau dafuer ist es eine Uhr."""
    d = object.__new__(ed.EdgeDock)
    d._layered, d._handle_shown = True, True
    d._glow_pulse, d._bloom, d._glow_int = False, 0.0, 1.0
    d._glow_color, d._hot, d._pulse_i = "#ffc48a", False, 0
    d._wave_t0 = d._now_ms() - 3000.0
    d.handle = d.handle_canvas = None
    d._glow_job = None
    d._start_glow = lambda: None              # der echte Timer braucht ein Tk-Fenster
    # (1)
    alt = d._wave_t0
    d.set_glow("#6ee7a8", 0.85, False, flash=True)
    assert d._wave_t0 > alt, "Eskalation stoesst den Kern neu an"
    # (2) – der Blitz aus (1) brennt noch, der zaehlte sonst selbst als Grund
    d._bloom = 0.0
    assert d._glow_needed() is True
    d._layered = False                       # ohne Alpha-Pfad wieder wie vorher
    assert d._glow_needed() is False
    d._layered = True
    d._handle_shown = False                  # aufgeklappt -> kein Griff, kein Timer
    assert d._glow_needed() is False
    # (3)
    gemalt = []
    d._handle_shown = True
    d._paint_handle = lambda: gemalt.append(1)
    d._anim = object()                        # sliding() liest genau das
    d._glow_tick()
    assert gemalt == [], "waehrend des Slides nicht malen"
    d._anim = None
    d._glow_tick()
    assert gemalt == [1]


def test_handle_never_pushes_into_a_hidden_window():
    """Ein VERSTECKTES layered Fenster nimmt kein Bild an – UpdateLayeredWindow lehnt
    mit ERROR_INVALID_PARAMETER ab. Das hat den Griff einmal komplett gekostet:
    _collapse_now positioniert (und zeichnet) ihn, BEVOR _show_handle ihn einblendet,
    der allererste Schub scheiterte, und der Griff fiel dauerhaft auf den Linien-Pfad
    zurück – dunkler Kasten statt Kapsel.

    Der Test haelt die Reihenfolge fest, nicht die Win32-Regel: solange der Griff nicht
    sichtbar ist, darf _paint_layered NICHTS schieben und vor allem nicht aufgeben."""
    pushed = []
    d = object.__new__(ed.EdgeDock)
    d.edge = "left"
    d._layered = True
    d._handle_hwnd = 1234
    d._img_size = (29, 220)
    d._handle_shown = False              # eingeklappt, aber Fenster noch versteckt
    d._hot = d._grip_hot = False
    d.handle = d.handle_canvas = None
    d._paint_layered("#ffc48a", 1.0)
    assert pushed == []                  # kein Schub
    assert d._layered is True            # und NICHT aufgegeben
    # Sichtbar geworden -> jetzt darf (und muss) es schieben. Ohne Tk-Fenster scheitert
    # der Schub, der Rueckfall ist also der erwartete Weg – entscheidend ist, dass es
    # ueberhaupt versucht wird.
    d._handle_shown = True
    d._enable_alpha = lambda force=False: None
    d._report_layer_failure = lambda w, h: None    # der Rueckfall ist hier gewollt
    d._draw_handle = lambda w, h: pushed.append(("linien", w, h))
    d._handle_drawn = (0, 0)
    d._paint_layered("#ffc48a", 1.0)
    assert pushed and pushed[0][0] == "linien"
    assert d._layered is False


def test_handle_window_is_thicker_than_the_capsule():
    """Das Fenster braucht Luft: für den Bloom (sonst wäre er an der Fensterkante
    abgeschnitten) UND als Greif-Zone. Alles Geometrische (Griff-Position,
    Slide-Startstreifen) rechnet deshalb mit der FENSTERdicke, nicht mit der Kapsel –
    und zwar unabhängig davon, ob Pillow da ist: die Zieh-Zone darf nicht an einer
    Bibliothek hängen."""
    assert dockm.handle_thick() == dockm.HANDLE_THICK + dockm.HANDLE_PAD
    assert dockm.HANDLE_PAD > 0
    # Die Grenze muss INNERHALB des Fensters liegen, sonst gäbe es keine der beiden
    # Zonen: 0 -> alles Polster (nie aufklappen), ganze Dicke -> alles Kapsel (nie greifen).
    assert 0 < dockm.capsule_extent() < dockm.handle_thick()
