"""Masse, Farben und Takte des Randdocks - plus die Umrechnungen darauf.

Diese Werte und Funktionen lagen auf der Modulebene von controller.py. Sie stehen
hier, weil die dock-Mixins sie brauchen: wuerden sie aus controller.py importieren,
das die Mixins selbst einbindet, entstuende ein Zirkelbezug.

Merkregel fuer die Umrechnungen: Punkte sind Widget-Maass, Pixel sind Canvas-Maass.
"""

import os

from deck.domain import config as cfg
from deck.domain import paths as dp
from deck.platform import dpi
from deck.platform import focus as wf
from deck.render import capsule as hrender
from deck.render.kit import mix as _mix


# Hierhin schreibt _report_layer_failure, wenn der Bild-Pfad des Griffs aufgibt
# (neben panel.lock, also im State-Ordner des Decks). Siehe dort, warum es eine
# Datei sein muss und kein print.
LAYER_ERR_PATH = os.path.join(os.path.dirname(dp.STATE_DIR), "handle_layer.err")

# Gültige Ränder (interne Werte, wie in deck_settings.json 'dock_edge').
EDGES = ("off", "left", "right", "top")

# Dicke der NEONRÖHRE (px, quer zum Rand) – nicht die des Fensters, siehe
# HANDLE_PAD. 12 war die urspruengliche Breite und genau so schmal, dass darin kein
# Verlauf unterzubringen war; ueber 20 px faengt der eingeklappte Zustand an, wie ein
# zweites Fenster zu wirken.
HANDLE_THICK = 16
# Luft rings um die Roehre, in der ihr Leuchthof auslaufen kann. Diese Luft ist
# UNSICHTBAR: das Griff-Fenster traegt echtes Alpha je Pixel (handle_render +
# win_focus.layered_push), dort ist also der Desktop zu sehen und kein Kasten.
#
# Damit ist das Fenster dicker als die Roehre – und weil HANDLE_THICK vorher BEIDES
# war (Balkendicke UND das Mass, an dem die Slide-Animation ihren Startstreifen
# aufhaengt), laeuft alles Geometrische jetzt ueber handle_thick(). Nach AUSSEN
# braucht der Hof keinen Platz: dort ist der Bildschirmrand, die Roehre klebt daran.
HANDLE_PAD = 13
HANDLE_MIN_LEN = 90      # Mindestlänge des Griffs entlang des Rands
HANDLE_MAX_LEN = 220     # Höchstlänge (sehr hohe/breite Decks bekommen keinen Riesen-Griff)
# Die Zieh-Zone ist das unsichtbare POLSTER (HANDLE_PAD) neben der Kapsel – nicht mehr
# ein Stück in der Mitte der Länge. Das ergibt sich aus dem Entwurf: sichtbar ist nur
# die Kapsel, und alles daneben im Griff-Fenster ist Luft für ihren Leuchthof. Diese
# Luft ist zu nichts anderem zu gebrauchen und liegt über die ganze Länge an – also ist
# sie die Greif-Fläche, und die Kapsel bleibt vollständig fürs Aufklappen frei.
#
# Zwei Dinge hängen daran:
#   • Der Hover (Deck klappt auf) reagiert NUR auf der Kapsel. Steht der Zeiger im
#     Polster, passiert nichts – man will dort greifen, nicht öffnen.
#   • Das Polster muss trotzdem Mausereignisse bekommen. Beim layered Fenster folgt der
#     Hit-Test dem Alpha, vollständig durchsichtige Pixel klicken durch; darum liegt
#     dort ein unsichtbares Mindest-Alpha (handle_render.HIT_ALPHA).
#
# Die Grenze zwischen beiden Zonen ist handle_render.capsule_extent() – EINE Quelle,
# damit Zonengrenze und Bild nicht auseinanderlaufen.
INSIDE_MARGIN = 8        # Kulanz um das aufgeklappte Fenster, bevor „Zeiger draußen" gilt
POLL_MS = 70             # Takt des Zeiger-Polls (nur fürs Einklappen)
COLLAPSE_DELAY_MS = 320  # so lange muss der Zeiger draußen sein, bevor eingeklappt wird
# 0 = sofort aufklappen, sobald der Zeiger den Griff berührt. Eine Verzögerung ist
# nicht mehr nötig, weil das Greifen jetzt über die Zieh-Zone (Mitte) läuft und
# nicht mehr mit dem Aufklappen um dieselbe Fläche konkurriert. Der after()-Pfad
# bleibt (statt direktem reveal()), damit ein Zonenwechsel es noch abbestellen kann.
HOVER_REVEAL_MS = 0
# Von außen angestoßenes Aufklappen (reveal_for_request) hält so lange offen: dort
# steht der Zeiger NICHT auf dem Deck, der Einklapp-Poll würde sonst nach
# COLLAPSE_DELAY_MS sofort wieder zumachen. Lang genug, um hinzusehen und die Maus
# hinzuführen (ab Berührung übernimmt der normale Poll).
REQUEST_HOLD_MS = 4000
DRAG_THRESH = 4          # ab so vielen px gilt es als Ziehen (statt Klick)

