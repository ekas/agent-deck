"""capsule: die Neonroehre mit Alphakanal - reine Bildrechnung, kein Tk.
"""

import helpers  # setzt sys.path und die Deck-Sprache
from helpers import HR_W, HR_TUBE, HR_LEN

from deck.dock import controller as ed
from deck.dock import metrics as dockm
from deck.platform import focus as wf
from deck.platform import timing as wtime
from deck.render import capsule as hrender
from deck.render import capsule_masks as cmask


# Getestet wird handle_rgba/handle_bits – beides braucht kein Fenster. Was NICHT
# hierher kann, ist das Schieben ins Fenster selbst (win_focus.layered_push, reines
# Win32); das prüft der Screenshot-Durchlauf. Fehlt Pillow, gibt es nichts zu
# prüfen – edge_dock fällt dann auf den Linien-Pfad zurück.



def _hr(edge="left", color="#ffc48a", eff=1.0, hot=False):
    w, h = (HR_LEN, HR_W) if edge == "top" else (HR_W, HR_LEN)
    return hrender.handle_rgba(w, h, edge, HR_TUBE, color, eff, hot=hot)


def _hr_light(img):
    """Mittleres AUSGESTRAHLTES Licht: Helligkeit mit der Deckung gewichtet. Nur die
    Farbe zu messen wäre irreführend – ein Pixel mit Alpha 0 leuchtet nicht, egal
    welche Farbe darunter steht."""
    px = list(img.getdata())
    return sum((r + g + b) * a for r, g, b, a in px) / float(len(px) * 255)


def test_handle_has_no_box_around_it():
    """Die Zusage des Entwurfs: um die Kapsel herum ist NICHTS zu SEHEN – kein Kasten,
    kein Saum. Geprüft an den vier Ecken UND an den drei freien Rändern des Fensters:
    sind die leer, ist der Bloom nirgends abgeschnitten. Der vierte Rand (die Dockkante)
    darf Licht tragen – dort schneidet der Bildschirm selbst ab, wie in der Vorlage.

    „Leer" heißt HIT_ALPHA, nicht 0: das Polster ist die Zieh-Zone und muss Mausereignisse
    bekommen, und dafür darf das Alpha nicht auf 0 fallen (sonst klickt es durch). Ein
    Alpha von 1 von 255 ist unsichtbar, aber anfassbar."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    floor = hrender.HIT_ALPHA
    for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        assert img.getpixel(p)[3] <= floor, f"Ecke {p} ist sichtbar"
    assert all(img.getpixel((w - 1, y))[3] <= floor for y in range(h)), "Innenkante"
    assert all(img.getpixel((x, 0))[3] <= floor for x in range(w)), "obere Kante"
    assert all(img.getpixel((x, h - 1))[3] <= floor for x in range(w)), "untere Kante"
    assert max(a for _r, _g, _b, a in img.getdata()) == 255   # die Kapsel deckt voll


def test_handle_pad_stays_clickable():
    """Der Maus-Hit-Test eines layered Fensters folgt dem ALPHA: wo es 0 ist, klickt man
    durch und es kommt kein Ereignis an. Das Polster ist aber genau die Greif-Zone zum
    Verschieben – KEIN Pixel des Griffs darf also ganz auf 0 fallen."""
    if not hrender.AVAILABLE:
        return
    assert hrender.HIT_ALPHA >= 1
    for edge in ("left", "right", "top"):
        assert min(a for _r, _g, _b, a in _hr(edge).getdata()) >= hrender.HIT_ALPHA, edge


def test_handle_capsule_sits_where_the_template_had_it():
    """Die Kapsel schwebt mit kleinem Abstand von der Dockkante (wie in der Vorlage),
    ist quer voll deckend und lässt zur Fenstermitte hin Platz für den Bloom."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    y = h // 2
    # Schwelle 200, nicht 255: die Flanken der Kapsel sind ANTIALIASED, die äußersten
    # Spalten decken also absichtlich nicht voll. Genau dafür wird das Bild in
    # vierfacher Auflösung gezeichnet.
    solid = [x for x in range(w) if img.getpixel((x, y))[3] >= 200]
    assert solid, "keine deckende Kapsel gefunden"
    assert 1 <= solid[0] <= 5                      # kleiner Abstand zur Dockkante
    assert len(solid) >= HR_TUBE - 2               # Kapsel in voller Breite
    assert solid[-1] < w - 3                       # innen bleibt Luft fuer den Bloom


def test_handle_idle_stays_dim():
    """Bei idle soll der Griff findbar bleiben, aber nicht leuchten wie eine Rückfrage.

    Die Schwelle ist bewusst nicht scharf: der KÖRPER behält seine Deckung mit Absicht
    (eingeklappt ist er die einzige Bedienfläche), gedämpft werden Kern und Hof. Ein
    guter Teil des Unterschieds liegt ausserdem in der FARBE – idle-Grau ist
    unauffällig, ohne dunkler zu sein – und das kann diese Messung nicht sehen."""
    if not hrender.AVAILABLE:
        return
    idle = _hr_light(_hr(color="#8b8b99", eff=0.45))
    wait = _hr_light(_hr(color="#ffc48a", eff=1.0))
    assert idle < wait * 0.7
    assert idle > 0                                # aber nicht unsichtbar


