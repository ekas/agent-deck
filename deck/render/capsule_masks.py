"""Lage, Masse und Masken der Griff-Kapsel - reine Bildrechnung.

Hier steckt die Geometrie: wo die Kapsel im Griff-Fenster sitzt, wie weit der Bloom
reicht, wo die Glaskante liegt, wie breit das unsichtbare Polster ist, das den Griff
trotzdem anfassbar macht - und die drei Hebel des Schwappens.

Masken werden gecacht (_trim haelt den Cache klein). Wellenbilder dagegen NICHT: sie
aendern sich je Frame, ein Cache dafuer waere reiner Speicherfrass.
"""
from collections import OrderedDict

from deck.render.kit import mix as _mix

try:                                  # Pillow ist die einzige Nicht-Stdlib-Abhaengigkeit
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    AVAILABLE = True
except Exception:                     # ohne Pillow bleibt der Linien-Pfad
    Image = ImageChops = ImageDraw = ImageFilter = None
    AVAILABLE = False


# Grundton, gegen den alle Schichten verblassen (= dock.metrics.HANDLE_BG). Steht hier
# als Konstante, weil das Schwappen ihn braucht: dort dunkelt die Welle den Koerper
# GEGEN diesen Ton ab, und beide Stellen muessen denselben treffen.
GROUND = "#15151c"

# Aufloesungs-Faktor beim Zeichnen der Form (wie card_render.SS): 4x ist der Punkt,
# ab dem eine heruntergerechnete Rundung auf diesen Groessen nicht mehr von einer
# Vektorkante zu unterscheiden ist; 8x kostet das Vierfache und bringt nichts.
SS = 4

# ── Lage der Kapsel im Griff-Fenster (Anteile der Kapselbreite B) ────────
OUT = 0.19          # Abstand der aeusseren Flanke von der Dockkante (Vorlage: 3 von 16)
ENDS = 0.80         # Einzug der runden Enden vom Fensterende (Vorlage: 10 bei B 12)

# ── Bloom rings um die Kapsel ────────────────────────────────────────────
BLOOM_GROW = 0.07   # Aufblasen VOR dem Weichzeichnen (kraeftiger Hof statt blassem Rand)
BLOOM_BLUR = 0.22
BLOOM_CAP = 0.55
BLOOM_FADE = 0.45   # Ruhe-Anteil Richtung Grundton (siehe _lit)
# Zeiger auf dem Griff: die Vorlage hebt dafuer nur den Weissanteil des Mitten-
# Highlights (WARM_WHITE 0.16 -> 0.28), und das ist GEMESSEN 2 % mehr Licht – also
# unsichtbar. Weil die Rueckmeldung beim Anfahren funktionieren muss, ziehen Bloom und
# Glaskante zusaetzlich an: das aendert nicht nur die Helligkeit, sondern auch die
# AUSDEHNUNG, und beides nur solange der Zeiger draufsteht. Der Ruhezustand – also
# alles, was am Entwurf ausgewaehlt wurde – bleibt unberuehrt.
BLOOM_HOT = 1.25
# Ein Gauss-Verlauf endet nie, er wird nur immer blasser. Am inneren Fensterrand stand
# so noch Deckung 23 von 255 – und genau dort schneidet das Fenster ab. Ein
# abgeschnittener Verlauf ist eine KANTE, der Kasten waere damit nicht weg, nur leise.
# Darum wird der Schweif gekappt: alles unter BLOOM_FLOOR auf 0, der Rest wieder
# hochgezogen.
#
# Der Wert ist ein KOMPROMISS mit HANDLE_PAD: hoch gekappt (26) endete der Bloom
# sicher im Fenster, war im Direktvergleich mit der Vorlage aber deutlich kompakter –
# die aeussere Haze fehlte. Mehr unsichtbare Luft (HANDLE_PAD 10 -> 13) erlaubt jetzt
# einen weicheren Bloom bei niedrigerer Kappung. Wer hier dreht, muss nachmessen, dass
# die letzte Spalte vor dem Fensterrand noch 0 ist – dafuer gibt es einen Test.
BLOOM_FLOOR = 12