# ── Bewegungsprofil: gedämpfte Feder statt fester Kurve ─────────────────
# Bis 2026-07-28 fuhren beide Richtungen dieselbe smoothstep-Kurve über eine feste
# Dauer. Das ist sauber, liest sich aber mechanisch: smoothstep ist symmetrisch und
# steht bei halber Zeit exakt auf halbem Weg – die Bewegung „verwaltet" sich, statt
# zu reagieren. Die Motion-Systeme machen es anders (Material 3 fährt Eingang und
# Ausgang mit VERSCHIEDENEN Kurven, `emphasized decelerate` rein / `emphasized
# accelerate` raus), und die Physik-basierten (SwiftUI, Framer) ersetzen die Kurve
# gleich ganz durch eine Feder.
#
# Hier steht jetzt eine KRITISCH GEDÄMPFTE Feder (Dämpfungsgrad genau 1). Drei
# Gründe, warum die für ein Randpanel besser passt als jede Kurve:
#   • Sie ist front-loaded: bei halber Bewegungszeit sind ~80 % des Wegs zurückgelegt
#     (smoothstep: 50 %). Das ist der Unterschied zwischen „reagiert" und „läuft ab".
#     Trotzdem startet sie aus dem Stand (Geschwindigkeit 0) – kein Sprung im ersten
#     Frame, an dem ein cubic-ease-out hier schon einmal gescheitert ist.
#   • Sie schwingt NIE über. Genau richtig so: Overshoot gehört zu Bewegungen, die
#     der Nutzer mit Schwung angestoßen hat (Wischen, Werfen). Ein Panel, das am
#     Bildschirmrand klebt und per Hover aufgeht, das über sein Ziel hinausschießt,
#     wirkt wackelig – die Design-Guides raten davon ausdrücklich ab.
#   • Sie hat einen ZUSTAND (Position + Geschwindigkeit) statt eines Fortschritts.
#     Beim Richtungswechsel mitten in der Bewegung wird nur das Ziel getauscht: das
#     Deck bremst aus voller Fahrt ab und kehrt um, statt seine Kurve rückwärts
#     abzuspulen. Das ist stetig in der GESCHWINDIGKEIT, nicht nur in der Position –
#     und die Umkehr dauert von selbst nur so lange, wie der Restweg hergibt.
#
# `response` ist die anschauliche Stellschraube (wie bei SwiftUI): die Eigenperiode
# der Feder. Kleiner = straffer/schneller. Aufklappen darf sich weicher setzen,
# Einklappen ist zügiger – Wegräumen soll nicht warten lassen.
#
# 190 ms ist so gewählt, dass die Bewegung nicht LÄNGER wirkt als die alte Kurve: sie
# hat nach 120 ms 90 % des Wegs hinter sich, exakt wie smoothstep über 170 ms. Der
# Unterschied liegt davor – nach 80 ms sind es 74 % statt 46 %. Die restlichen
# ~130 ms sind ein Ausrollen im einstelligen Pixelbereich, das man nicht als Warten
# liest, sondern als Weichheit. Größere Werte fühlen sich gemächlich an, kleinere
# kippen ins Schnippische.
REVEAL_RESPONSE_MS = 190
COLLAPSE_RESPONSE_MS = 150
# Näher als das am Ziel gilt als angekommen -> festsetzen. Eine Feder erreicht ihr
# Ziel nur asymptotisch; ohne diese Schwelle liefe der Timer noch hunderte Millisekunden
# für eine Bewegung weiter, die längst unter einem Pixel liegt.
SPRING_SETTLE_PX = 1.0
# ── Ziel-Takt der Animation: EIN Frame je Bild, das der Monitor zeigt ────
# Bis 2026-07-28 stand hier fest 10 ms (~100 Frames/s) mit der Begründung, etwas MEHR
# als die 60 Hz des Bildschirms zu rechnen sei sicherer – dann liege zu jedem
# Bildwechsel ein frischer Frame bereit. Gemessen ist das genau falsch, aus zwei
# Gründen, die sich addieren:
#
#   • 100 Frames auf 60 Bilder gehen nicht gleichmäßig auf. Von je fünf gerechneten
#     Frames werden drei gezeigt, im Muster 2-1-2-1-… Die ANGEZEIGTEN Positionen
#     liegen damit abwechselnd 10 und 20 ms Bewegung auseinander – bei diesem Weg
#     (~400 px) sind das abwechselnd ~45 und ~90 px pro Bild. Eine Bewegung, deren
#     Schrittweite je Bild zwischen einfach und doppelt springt, sieht man als
#     Stottern, obwohl jeder einzelne Frame zeitlich korrekt gerechnet ist.
#   • Das Verschieben des Fensters ist nicht gratis. Hier gemessen: ~8-9 ms je Schritt,
#     solange das Deck aus der Kante HEREINfährt (nach außen nur ~2-4 ms – Windows
#     muss die neu sichtbar werdende Fläche komponieren). Ein 10-ms-Takt hat davon
#     1 ms Luft, jeder Ausschlag lässt Frames platzen: die Abstände lagen zwischen
#     9,7 und 19,5 ms. Derselbe Weg mit weniger, dafür pünktlichen Frames läuft
#     sichtbar glatter – der Monitor kann die Extra-Frames ohnehin nicht zeigen.
#
# Also: Takt = Bildperiode, aus der tatsächlichen Rate des Monitors, unter dem das
# Deck liegt (60 Hz -> 16 ms, 120 Hz -> 8 ms, 144 Hz -> 6 ms). Abgerundet, damit ein
# leicht verspäteter Frame noch vor dem Bildwechsel ankommt.
#
# Damit der Takt überhaupt eingehalten werden KANN, hebt der Slide für seine Dauer
# die Windows-Timer-Auflösung an (wf.timer_precision_begin): sonst tickt Windows nur
# alle 15,6 ms und after(16) käme mal nach 15, mal nach 31 ms.
ANIM_TICK_FALLBACK_MS = 16     # 60 Hz, wenn die Rate nicht zu erfragen ist
ANIM_TICK_MIN_MS = 6           # darunter lohnt nichts: ein Move kostet mehr (s.o.)
ANIM_TICK_MAX_MS = 20          # Schutz gegen absurd gemeldete Raten (< 50 Hz)
_tick_ms = None                # gemerkt; apply_ui_scale() verwirft es (Monitorwechsel)
# Notbremse: dauert ein Slide länger als das, springt er ans Ziel und gilt als
# fertig. Lieber hart am Ziel als für immer auf halber Strecke – ein halb
# ausgefahrenes Deck ist der einzige Zustand, den es nicht geben darf. Großzügig
# bemessen: selbst eine bis zum Stillstand abgebremste und wieder umgekehrte Feder
# ist lange vorher da, hier greift also wirklich nur der Störfall.
ANIM_DEADLINE_MS = 900
# Nach dem Einklappen kurz kein Poll-Aufklappen (siehe _poll_reveal). Verhindert,
# dass ein Zeiger, der beim Einklappen zufällig über dem Griff liegt, das Deck im
# selben Atemzug wieder aufreißt. Gilt NUR für den Poll – ein bewusstes
# Hover-/Klick-Ereignis auf dem Griff greift immer sofort.
REVEAL_LOCK_MS = 220