def test_handle_body_is_brighter_in_the_middle():
    """Der Kapselkörper trägt einen Längs-Verlauf: in der Mitte heller, zu den Enden
    hin zurückgenommen. Das ist es, was ihn als Körper und nicht als Farbfliese lesen
    lässt – gemessen INNERHALB der Kapsel, damit der Bloom nicht mitmisst."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    h = img.size[1]
    x = int(HR_TUBE * cmask.OUT) + HR_TUBE // 2
    mid = sum(img.getpixel((x, h // 2))[:3])
    near_end = sum(img.getpixel((x, int(h * 0.12)))[:3])
    assert mid > near_end * 1.05
    assert img.getpixel((x, h // 2))[3] == 255      # trotzdem voll deckend


def test_handle_hot_and_flash_brighten():
    """Zeiger auf dem Griff und Aufblitzen müssen heller sein.

    Der Hover wird an der KAPSEL gemessen, nicht am Bildmittel: er wirkt vor allem auf
    den Körper, und im Mittel über das ganze Fenster (das grösstenteils durchsichtig
    ist) verschwindet das unter 5 %. Genau daran wäre die Messung fast vorbeigelaufen –
    die Vorlage hebt unter dem Zeiger nur den Weissanteil des Mitten-Highlights, und
    das sind gemessen 2 % Licht, also nichts.

    Das Aufblitzen darf am Mittel gemessen werden: dort zieht auch der Bloom auf, und
    das ist gerade der Sinn – es soll im Augenwinkel auffallen."""
    if not hrender.AVAILABLE:
        return
    x, y = int(HR_TUBE * cmask.OUT) + HR_TUBE // 2, HR_LEN // 2
    body = sum(_hr().getpixel((x, y))[:3])
    assert sum(_hr(hot=True).getpixel((x, y))[:3]) > body * 1.03
    assert _hr_light(_hr(eff=1.9)) > _hr_light(_hr()) * 1.10


def test_handle_edge_orientation():
    """Die Röhre klebt an JEDER Kante am Bildschirmrand: rechts andocken spiegelt das
    kanonische (linke) Bild, oben andocken dreht es um 90 Grad im Uhrzeigersinn –
    dabei wird aus der linken Spalte die obere Zeile."""
    if not hrender.AVAILABLE:
        return
    from PIL import Image
    left, right, top = _hr("left"), _hr("right"), _hr("top")
    assert list(right.getdata()) == list(left.transpose(Image.FLIP_LEFT_RIGHT).getdata())
    assert top.size == (HR_LEN, HR_W)
    assert list(top.getdata()) == list(left.transpose(Image.ROTATE_270).getdata())


def test_handle_has_a_glass_edge_toward_the_dock_side():
    """Die Glaskante liegt IM Körper, auf der Seite zur Dockkante – sie ist das Detail,
    das die Kapsel wie Glas und nicht wie Plastik aussehen lässt. Also muss es dort
    eine hellere Spalte geben als in der Kapselmitte."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    y = img.size[1] // 2
    x0 = int(HR_TUBE * cmask.OUT)
    inner = [sum(img.getpixel((x0 + d, y))[:3]) for d in range(HR_TUBE)]
    sheen = max(inner[:HR_TUBE // 3])              # helle Spalte auf der Dockseite
    plain = inner[HR_TUBE // 2]                    # Kapselmitte
    assert sheen > plain


def test_handle_bits_are_premultiplied_bgra():
    """UpdateLayeredWindow will BGRA mit VORMULTIPLIZIERTEM Alpha. Ohne die
    Vormultiplikation bekommen die weichen Kanten einen hellen Saum – und wo Alpha 0
    ist, MÜSSEN alle Kanäle 0 sein, sonst leuchtet dort ein Rest."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    bits = hrender.handle_bits(w, h, "left", HR_TUBE, "#ffc48a", 1.0)
    assert len(bits) == w * h * 4
    for i in range(0, len(bits), 4):
        b, g, r, a = bits[i:i + 4]
        assert max(b, g, r) <= a     # vormultipliziert: kein Kanal ueber dem Alpha
    # Oben links = Ecke: unsichtbar (keine Farbe), aber mit HIT_ALPHA anfassbar. Bei
    # vormultipliziertem Alpha heisst unsichtbar zwangslaeufig auch farblos.
    assert bits[0:3] == b"\x00\x00\x00" and bits[3] == hrender.HIT_ALPHA
    # BGRA, nicht RGBA: Amber (255,196,138) hat mehr Rot als Blau, im Puffer steht
    # Blau also VOR Rot – sonst waere der Griff blau statt amber. Gemessen in der
    # KAPSEL, nicht im Bloom: dort ist die Farbe unverwaessert.
    x = int(HR_TUBE * cmask.OUT) + HR_TUBE // 2
    mid = ((h // 2) * w + x) * 4
    assert bits[mid + 2] > bits[mid]


def test_handle_breathing_is_a_ramp_not_a_staircase():
    """Der Griff atmet nur zwischen 60 und 100 % Leuchtkraft – ein schmaler Weg. Wird
    die Leuchtkraft für den Bild-Cache zu grob gerastert (_qe), fällt er in wenige
    Stufen: mit dem von den Kacheln übernommenen 0.07er-Schritt kamen gemessen ganze
    SECHS verschiedene Bilder je Atemzug heraus, eine Stufe stand 605 ms unverändert.
    Das Atmen war damit keine Rampe, sondern eine Treppe – und sah genau so aus.

    Geprüft wird deshalb am fertigen Cache-Schlüssel, dass ein Atemzug für die MEHRHEIT
    seiner Frames ein neues Bild ergibt und keine Stufe merklich stehen bleibt. Dazu die
    Dauer des Atemzugs selbst: sie hängt an NEON_MS × NEON_PULSE_TICKS, und wer am Takt
    dreht, muss die Tickzahl mitziehen – sonst atmet der Griff plötzlich schneller."""
    d = object.__new__(ed.EdgeDock)
    d._glow_int, d._glow_pulse, d._bloom = 1.0, True, 0.0
    keys = []
    for i in range(dockm.NEON_PULSE_TICKS):
        d._pulse_i = i
        keys.append(round(hrender._qe(d._eff_intensity()), 6))
    assert len(set(keys)) >= dockm.NEON_PULSE_TICKS * 0.3, len(set(keys))
    laengste, lauf = 1, 1
    for a, b in zip(keys, keys[1:]):
        lauf = lauf + 1 if a == b else 1
        laengste = max(laengste, lauf)
    assert laengste * dockm.NEON_MS <= 200, laengste * dockm.NEON_MS
    assert 2100 <= dockm.NEON_MS * dockm.NEON_PULSE_TICKS <= 2500


def test_dock_frame_tick_is_one_frame_per_screen_refresh():
    """Der Animations-Takt ist die BILDPERIODE des Monitors, keine feste Zahl mehr.

    Vorher standen dort 10 ms (~100 Frames/s) in der Annahme, mehr als die 60 Hz des
    Schirms zu rechnen sei sicherer. Gemessen ist es das Gegenteil: 100 auf 60 gehen
    nicht auf, von je fünf Frames werden drei gezeigt (2-1-2-1-…), und die Schrittweite
    je ANGEZEIGTEM Bild springt zwischen einfach und doppelt – das war das Stottern.
    Dazu kostet ein Fenster-Move beim Hereinfahren ~8-9 ms, der 10-ms-Takt hatte also
    1 ms Luft und platzte laufend (Abstände 9,7 bis 19,5 ms statt 10).

    Die Rate selbst kommt vom Rechner, auf dem der Test läuft – geprüft wird darum die
    Rechnung darum herum, nicht ein fester Wert."""
    dockm._tick_ms = None                       # Messung erzwingen (Wert wird gemerkt)
    tick = dockm.frame_tick_ms()
    assert dockm.ANIM_TICK_MIN_MS <= tick <= dockm.ANIM_TICK_MAX_MS, tick
    if dockm.ANIM_TICK_MIN_MS < tick < dockm.ANIM_TICK_MAX_MS:      # nicht an die Grenze geklemmt
        assert tick == int(1000.0 / float(wtime.refresh_hz())), tick
    assert dockm.frame_tick_ms() == tick        # gemerkt, kein Win32-Aufruf je Frame
    # Eine unbrauchbar gemeldete Rate darf nie einen Takt von 0 ergeben (Timer-Sturm).
    dockm._tick_ms = None
    real, wtime.refresh_hz = wtime.refresh_hz, lambda *a, **k: 0
    try:
        assert dockm.frame_tick_ms() == dockm.ANIM_TICK_FALLBACK_MS
    finally:
        wtime.refresh_hz = real
        dockm._tick_ms = None


def test_handle_cache_reuse_and_clear():
    """Die FORM hängt nur an der Größe, nicht an der Farbe – dieselbe Größe muss
    denselben Maskensatz zurückgeben (sonst würde der atmende Griff je Frame neu
    rendern), und clear_cache muss ihn nach einem Monitorwechsel freigeben."""
    if not hrender.AVAILABLE:
        return
    hrender.clear_cache()
    first = cmask._masks(HR_W, HR_LEN, "left", HR_TUBE, False)
    assert cmask._masks(HR_W, HR_LEN, "left", HR_TUBE, False) is first
    assert cmask._masks(HR_W, HR_LEN, "left", HR_TUBE, True) is not first
    hrender.clear_cache()
    assert cmask._masks(HR_W, HR_LEN, "left", HR_TUBE, False) is not first
