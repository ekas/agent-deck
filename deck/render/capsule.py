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

from typing import Any

# Masse und Masken liegen in capsule_masks - dieses Modul setzt daraus die Schichten
# zusammen und faerbt sie ein.
from deck.render.capsule_masks import (
    _BITS_MAX,
    BLOOM_FADE,
    BLOOM_HOT,
    BODY_FADE,
    BODY_WHITE_HOT,
    FLASH_BLOOM,
    FLASH_WHITE,
    GROUND,
    HIT_ALPHA,
    SHEEN_HOT,
    SHEEN_WHITE,
    WARM_WHITE,
    WARM_WHITE_HOT,
    _bits_cache,
    _canon,
    _mask_cache,
    _masks,
    _mul,
    _trim,
    _wave_layers,
)
from deck.render.kit import mix as _mix

try:                                  # Pillow ist die einzige Nicht-Stdlib-Abhaengigkeit
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    AVAILABLE = True
except Exception:                     # ohne Pillow bleibt der Linien-Pfad
    # Ohne Pillow bleibt der Linien-Pfad; die Namen muessen aber existieren.
    Image = ImageChops = ImageDraw = ImageFilter = None  # type: ignore[assignment]
    AVAILABLE = False

def _lit(color: str, eff: float, fade: float, bg: str | None = None) -> str:
    """Eine Schicht von der Statusfarbe Richtung Grundton verblassen, skaliert mit der
    Leuchtkraft – genau die Rechnung der Vorlage (und von edge_dock.neon_color), damit
    idle, arbeitet, ungelesen und Rueckfrage dieselben Helligkeiten treffen wie im
    ausgewaehlten Entwurf."""
    return _mix(color, bg or GROUND, 1 - (1 - fade) * min(eff, 1.0))


def _dim(mask: Any, factor: float) -> Any:
    """Deckung einer Schicht skalieren (fuer den aufziehenden Bloom beim Aufblitzen)."""
    f = max(0.0, factor)
    if 0.999 <= f <= 1.001:
        return mask
    return mask.point(lambda v: min(255, int(v * f)))


def _layer(size: Any, color: str, mask: Any) -> Any:
    """Eine RGBA-Schicht: durchgehende Farbe, Deckung aus der Maske."""
    img = Image.new("RGBA", size, color)
    img.putalpha(mask)
    return img


def handle_rgba(w: int, h: int, edge: str, tube: int, color: str, eff: float,
                hot: bool = False, prof: list[float] | None = None) -> Any:
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

    def tone(col: str) -> str:
        return _mix(col, "#ffffff", flash * FLASH_WHITE) if flash else col

    img = Image.new("RGBA", size, (0, 0, 0, 0))

    def put(mask: Any, col: str) -> Any:
        return Image.alpha_composite(img, _layer(size, tone(col), mask))

    def put_raw(mask: Any, col: str) -> Any:
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


def _premultiplied_bgra(img: Any) -> bytes:
    """RGBA (PIL) -> Bytes, wie UpdateLayeredWindow sie will: BGRA mit
    VORMULTIPLIZIERTEM Alpha. Die Multiplikation macht ImageChops.multiply exakt
    ((c*a)/255) – von Hand ueber Python-Schleifen waere es je Frame zu teuer."""
    r, g, b, a = img.split()
    return Image.merge("RGBA", (_mul(b, a), _mul(g, a), _mul(r, a), a)).tobytes()


def _qc(color: str, step: int = 6) -> str:
    """Farbe grob rastern – haelt den Cache klein, waehrend der Griff in eine neue
    Statusfarbe fadet. Der Sprung liegt unter der Wahrnehmungsschwelle."""
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return f"#{r // step * step:02x}{g // step * step:02x}{b // step * step:02x}"


def _qe(eff: float, step: float = 0.015) -> float:
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


def handle_bits(w: int, h: int, edge: str, tube: int, color: str, eff: float,
                hot: bool = False, prof: list[float] | None = None) -> bytes | None:
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


def clear_cache() -> None:
    """Beide Caches leeren – nach einem Monitorwechsel (andere Groessen) oder in
    Tests."""
    _mask_cache.clear()
    _bits_cache.clear()