# ── Kapselkoerper ────────────────────────────────────────────────────────
BODY_FADE = 0.10    # Ruhe-Anteil Richtung Grundton – nahe 0, der Koerper IST das Licht
# Der Koerper unter dem Zeiger. Bloom und Glaskante allein reichen fuer die
# Rueckmeldung nicht: gemessen an der abgestrahlten Lichtmenge bewegen sie 1 %, weil
# die deckende Kapselflaeche alles dominiert. Wer den Griff anfaehrt, soll aber die
# KAPSEL aufleuchten sehen – also hellt der Koerper selbst leicht auf.
BODY_WHITE_HOT = 0.10
WARM_ENDS = 60      # Laengs-Verlauf: Deckung an den Bildenden (Mitte = 255)
WARM_WHITE = 0.16   # Weissanteil des Mitten-Highlights
WARM_WHITE_HOT = 0.28   # dito, solange der Zeiger auf dem Griff steht

# ── Glaskante (im Koerper, zur Dockkante hin) ────────────────────────────
SHEEN_OFF = 0.10    # Abstand von der Kapselflanke
SHEEN_W = 0.10      # Breite
SHEEN_BLUR = 0.03
SHEEN_INSET = 0.19  # Einzug an den Enden (damit sie nicht ueber die Rundung laeuft)
SHEEN_ENDS = 90     # Laengs-Verlauf der Glaskante
SHEEN_MID = 200
SHEEN_CAP = 0.55
SHEEN_WHITE = 0.75
SHEEN_HOT = 1.35    # dito unter dem Zeiger (siehe BLOOM_HOT)

# ── Unsichtbares Polster: trotzdem anfassbar ─────────────────────────────
# Der Maus-Hit-Test eines layered Fensters folgt dem ALPHA: wo es 0 ist, klickt man
# durch, dort kommt kein Ereignis mehr an. Genau dieses Polster ist aber die Greif-Zone
# zum Verschieben des Griffs – also darf es nirgends ganz auf 0 fallen. Ein Alpha von 1
# von 255 ist unsichtbar (auf Weiss verdunkelt es um eine Stufe von 255), macht das
# Fenster aber vollstaendig anfassbar.
HIT_ALPHA = 1

# Aufblitzen bei dringlicherem Status (edge_dock._bloom liefert Leuchtkraft > 1). Die
# Kapsel steht bei eff = 1 schon auf ihrer Vollfarbe – „noch mehr Farbe" gibt es dort
# nicht, das Aufblitzen waere unsichtbar. Also wird der Ueberschuss in WEISS umgesetzt,
# zusaetzlich zieht der Bloom kurz weiter auf.
FLASH_WHITE = 0.45
FLASH_BLOOM = 0.55

# ── Schwappen: die drei Hebel (handle_wave liefert das Profil) ────────────
# Der erste Anlauf drehte allein an der WARM-Schicht – und war kaum zu sehen. Der
# Grund steht schon oben bei WARM_WHITE_HOT: sie mischt 16 % Weiss ueber einen Koerper,
# der bereits Vollfarbe ist, und ihre Deckung klemmt in der Kapselmitte ohnehin auf
# 255 – nach oben ist dort kein Platz. Gemessen bewegte dieselbe Welle darueber 21 von
# 255 Graustufen, ueber die drei Hebel unten 88. Genau dieselbe Einsicht steckt in
# FLASH_WHITE: wo Farbe nicht mehr hilft, hilft Weiss.
WAVE_STRENGTH = 0.85     # in der Vorlage ausgewaehlte Staerke; EINE Zahl zum Drehen
WAVE_DARK = 0.62         # Koerper Richtung Grundton, wo die Welle dunkel ist
WAVE_WHITE = 0.55        # Weissglut ueber WARM_WHITE hinaus, wo sie hell ist
WAVE_BLOOM = 0.75        # Leuchthof je Zeile mitziehen (die Bewegung im Augenwinkel)
WAVE_WARM = 0.45         # Deckung der Warm-Schicht – Beiwerk, siehe oben

_mask_cache = OrderedDict()      # (W, H, edge, tube, hot, grip) -> dict der Masken
_bits_cache = OrderedDict()      # + Farbschluessel -> BGRA-Bytes (vormultipliziert)
_MASK_MAX, _BITS_MAX = 24, 64


def _trim(cache, limit):
    while len(cache) > limit:
        cache.popitem(last=False)


def _canon(w, h, edge):
    """Kanonische Groesse (Dicke quer, Laenge laengs). Am oberen Rand liegt der Griff
    quer, dort sind Breite und Hoehe getauscht."""
    return (h, w) if edge == "top" else (w, h)


