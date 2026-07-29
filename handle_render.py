"""Griff-Balken des angedockten Decks: die KAPSEL, freistehend auf dem Desktop.

Der Entwurf ist Variante 02 aus der Design-Vorlage, unverändert: ein voller
Leuchtkörper über fast die ganze Griffdicke, mit runden Enden, in der Mitte heller
(Längs-Verlauf), einer Glaskante an der Seite zur Bildschirmkante und einem weichen
Bloom rings herum. Die Zieh-Zone wird zur WULST – die Kapsel ist dort kurz dicker,
statt nur heller zu werden; das trägt auch bei Rückfrage-Amber, wo „noch heller"
nichts mehr hergibt.

Der EINZIGE Unterschied zur Vorlage: dort lag die Kapsel auf einer dunklen
Grundfläche, weil Tk nichts anderes hergab – und diese Fläche war am Bildschirmrand
als Kasten zu sehen. Hier ist sie WEG. Das Bild trägt einen Alphakanal, und alles,
was nicht Kapsel oder Bloom ist, hat Deckung 0: dort ist der Desktop.

Zwei Dinge kann Tk nicht, beide über Win32 umgangen:
  • Antialiasing – der Canvas rastert eine Rundung hart. Also zeichnet Pillow die
    Form in SS-facher Auflösung und rechnet sie herunter (wie card_render.py).
  • Transparenz je Pixel – Tk kennt nur „ganzes Fenster halbdurchsichtig" (-alpha)
    und „eine Farbe ausgestanzt" (-transparentcolor); letzteres lässt die Mischpixel
    weicher Kanten als Saum stehen. Das Griff-Fenster wird darum WS_EX_LAYERED und
    bekommt seinen Inhalt per UpdateLayeredWindow (win_focus.layered_push).

Die Kapsel klebt nicht am Bildschirmrand, sie hat wie in der Vorlage einen kleinen
Abstand davon (OUT). Nach INNEN braucht der Bloom mehr Luft – deshalb ist das
Griff-FENSTER dicker als die Kapsel (edge_dock.HANDLE_PAD), und dieses Modul bekommt
beide Maße getrennt: die Kapselbreite als `tube`, die Fensterbreite über w/h.

Alle übrigen Maße sind Anteile der Kapselbreite, nicht absolute Pixel: wächst der
Griff (HiDPI oder eine andere HANDLE_THICK), wächst der Entwurf mit.

Der Trick gegen die Rechenlast (der Griff atmet im 55-ms-Takt) ist derselbe wie bei
den Kacheln: die FORM hängt nur an der Größe, nicht an der Farbe. Sie wird je Größe
einmal als Satz Graustufen-MASKEN gerendert und behalten; pro Frame wird daraus nur
zusammengesetzt. Zwei Caches – Masken je Größe und fertige BGRA-Bytes je
Farbkombination, deren Schlüssel Farbe und Leuchtkraft grob rastert; sonst legte
jeder Atemzug einen neuen Eintrag an.

Fehlt Pillow, ist AVAILABLE False und edge_dock bleibt beim alten Linien-Pfad – an
einer fehlenden Bibliothek darf das Deck nicht scheitern.
"""
from collections import OrderedDict

from canvas_kit import mix as _mix

try:                                  # Pillow ist die einzige Nicht-Stdlib-Abhaengigkeit
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    AVAILABLE = True
except Exception:                     # ohne Pillow bleibt der Linien-Pfad
    Image = ImageChops = ImageDraw = ImageFilter = None
    AVAILABLE = False

# Grundton, gegen den alle Schichten verblassen (= edge_dock.HANDLE_BG). Steht hier
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


def _lit(color, eff, fade, bg=None):
    """Eine Schicht von der Statusfarbe Richtung Grundton verblassen, skaliert mit der
    Leuchtkraft – genau die Rechnung der Vorlage (und von edge_dock.neon_color), damit
    idle, arbeitet, ungelesen und Rueckfrage dieselben Helligkeiten treffen wie im
    ausgewaehlten Entwurf."""
    return _mix(color, bg or GROUND, 1 - (1 - fade) * min(eff, 1.0))


