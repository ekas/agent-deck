"""Das Schwappen im Kern der Griff-Kapsel: Variante 09 der Fluid-Vorlage.

Die Kapsel war innen still. Ihr Licht kam aus einem festen Längs-Verlauf (in der
Mitte 255, an den Enden 60, siehe handle_render._gradient) – nur die Helligkeit der
GANZEN Röhre atmete. Dieses Modul liefert die Bewegung DARIN: angestoßenes Wasser,
das zur einen Seite kippt, zurück zur anderen und langsam zur Ruhe kommt, bis der
nächste Stoß kommt.

Was es liefert, ist bewusst wenig: eine Liste von n Zahlen längs der Röhre, die
ABWEICHUNG vom heutigen Zustand. 0 heißt „genau wie bisher", negativ dunkler,
positiv heller. Damit bleibt der ausgewählte Entwurf der Nullpunkt – wer das
Schwappen abschaltet, bekommt Pixel für Pixel das alte Bild zurück (dafür gibt es
einen Test). Wie aus diesen Zahlen Licht wird, entscheidet handle_render; hier steht
keine Farbe und kein Pillow.

Physikalisch ist es die Grundmode eines schwappenden Behälters (eine Seiche): eine
gedämpfte Schwingung, deren Form über die Länge ein cos ist – eine Seite hoch, die
andere runter. Dazu eine zweite, schnellere Mode mit kleinerer Amplitude; erst sie
nimmt der Bewegung das Mechanische, weil beide Perioden nicht aufgehen.

ZWEI Dinge, die beim Einbau wichtig sind:

  • Die Zeit muss eine ECHTE Uhr sein (perf_counter), kein Frame-Zähler. Fällt ein
    Frame aus, soll die Schwingung im Takt bleiben und nicht stehenbleiben – genau
    daran ist die Slide-Animation einmal gescheitert (siehe edge_dock._now_ms).

  • Zwischen zwei Stößen steht das Wasser fast still. Diese Ruhephase ist nicht nur
    erlaubt, sie ist der Sinn der Sache: dort meldet quiet() „nichts zu sehen", und
    der Griff fällt auf seinen gecachten Ruhezustand zurück, statt Bilder zu rechnen,
    die sich nicht unterscheiden.

Der Anstoß kommt periodisch (PERIOD) – und zusätzlich von außen: ein Statuswechsel,
der dringlicher wird, setzt die Uhr zurück und stößt neu an. Damit sitzt die Bewegung
genau dort, wo das Deck heute kurz aufblitzt und verpufft (edge_dock.NEON_BLOOM).
"""
import math

# Zeit bis zum nächsten Anstoß. Die Schwingung ist nach ~4 s abgeklungen, danach
# steht das Wasser kurz still – dieser Atem zwischen den Stößen ist gewollt, ein
# dauerhaft schwappender Griff wäre Zappeln.
PERIOD = 5.6

# Grundmode: Frequenz (Hz) und Dämpfung (1/s). 0.62 Hz sind 1,6 s je Kippbewegung –
# langsam genug, dass das Auge folgt, schnell genug, dass es lebt.
MODE1_F, MODE1_DAMP = 0.62, 0.55

# Zweite Mode: doppelt so viele Knoten, schneller, kräftiger gedämpft und nur zu
# knapp der Hälfte beteiligt. Ohne sie sieht die Bewegung wie ein Metronom aus – und
# weil ihr Bauch in der MITTE der Röhre liegt (cos(2πu) ist dort maximal), ist sie es
# auch, die den Kern selbst bewegt und nicht nur die Enden kippen lässt.
#
# Ihre Phase ist 0, und das ist kein Detail: mit einem Versatz stünde die Mode im
# Moment des Anstoßes schon auf 32 % Ausschlag – die Welle würde aus dem Stand
# SPRINGEN. Gemessen waren das gut sechs Graustufen zwischen zwei Frames, also genau
# das Zucken, das eine flüssige Bewegung nicht haben darf.
MODE2_F, MODE2_DAMP, MODE2_AMP = 1.15, 0.90, 0.45