def _orient(m, edge):
    """Die Masken entstehen KANONISCH senkrecht, mit der Dockkante links (x=0) – so
    gibt es nur einen Zeichenweg. Fuer die anderen Kanten wird die fertige Maske
    gedreht/gespiegelt, damit die Kapsel immer richtig zur Bildschirmkante liegt
    (Glaskante nach aussen): rechts andocken spiegelt, oben andocken dreht um 90 Grad
    im Uhrzeigersinn (dabei wird aus der linken Spalte die obere Zeile)."""
    if edge == "right":
        return m.transpose(Image.FLIP_LEFT_RIGHT)
    if edge == "top":
        return m.transpose(Image.ROTATE_270)
    return m


def _shape(W, H, x0, y0, x1, y1, blur=0.0, grow=0.0, cap=1.0):
    """Kapsel-Maske in kanonisch senkrechter Ausrichtung (W x H Pixel). Gezeichnet
    wird SS-fach, danach LANCZOS herunter – daher die weichen Rundungen."""
    k = SS
    big = Image.new("L", (W * k, H * k), 0)
    g = grow * k
    box = [x0 * k - g, y0 * k - g, x1 * k - 1 + g, y1 * k - 1 + g]
    r = max(1.0, min(box[2] - box[0], box[3] - box[1]) / 2.0)
    ImageDraw.Draw(big).rounded_rectangle(box, radius=r, fill=255)
    if blur > 0:
        big = big.filter(ImageFilter.GaussianBlur(blur * k))
    m = big.resize((W, H), Image.LANCZOS)
    if cap < 1.0:
        m = m.point(lambda v: int(v * cap))
    return m


def _gradient(W, H, ends, mid=255):
    """Laengs-Verlauf ueber das ganze Bild: an den Enden `ends`, in der Mitte `mid`.
    Wie in der Vorlage ueber die volle Bildhoehe, nicht nur ueber die Kapsel."""
    vals = []
    for y in range(H):
        t = abs((y + 0.5) / H * 2.0 - 1.0)         # 0 in der Mitte .. 1 am Bildende
        vals.append(int(round(mid + (ends - mid) * t)))
    col = Image.new("L", (1, H))
    col.putdata(vals)
    return col.resize((W, H))


def _byte(v):
    """Anteil 0..1 -> Deckung 0..255, geklemmt."""
    if v <= 0.0:
        return 0
    if v >= 1.0:
        return 255
    return int(v * 255.0 + 0.5)


def _column(W, H, raw, edge):
    """Ein Graustufen-Bild aus EINEM Byte je Bildzeile, auf die Breite gezogen – der
    Weg, auf dem ein 1D-Profil zu einer Maske wird.

    NEAREST ist hier nicht die schnelle, sondern die RICHTIGE Wahl: gestreckt wird ein
    Streifen von einem Pixel Breite, jede Spalte soll denselben Wert bekommen. Pillows
    Voreinstellung (BICUBIC) interpoliert stattdessen und schiesst an Kanten ueber –
    und sie kostet dabei das Dreifache.

    Gebaut wird KANONISCH senkrecht wie alle Masken hier; die Drehung zur Dockkante
    macht _orient gleich mit, sonst laege das Profil am oberen Rand quer."""
    col = Image.frombytes("L", (1, H), raw)
    return _orient(col.resize((W, H), Image.NEAREST), edge)