HANDLE_BG = "#15151c"    # Griff-Grundton (wie die Frost-Titelleiste/Caption)
HANDLE_ACCENT = "#7ecbff"  # Cyan-Akzent (Ruhefarbe ohne gemeldeten Agenten-Status)
# Rahmenlos gibt es keine native DWM-Kante mehr → denselben Cyan-Rand wie das
# Standardfenster (style_titlebar border) selbst auf den Fensterrand zeichnen.
BORDER_COLOR = "#7ecbff"
BORDER_PX = 2
# Landung: wenn das Deck sein Ziel erreicht, leuchtet sein Rand kurz in der Farbe
# des Griffs auf und verblasst auf das Ruhe-Cyan. Das erzählt den Übergang zu Ende –
# der glühende Balken am Rand IST das Deck, und beim Ankommen gibt er seine Farbe
# ab. Nebenbei quittiert es das Ende der Bewegung, ohne dass die Bewegung selbst
# überschwingen müsste (was ein Randpanel nicht tun soll, siehe oben).
#
# Bewusst nur die Rahmenfarbe: das ist ein einzelnes configure() auf dem Fenster,
# kein Canvas-Neuzeichnen. Der Effekt läuft NACH dem Slide, konkurriert also mit
# keinem Frame. Bei ruhigem Deck (Griff-Cyan = Rand-Cyan) bliebe er unsichtbar,
# darum wird die Griff-Farbe zusätzlich Richtung Weiß aufgehellt.
BORDER_LAND_MS = 55        # Takt des Verblassens (wie der Kachel-Animator)
BORDER_LAND_FRAMES = 7     # ~390 ms bis zurück auf BORDER_COLOR
BORDER_LAND_WHITE = 0.35   # Weißanteil auf der Griff-Farbe im Moment der Landung
# Luft zwischen Bildschirmrand und aufgeklapptem Fenster. Klingt nach Kosmetik, ist
# aber der Grund, warum der Rand ueberhaupt rundum zu SEHEN ist: Windows 11 legt bei
# runden Ecken (siehe _round_corners) seinen eigenen grauen Rand ueber die aeusserste
# Pixelreihe des Fensters. Von BORDER_PX bleibt damit eine Reihe weniger uebrig – und
# lag das Fenster buendig am Rand, fiel genau die Kante an der Dockseite aus (drei
# Kanten mit Rand, die vierte ohne). Um EDGE_GAP eingerueckt liegt der graue
# DWM-Rand noch im Sichtbaren und die Cyan-Reihe liest sich an allen vier Kanten
# gleich. Der GRIFF bleibt buendig (er hat keinen Rand) – die Luft entsteht erst beim
# Aufklappen und ist so schmal wie der Rand selbst.
EDGE_GAP = 2
# Schrittweite der Beschneidung an der Kante (nur im Nachbarmonitor-Fall, siehe
# _clip_for). Jede Änderung der Fenster-Region kostet ein SetWindowRgn und damit ein
# Neuzeichnen des ganzen Fensters. Bewusst klein: auf dem schnellen Stück legt das
# Fenster ohnehin mehr als CLIP_QUANT pro Frame zurück, gespart wird also vor allem
# im Anlauf und im langen Ausrollen der Feder – dort fallen mehrere Frames auf
# dieselbe Stufe. Ein grober Wert würde mehr sparen, aber sichtbar zu viel vom
# herausfahrenden Streifen abschneiden.
CLIP_QUANT = 4

