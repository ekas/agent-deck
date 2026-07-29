"""Kachelflaeche + Halo als gerendertes Bild statt als Canvas-Polygon.

Warum ueberhaupt: Tk-Canvas kennt KEIN Antialiasing. Eine runde Ecke wird hart
gerastert, der Halo besteht aus drei gestuften Ringen – beides sieht man bei
echten Geraetepixeln sofort als Treppe. Pillow kann glaetten: die Form wird in
vierfacher Aufloesung gezeichnet und heruntergerechnet, der Halo ist ein echter
Gauss-Verlauf statt drei Stufen.

Der Trick gegen die Rechenlast (die Kacheln faden und atmen im 55-ms-Takt):
Die FORM haengt nur an der Groesse, nicht an der Farbe. Sie wird darum einmal je
Groesse gerendert und als drei Graustufen-MASKEN behalten:

    halo  – Deckung des Leucht-Hofs ringsum (weich auslaufend)
    body  – Deckung der Kachelflaeche (innen voll, an der Rundung weich)
    edge  – nur der Kantenring der Kachel

Pro Frame wird daraus nur noch zusammengesetzt: BG, Halo in Glowfarbe darauf,
Flaeche in Fuellfarbe darauf, Kante darauf. Das sind ein paar LUT- und
Composite-Operationen auf ~250x90 Pixeln – Bruchteile einer Millisekunde. Teuer
ist allein das Maskenrendern, und das passiert je Groesse genau einmal.

Zwei Caches:
  * _mask_cache  – Masken je (Breite, Hoehe, Radius, Rand); klein und langlebig.
  * _photo_cache – fertige Tk-Bilder je Farbkombination; Farben und Leuchtkraft
                   werden dafuer grob gerastert (_qc/_qe), sonst legte jeder
                   Zwischenschritt eines Farbverlaufs einen neuen Eintrag an.

WICHTIG fuer Aufrufer: Ein Tk-PhotoImage lebt nur, solange irgendwer es
referenziert – der Canvas selbst tut das NICHT. Wer ein Bild anzeigt, muss es
zusaetzlich im eigenen Zustand halten (das Deck legt es in den Kachel-Record),
sonst verschwindet die Kachel, sobald der Cache den Eintrag verdraengt.

Randfall TRANSPARENT_BG: das Bild traegt den Panel-Hintergrund als Grundton mit.
Ist die Fensterdurchsicht aktiv (config.TRANSPARENT_BG), wird genau diese Farbe
vom Fenster ausgestanzt – dann leuchtet der weiche Halo-Rand nicht gegen den
Desktop, sondern verschwindet mit ihm. Fuer diesen Fall bleibt der alte
Polygon-Weg der richtige (das Deck faellt dann selbst darauf zurueck).
"""
from collections import OrderedDict

from deck.render.kit import BG, hex_to_rgb

try:                                  # Pillow ist die einzige Nicht-Stdlib-Abhaengigkeit
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    AVAILABLE = True
except Exception:                     # ohne Pillow bleibt das Deck beim Polygon-Weg
    Image = ImageDraw = ImageFilter = ImageTk = None
    AVAILABLE = False

# Aufloesungs-Faktor beim Zeichnen der Form. 4x ist der Punkt, ab dem eine
# heruntergerechnete Rundung auf diesen Groessen nicht mehr von einer echten
# Vektorkante zu unterscheiden ist; 8x kostet das Vierfache und bringt nichts.
SS = 4
# Der Halo entsteht aus der Kachelform, die erst AUFGEBLASEN und dann weich-
# gezeichnet wird. Ohne das Aufblasen faengt der Verlauf direkt an der Kachelkante
# bei halber Deckung an und der Hof wirkt blass – die drei alten Ringe waren
# ringsum kraeftig (innerster Ring 70 % Vollfarbe) und genau das soll er treffen.
HALO_GROW = 0.35     # Anteil von pad, um den die Form vor dem Blur waechst
HALO_BLUR = 0.55     # Weichheit, ebenfalls als Anteil von pad
# Deckel der Halo-Deckung. Bewusst auf die Leuchtkraft des frueheren innersten
# Rings (70 % Vollfarbe) eingestellt: der Umbau soll die Kachel SCHAERFER machen,
# nicht heller – hier lieber nachjustieren als am Rest.
HALO_MAX = 0.75

_mask_cache = OrderedDict()      # (w,h,r,pad) -> (halo, body, edge)
_photo_cache = OrderedDict()     # Farbschluessel -> PhotoImage
_MASK_MAX, _PHOTO_MAX = 24, 160


def pad_for(scale):
    """Randstreifen (in Pixeln), den der Halo ausserhalb der Kachel braucht.
    Entspricht der Ausdehnung der frueheren drei Glow-Ringe (3 x 2 px), damit
    das Layout unveraendert bleibt – nur eben weich statt gestuft."""
    return max(3, int(round(6 * scale)))


def _trim(cache, limit):
    while len(cache) > limit:
        cache.popitem(last=False)