def _wave_layers(m, W, H, edge, prof):
    """Aus dem Wellen-Profil die fuenf Schichten machen, mit denen es sichtbar wird.

    `prof` traegt je Bildzeile die Abweichung vom Ruhezustand (handle_wave.profile):
    negativ = dunkler, positiv = heller. Getrennt behandelt, weil die beiden
    Richtungen verschiedene Hebel brauchen – dunkler geht ueber den Grundton, heller
    nur ueber Weiss (Farbe ist bei eff = 1 schon ausgereizt).

    Der Bloom bekommt ZWEI Schichten statt einer skalierten: eine Maske kann man
    multiplizieren (dimmen), aber nicht ueber ihren eigenen Wert hinaus anheben. Die
    zweite Schicht liegt darum in derselben Farbe darueber – zwei Schichten mit
    Deckung a1, a2 ergeben 1-(1-a1)(1-a2), und das ist genau der dichtere Hof. Wo der
    Bloom 0 ist (BLOOM_FLOOR hat ihn dort gekappt), bleibt er auch 0: die Zusage
    „kein Kasten am Fensterrand" gilt damit im Schwappen genauso.

    Die fuenf Byte-Reihen entstehen in EINER Schleife. Das ist kein Geiz: dieser Weg
    laeuft je Frame und war mit fuenf getrennten List-Comprehensions gemessen mehr als
    doppelt so teuer – bei einer Rechnung, die ohnehin nur aus Multiplikationen
    besteht, ist der Schleifen-Overhead selbst der Posten."""
    s = WAVE_STRENGTH
    bl, up, dk, wa, gl = bytearray(), bytearray(), bytearray(), bytearray(), bytearray()
    for v in prof:
        d = -v * s if v < 0.0 else 0.0           # Anteil dunkel
        u = v * s if v > 0.0 else 0.0            # Anteil hell
        bl.append(_byte(1.0 - WAVE_BLOOM * d))
        up.append(_byte(WAVE_BLOOM * u))
        dk.append(_byte(WAVE_DARK * d))
        wa.append(_byte(1.0 - WAVE_WARM * d))
        gl.append(_byte(WAVE_WHITE * u))
    return {
        "bloom": _mul(m["bloom"], _column(W, H, bytes(bl), edge)),
        "bloom_up": _mul(m["bloom"], _column(W, H, bytes(up), edge)),
        "dark": _mul(m["body"], _column(W, H, bytes(dk), edge)),
        "warm": _mul(m["warm"], _column(W, H, bytes(wa), edge)),
        "glow": _mul(m["body"], _column(W, H, bytes(gl), edge)),
    }


def _tail_cut(mask, floor=BLOOM_FLOOR):
    """Den blassen Schweif eines Verlaufs auf 0 kappen und den Rest wieder auf die
    volle Spanne ziehen (siehe BLOOM_FLOOR)."""
    if floor <= 0:
        return mask
    scale = 255.0 / (255 - floor)
    return mask.point(lambda v: 0 if v <= floor else min(255, int((v - floor) * scale)))


def _mul(a, b):
    return ImageChops.multiply(a, b)


def capsule_extent(tube):
    """Wie weit die SICHTBARE Kapsel von der Dockkante nach innen reicht (px).

    Daran haengen die Zonen des Griffs: bis hierher ist Kapsel (Hover -> Deck klappt
    auf), dahinter unsichtbares Polster (Greifen -> verschieben). edge_dock rechnet mit
    genau diesem Wert, damit die Zonengrenze und das Bild nicht auseinanderlaufen."""
    return int(round(tube * OUT)) + int(tube)


def _masks(W, H, edge, tube, hot):
    """Der Masken-Satz fuer diese Groesse (gecacht). `hot` gehoert in den Schluessel,
    damit er zum Bild-Cache passt – die Vorlage aendert unter dem Zeiger nur Farben,
    aber das darf sich hier nicht auf ein Detail verlassen."""
    key = (W, H, edge, tube, bool(hot))
    hit = _mask_cache.get(key)
    if hit is not None:
        _mask_cache.move_to_end(key)
        return hit

    B = float(tube)
    x0 = max(0.0, B * OUT)
    x1 = x0 + B
    ends = max(2.0, B * ENDS)
    y0, y1 = ends, max(ends + 2, H - ends)
    out = {}
    out["bloom"] = _tail_cut(_shape(W, H, x0, y0, x1, y1, blur=B * BLOOM_BLUR,
                                    grow=B * BLOOM_GROW, cap=BLOOM_CAP))
    body = _shape(W, H, x0, y0, x1, y1)
    out["body"] = body
    out["warm"] = _mul(body, _gradient(W, H, WARM_ENDS))
    sx = x0 + max(0.5, B * SHEEN_OFF)
    inset = max(1.0, B * SHEEN_INSET)
    out["sheen"] = _mul(
        _shape(W, H, sx, y0 + inset, sx + max(1.0, B * SHEEN_W), y1 - inset,
               blur=max(0.3, B * SHEEN_BLUR)),
        _gradient(W, H, SHEEN_ENDS, SHEEN_MID)).point(lambda v: int(v * SHEEN_CAP))
    out = {k: _orient(v, edge) for k, v in out.items()}
    _mask_cache[key] = out
    _trim(_mask_cache, _MASK_MAX)
    return out