# ── Neon-Griff ───────────────────────────────────────────────────────────
# Die Farbe kommt vom Panel (set_glow) und trägt den dringlichsten Agenten-Status;
# ohne Agenten bleibt es beim Cyan-Akzent oben.
#
# Gezeichnet wird der Griff seit dem randlosen Entwurf als BILD (handle_render, aus
# Pillow) – die Tabelle hier beschreibt den RÜCKFALL, der greift, wenn Pillow fehlt:
# die alte „Röhre" aus drei deckungsgleichen Linien längs der Mitte, (Linienbreite px,
# Fade Richtung HANDLE_BG bei voller Intensität), breit+blass zuerst (Halo),
# schmal+kräftig zuletzt (Kern). Dieselbe Staffelung wie GLOW_RINGS bei den Kacheln,
# nur konzentrisch statt ringförmig. Genau ihre drei Stufen waren der Grund für den
# Umbau: Tk-Canvas kennt kein Antialiasing, also blieb der Verlauf eine Treppe.
#
# Die Breiten wandern mit HANDLE_THICK mit (bei 12 px waren es 10/6/2): der Halo lässt
# je Seite gut einen Pixel Grundfläche stehen, sonst wäre der Balken ein Farbstreifen
# ohne Röhren-Wirkung.
NEON_LAYERS = ((15, 0.84), (9, 0.58), (3, 0.0))
NEON_CORE_WHITE = 0.30   # Kern zusätzlich Richtung Weiß mischen -> Röhren-Look
NEON_HOT_WHITE = 0.65    # dito, solange der Zeiger auf dem Griff steht (Hover-Rückmeldung)
NEON_TINT = 0.14         # Grundfläche leicht in die Statusfarbe tauchen
# Mindest-Leuchtkraft: der Griff ist im eingeklappten Zustand die EINZIGE Bedienfläche
# und muss auch bei „idle" (Intensität 0.22) noch findbar/greifbar bleiben.
NEON_FLOOR = 0.45
# Frame-Takt des Pulses. War an den Kachel-Takt (cfg.ANIM_MS, 55 ms) gekoppelt mit dem
# Gedanken, alles im Deck solle im Gleichschritt faden. Das Argument trägt hier aber
# nicht: der Griff ist NUR sichtbar, wenn das Deck eingeklappt ist – dann ist keine
# einzige Kachel zu sehen, mit der er gleichlaufen könnte. 55 ms sind 18 Bilder/s, und
# damit springt die Helligkeit je Frame um ~5 von 255 Stufen. 33 ms (30 Bilder/s)
# halbieren den Sprung; ein Frame kostet gemessen 0,2 ms, das Atmen ist damit unter
# 1 % Rechenzeit – und es läuft ohnehin nur, solange der Griff zu sehen ist.
NEON_MS = 33
# Schwappen im Kern der Kapsel (handle_wave, Variante 09 der Fluid-Vorlage): das
# helle Mittelstück kippt zur einen Seite, zurück zur anderen, und kommt zur Ruhe –
# angestoßen alle handle_wave.PERIOD Sekunden UND bei jedem dringlicher werdenden
# Status (set_glow flash -> _wave_kick).
#
# EIN Schalter, weil es genau zwei Dinge gibt, die man daran ändern will: aus (dann
# ist der Griff Pixel für Pixel der alte) oder stärker/schwächer – letzteres über
# handle_render.WAVE_STRENGTH. Das ATMEN bleibt davon unberührt und läuft weiter;
# es ist eine Helligkeit über die ganze Röhre, das Schwappen eine Verteilung darin.
# Wem beides zusammen zu viel ist, schaltet hier das eine oder im Panel (GLOW_STYLE,
# Spalte `pulse`) das andere ab.
WAVE_ON = True
NEON_PULSE_TICKS = 70                                 # Ticks je Atemzug (~2,3 s bei 33 ms – wie vorher)
NEON_BLOOM = getattr(cfg, "BLOOM_ON_CHANGE", 0.90)    # Aufblitzen, wenn es dringlicher wird
# Abklingen dieses Aufblitzens JE FRAME – der Wert aus config gilt für dessen 55-ms-Takt.
# Bei feinerem Takt muss er angehoben werden, sonst ist der Blitz nach derselben Zahl
# Frames, aber in 40 % weniger ZEIT verbrannt. Umgerechnet über die Zeit: decay^(neu/alt).
NEON_DECAY = getattr(cfg, "BLOOM_DECAY", 0.82) ** (NEON_MS / float(getattr(cfg, "ANIM_MS", 55)))