# Sanfter Ausklang am Zyklusende. Nach 5,6 s steht die Grundmode noch auf knapp 5 %:
# ohne dieses Ausblenden bräche die Bewegung dort ab und begänne bei 0 neu – wieder
# ein Sprung, nur am anderen Ende. Über die letzten FADE_OUT Sekunden geht die
# Amplitude stetig auf 0, und erst DADURCH gibt es überhaupt eine Ruhephase, in der
# quiet() greifen kann.
FADE_OUT = 0.6

GAIN = 1.35              # Gesamt-Ausschlag, bevor geklemmt wird
LO, HI = -1.0, 1.2       # Grenzen von m: nach unten bis Grundton, nach oben etwas Luft

# Unterhalb dieses Ausschlags ist die Bewegung nicht mehr zu sehen, und dann ist es
# billiger UND ruhiger, gar nichts zu rechnen. Die Schranke ist nicht geraten: der
# stärkste Hebel dunkelt den Körper um m·WAVE_DARK·WAVE_STRENGTH gegen den Grundton
# ab, und zwischen Statusfarbe und Grundton liegen rund 200 Stufen – für weniger als
# eine Stufe muss m also unter 1/(0.62·0.85·200) ≈ 0.0095 liegen. Wer hier höher geht,
# baut sich einen sichtbaren Sprung an der Stelle, wo das Profil wegfällt.
QUIET = 0.01

_shape_cache = {}        # n -> (cos(pi*u), cos(2*pi*u)) je Stützstelle


def _shapes(n):
    """Die beiden Modenformen über die Länge. Sie hängen nur an der Zahl der
    Stützstellen, nicht an der Zeit – also einmal rechnen und behalten. Ohne das
    stünden hier 2n Kosinusse je Frame."""
    hit = _shape_cache.get(n)
    if hit is None:
        one, two = [], []
        for i in range(n):
            u = (i + 0.5) / n
            one.append(math.cos(math.pi * u))
            two.append(math.cos(2.0 * math.pi * u))
        hit = (one, two)
        if len(_shape_cache) > 8:            # zwei Kanten x HiDPI reichen dicke
            _shape_cache.clear()
        _shape_cache[n] = hit
    return hit


def amplitudes(t):
    """Ausschlag der beiden Moden zum Zeitpunkt t (Sekunden, beliebiger Nullpunkt).

    Beide sind gedämpfte Sinusse ab dem letzten Anstoß. Weil der Anstoß periodisch
    wiederkehrt, zählt nur die Position im Zyklus – und damit ist die ganze Bewegung
    eine reine Funktion der Zeit: keine Simulation, kein Zustand, kein Nachrechnen
    nach einer Pause."""
    ph = math.fmod(t, PERIOD)
    if ph < 0:
        ph += PERIOD
    fade = 1.0
    if ph > PERIOD - FADE_OUT:                   # stetig auf 0, siehe FADE_OUT
        fade = (PERIOD - ph) / FADE_OUT
    a = fade * math.exp(-MODE1_DAMP * ph) * math.sin(2.0 * math.pi * MODE1_F * ph)
    b = (fade * MODE2_AMP * math.exp(-MODE2_DAMP * ph)
         * math.sin(2.0 * math.pi * MODE2_F * ph))
    return a, b


def quiet(t):
    """Steht das Wasser gerade praktisch still? Dann braucht der Griff kein neues
    Bild – er nimmt seinen gecachten Ruhezustand, und das ist derselbe, den er ohne
    dieses Modul hätte."""
    a, b = amplitudes(t)
    return GAIN * (abs(a) + abs(b)) < QUIET     # b traegt MODE2_AMP schon in sich


def profile(n, t):
    """n Werte längs der Röhre: die Abweichung m vom heutigen Zustand, in [LO, HI].

    n ist die LÄNGE des Griff-Bildes in Pixeln (bei HiDPI also mehr) – das Profil
    wird nirgends interpoliert, sondern direkt in dieser Auflösung gerechnet; es
    kostet nur eine Multiplikation je Wert."""
    a, b = amplitudes(t)
    one, two = _shapes(n)
    ga, gb = GAIN * a, GAIN * b
    out = []
    for i in range(n):
        v = ga * one[i] + gb * two[i]
        out.append(LO if v < LO else (HI if v > HI else v))
    return out