def _dim(mask, factor):
    """Deckung einer Schicht skalieren (fuer den aufziehenden Bloom beim Aufblitzen)."""
    f = max(0.0, factor)
    if 0.999 <= f <= 1.001:
        return mask
    return mask.point(lambda v: min(255, int(v * f)))


def _layer(size, color, mask):
    """Eine RGBA-Schicht: durchgehende Farbe, Deckung aus der Maske."""
    img = Image.new("RGBA", size, color)
    img.putalpha(mask)
    return img


def handle_rgba(w, h, edge, tube, color, eff, hot=False, prof=None):
    """Ein fertiges Griff-Bild als RGBA (PIL) – die Kapsel und ihr Bloom, sonst nichts.
    Alles ausserhalb bleibt unsichtbar (Deckung HIT_ALPHA, siehe dort): dort ist der
    Desktop zu sehen, kein Kasten.

    `prof` ist das Schwappen: je Bildzeile die Abweichung vom Ruhezustand
    (handle_wave.profile, siehe WAVE_* oben). Ohne prof – und mit einem Profil aus
    Nullen – entsteht Pixel fuer Pixel dasselbe Bild wie vorher; der ausgewaehlte
    Entwurf bleibt also der Nullpunkt, das Schwappen ist nur eine Auslenkung daraus."""
    W, H = _canon(w, h, edge)
    tube = max(2, min(int(tube), W))
    m = _masks(W, H, edge, tube, hot)
    wave = _wave_layers(m, W, H, edge, prof) if prof else None
    size = (w, h)
    flash = min(1.0, max(0.0, eff - 1.0))

    def tone(col):
        return _mix(col, "#ffffff", flash * FLASH_WHITE) if flash else col

    img = Image.new("RGBA", size, (0, 0, 0, 0))

    def put(mask, col):
        return Image.alpha_composite(img, _layer(size, tone(col), mask))

    def put_raw(mask, col):
        """Wie put, aber OHNE die Flash-Toenung: der Grundton, mit dem die Welle
        abdunkelt, darf beim Aufblitzen nicht mitweiss werden – sonst hellt gerade
        die dunkle Seite der Welle auf."""
        return Image.alpha_composite(img, _layer(size, col, mask))

    bloom_gain = (BLOOM_HOT if hot else 1.0) + flash * FLASH_BLOOM
    bloom_col = _lit(color, eff, BLOOM_FADE)
    # 1) Bloom rings um die Kapsel – im Schwappen je Zeile gedimmt, und wo die Welle
    #    hell ist, liegt eine zweite Schicht derselben Farbe darueber (siehe
    #    _wave_layers: eine Maske laesst sich nicht ueber ihren Wert hinaus anheben).
    img = put(_dim(wave["bloom"] if wave else m["bloom"], bloom_gain), bloom_col)
    if wave:
        img = put(_dim(wave["bloom_up"], bloom_gain), bloom_col)
    # 2) Kapselkoerper, in der Mitte heller (Laengs-Verlauf).
    body_col = _lit(color, eff, BODY_FADE)
    img = put(m["body"], _mix(body_col, "#ffffff", BODY_WHITE_HOT) if hot else body_col)
    if wave:                              # wo die Welle unten ist: Richtung Grundton
        img = put_raw(wave["dark"], GROUND)
    img = put(wave["warm"] if wave else m["warm"],
              _mix(color, "#ffffff", WARM_WHITE_HOT if hot else WARM_WHITE))
    if wave:                              # wo sie oben ist: Weissglut (Farbe ist aus)
        img = put_raw(wave["glow"], "#ffffff")
    # 3) Glaskante im Koerper, zur Dockkante hin.
    img = put(_dim(m["sheen"], SHEEN_HOT if hot else 1.0),
              _mix(color, "#ffffff", SHEEN_WHITE))
    # 4) Das Polster anfassbar halten (siehe HIT_ALPHA) – unsichtbar, aber nicht leer.
    if HIT_ALPHA > 0:
        r, g, b, a = img.split()
        img = Image.merge("RGBA", (r, g, b, a.point(
            lambda v: max(v, HIT_ALPHA))))
    return img