# ── HiDPI ────────────────────────────────────────────────────────────────
# Alle Pixelmasse oben sind DESIGN-Einheiten (Mass bei 100 %). Seit das Deck
# DPI-aware zeichnet (dpi.py), sind Tk-Koordinaten echte Geraetepixel – ein
# 12-px-Griff waere auf einem 150-%-Schirm nur noch 8 px "gross" und kaum
# greifbar. Darum werden die Masse hier EINMAL beim Start und danach bei jedem
# Monitorwechsel umgerechnet.
#
# Umgerechnet wird in die Modul-Konstanten selbst (statt an ~20 Verwendungs-
# stellen), damit der Rest der Datei unveraendert lesbar bleibt. Die
# Ausgangswerte merkt sich _design beim ersten Lauf – ein zweiter Aufruf
# rechnet also wieder vom Design-Wert aus und kumuliert nicht.
_SCALED_PX = ("HANDLE_THICK", "HANDLE_PAD", "HANDLE_MIN_LEN", "HANDLE_MAX_LEN",
              "INSIDE_MARGIN", "DRAG_THRESH", "BORDER_PX", "EDGE_GAP",
              "CLIP_QUANT")
_design = {}


def scale_metrics():
    """Design-Masse -> Geraetepixel (siehe Kommentar oben). Idempotent."""
    g = globals()
    for name in _SCALED_PX:
        if name not in _design:
            _design[name] = g[name]
        g[name] = max(1, dpi.px(_design[name]))
    # Die Roehren-Tabelle traegt Linienbreiten in px (der Fade-Anteil bleibt).
    if "NEON_LAYERS" not in _design:
        _design["NEON_LAYERS"] = NEON_LAYERS
    g["NEON_LAYERS"] = tuple((max(1, dpi.px(lw)), fade)
                             for lw, fade in _design["NEON_LAYERS"])