def _masks(w, h, r, pad):
    """Die drei Graustufen-Masken fuer diese Groesse (gecacht)."""
    key = (w, h, r, pad)
    hit = _mask_cache.get(key)
    if hit is not None:
        _mask_cache.move_to_end(key)
        return hit
    iw, ih = w + 2 * pad, h + 2 * pad
    k = SS
    box = [pad * k, pad * k, (pad + w) * k - 1, (pad + h) * k - 1]
    rad = max(1, r * k)

    # 1) Flaeche: volle Deckung innerhalb der Kachelform.
    big = Image.new("L", (iw * k, ih * k), 0)
    ImageDraw.Draw(big).rounded_rectangle(box, radius=rad, fill=255)
    body = big.resize((iw, ih), Image.LANCZOS)

    # 2) Halo: dieselbe Form, um HALO_GROW aufgeblasen und dann weichgezeichnet.
    #    Der Blur laeuft auf der grossen Fassung (sonst treppt der Verlauf selbst
    #    wieder). Aufgeblasen heisst: die Vollfarbe reicht noch ein Stueck ueber die
    #    Kachelkante hinaus und faellt erst danach ab – so kraeftig wie die
    #    frueheren Ringe, nur ohne deren Stufen.
    grow = pad * k * HALO_GROW
    halo_big = Image.new("L", big.size, 0)
    ImageDraw.Draw(halo_big).rounded_rectangle(
        [box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow],
        radius=rad + grow, fill=255)
    halo = halo_big.filter(ImageFilter.GaussianBlur(radius=pad * k * HALO_BLUR))
    halo = halo.resize((iw, ih), Image.LANCZOS)
    halo = halo.point(lambda v: int(v * HALO_MAX))

    # 3) Kante: Ring der Kachelform (aussen minus innen), damit die Umrandung
    #    dieselbe weiche Rundung bekommt wie die Flaeche.
    inner = Image.new("L", (iw * k, ih * k), 0)
    ImageDraw.Draw(inner).rounded_rectangle(
        [box[0] + k, box[1] + k, box[2] - k, box[3] - k],
        radius=max(1, rad - k), fill=255)
    edge = Image.new("L", (iw * k, ih * k), 0)
    ImageDraw.Draw(edge).rounded_rectangle(box, radius=rad, fill=255)
    edge.paste(0, (0, 0), inner)
    edge = edge.resize((iw, ih), Image.LANCZOS)

    out = (halo, body, edge)
    _mask_cache[key] = out
    _trim(_mask_cache, _MASK_MAX)
    return out


def _qc(color, step=6):
    """Farbe grob rastern – haelt den Bildcache klein, waehrend eine Flaeche in
    ihre Zielfarbe fadet. Der Sprung liegt unter der Wahrnehmungsschwelle."""
    r, g, b = hex_to_rgb(color)
    return "#%02x%02x%02x" % (r // step * step, g // step * step, b // step * step)


def _qe(eff, step=0.07):
    """Leuchtkraft rastern (dito, fuer den atmenden Halo)."""
    return round(max(0.0, min(3.0, eff)) / step) * step


def tile_image(w, h, r, pad, fill, glow, eff, border, border_w, bg=BG):
    """Ein fertiges Kachelbild (PIL, RGB) – Halo, Flaeche und Kante in einem."""
    halo, body, edge = _masks(w, h, r, pad)
    size = halo.size
    img = Image.new("RGB", size, bg)
    if eff > 0.01:
        lit = halo if eff >= 1.0 else halo.point(lambda v, e=eff: int(v * e))
        img = Image.composite(Image.new("RGB", size, glow), img, lit)
    img = Image.composite(Image.new("RGB", size, fill), img, body)
    if border_w > 1:
        # Dickere Kante (Auswahl/Rueckfrage): die 1-px-Maske reicht dafuer nicht,
        # also den Ring per Maximum mehrfach versetzt uebereinanderlegen – das
        # bleibt weich, statt eine harte zweite Linie zu zeichnen.
        from PIL import ImageChops
        thick = edge
        for off in range(1, int(border_w)):
            for dx, dy in ((off, 0), (-off, 0), (0, off), (0, -off)):
                thick = ImageChops.lighter(thick, ImageChops.offset(edge, dx, dy))
        edge = thick
    img = Image.composite(Image.new("RGB", size, border), img, edge)
    return img


def tile_photo(w, h, r, pad, fill, glow, eff, border, border_w=1, bg=BG):
    """Wie tile_image, aber als Tk-PhotoImage und gecacht.

    Der Aufrufer MUSS die zurueckgegebene Referenz halten (siehe Modul-Kopf).
    Gibt None zurueck, wenn Pillow fehlt – dann bleibt der Polygon-Weg."""
    if not AVAILABLE:
        return None
    key = (w, h, r, pad, _qc(fill), _qc(glow), _qe(eff), _qc(border),
           int(border_w), bg)
    hit = _photo_cache.get(key)
    if hit is not None:
        _photo_cache.move_to_end(key)
        return hit
    img = tile_image(w, h, r, pad, key[4], key[5], key[6], key[7],
                     int(border_w), bg)
    photo = ImageTk.PhotoImage(img)
    _photo_cache[key] = photo
    _trim(_photo_cache, _PHOTO_MAX)
    return photo


def clear_cache():
    """Beide Caches leeren – nach einem Monitorwechsel (andere Groessen) oder in
    Tests."""
    _mask_cache.clear()
    _photo_cache.clear()