def _premultiplied_bgra(img):
    """RGBA (PIL) -> Bytes, wie UpdateLayeredWindow sie will: BGRA mit
    VORMULTIPLIZIERTEM Alpha. Die Multiplikation macht ImageChops.multiply exakt
    ((c*a)/255) – von Hand ueber Python-Schleifen waere es je Frame zu teuer."""
    r, g, b, a = img.split()
    return Image.merge("RGBA", (_mul(b, a), _mul(g, a), _mul(r, a), a)).tobytes()


def _qc(color, step=6):
    """Farbe grob rastern – haelt den Cache klein, waehrend der Griff in eine neue
    Statusfarbe fadet. Der Sprung liegt unter der Wahrnehmungsschwelle."""
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (r // step * step, g // step * step, b // step * step)


def _qe(eff, step=0.015):
    """Leuchtkraft rastern (dito, fuer den atmenden Griff).

    Der Schritt war bis 2026-07-28 mit 0.07 von den Kacheln uebernommen (card_render)
    – und dort ist er richtig, weil eine Kachel ihre Leuchtkraft ueber die ganze Spanne
    0..1 fahren laesst. Der GRIFF atmet aber nur zwischen 0.60 und 1.00: gemessen kamen
    dabei ganze SECHS verschiedene Bilder je Atemzug heraus, eine Stufe stand bis zu
    605 ms unveraendert. Das Atmen war also keine Rampe, sondern eine Treppe mit sechs
    Stufen – genau das, was man als hakelig sieht.

    0.015 gibt ueber denselben Weg ~27 Stufen, also praktisch je Frame ein neues Bild.
    Bezahlt ist das laengst: ein Bild neu zusammenzusetzen kostet gemessen 0,2 ms (die
    teuren MASKEN haengen nur an der Groesse und bleiben gecacht), der Puls tickt alle
    33 ms. Der Cache ist hier Bonus, nicht Notwendigkeit."""
    return round(max(0.0, min(3.0, eff)) / step) * step


def handle_bits(w, h, edge, tube, color, eff, hot=False, prof=None):
    """Wie handle_rgba, aber als fertige BGRA-Bytes fuer win_focus.layered_push –
    gecacht. Gibt None zurueck, wenn Pillow fehlt oder die Groesse unbrauchbar ist;
    dann bleibt der Linien-Pfad in edge_dock.

    Mit einem Wellen-Profil wird NICHT gecacht, und das ist Absicht: das Profil ist je
    Frame ein anderes, ein Eintrag traefe also nie wieder und wuerde nur den Cache
    ausspuelen, in dem die Ruhezustaende liegen. Was teuer ist, sind ohnehin die MASKEN
    (Kapselform, Gauss-Bloom, Glaskante) – die haengen weiter nur an der Groesse und
    bleiben gecacht.

    Uebrig bleibt das Zusammensetzen, und das kostet GEMESSEN 0,40 ms bei 100 % und
    0,81 ms bei 150 % (Griff 29x220 bzw. 44x330) – also 1,2 bzw. 2,4 % des 33-ms-Takts.
    Ein Ruhebild aus dem Cache kostet dagegen 0,004 ms. Die Zahl ist bewusst hier
    notiert, weil sie beim Umbau zunaechst mit „0,2 ms" ueberschaetzt wurde: das war die
    Messung fuer VIER Schichten ohne Profil, das Schwappen braucht sechs plus fuenf
    Zeilenmasken.

    Aus demselben Grund wird die Leuchtkraft hier nicht gerastert (_qe): das ist eine
    Cache-Massnahme, und ohne Cache kostet sie nur Auflösung in der Bewegung."""
    if not AVAILABLE or w < 4 or h < 4:
        return None
    if prof:
        return _premultiplied_bgra(handle_rgba(w, h, edge, int(tube), color, eff,
                                               hot=hot, prof=prof))
    key = (w, h, edge, int(tube), _qc(color), _qe(eff), bool(hot))
    hit = _bits_cache.get(key)
    if hit is not None:
        _bits_cache.move_to_end(key)
        return hit
    bits = _premultiplied_bgra(handle_rgba(w, h, edge, int(tube), key[4], key[5],
                                          hot=hot))
    _bits_cache[key] = bits
    _trim(_bits_cache, _BITS_MAX)
    return bits


def clear_cache():
    """Beide Caches leeren – nach einem Monitorwechsel (andere Groessen) oder in
    Tests."""
    _mask_cache.clear()
    _bits_cache.clear()