def frame_tick_ms(hwnd=None):
    """Ziel-Abstand zweier Animations-Frames in ms = Bildperiode des Monitors, unter
    dem das Fenster liegt (ausführlich bei ANIM_TICK_FALLBACK_MS oben).

    Einmal erfragt und gemerkt: die Rate ändert sich nur beim Monitorwechsel, und
    genau dort verwirft apply_ui_scale() den Wert wieder."""
    global _tick_ms
    if _tick_ms is None:
        try:
            hz = float(wf.refresh_hz(hwnd))
            tick = int(1000.0 / hz) if hz > 1 else ANIM_TICK_FALLBACK_MS
        except Exception:
            tick = ANIM_TICK_FALLBACK_MS
        _tick_ms = int(max(ANIM_TICK_MIN_MS, min(ANIM_TICK_MAX_MS, tick)))
    return _tick_ms


def handle_thick():
    """Dicke des GRIFF-FENSTERS quer zum Rand: Röhre + Luft für ihren Leuchthof.

    Alles Geometrische rechnet damit – wo der Griff liegt (_handle_geom), wie weit das
    Deck beim Slide zurückgesetzt startet (_slide_off) und wie viel beim Beschneiden
    stehen bleiben darf. HANDLE_THICK allein wäre falsch: es ist nur noch das Maß der
    sichtbaren Kapsel, und das Fenster ist um HANDLE_PAD größer.

    Auch im Linien-Rückfall (kein Pillow) gilt dieselbe Dicke, obwohl es dort keinen
    Leuchthof gibt: das Polster IST die Zieh-Zone, und die darf nicht davon abhängen,
    ob eine Bibliothek installiert ist."""
    return HANDLE_THICK + HANDLE_PAD


def capsule_extent():
    """Bis hierher (px von der Dockkante quer nach innen) reicht die sichtbare Kapsel;
    dahinter liegt das unsichtbare Polster. Das ist die Grenze zwischen Aufklapp- und
    Greif-Zone. Der Wert kommt aus dem Renderer, damit Zone und Bild EINE Quelle haben;
    im Linien-Rückfall gilt derselbe Aufbau (siehe _draw_handle)."""
    return min(handle_thick() - 1, hrender.capsule_extent(HANDLE_THICK))


def neon_color(color, fade, eff, hot=False):
    """Farbe EINER Röhren-Schicht: von der Statusfarbe weich nach HANDLE_BG verblassen
    (`fade` = Ruhe-Anteil der Schicht), skaliert mit der Leuchtkraft `eff` – genau wie
    die Glow-Ringe der Kacheln. Die Kern-Schicht (fade 0) wird zusätzlich Richtung Weiß
    gemischt, unter dem Zeiger stärker (`hot`) -> Röhren-Look statt flacher Linie.
    eff > 1 (Aufblitzen) klemmt in mix() auf die Vollfarbe."""
    if fade <= 0:
        color = _mix(color, "#ffffff", NEON_HOT_WHITE if hot else NEON_CORE_WHITE)
    return _mix(color, HANDLE_BG, 1 - (1 - fade) * eff)


def neon_tint(color, eff):
    """Grundton der Griff-Fläche: HANDLE_BG leicht in die Statusfarbe getaucht (wie die
    Kartenflächen im Deck), damit der ganze Balken glüht und nicht nur die Linie."""
    return _mix(HANDLE_BG, color, NEON_TINT * min(eff, 1.0))
