"""Agent Deck – am Bildschirmrand andocken + Auto-Hide (Griff-Balken).

Dockt das Hauptfenster an einen Bildschirmrand (links / rechts / oben). Im
angedockten Zustand verschwindet das Fenster komplett und hinterlässt nur einen
schmalen Griff-Balken bündig am Rand. Fährt der Zeiger über den Griff, taucht das
ganze Deck sofort wieder auf; verlässt der Zeiger das Fenster, klappt es nach
kurzer Verzögerung wieder auf den Griff ein.

Am Griff lässt sich das Deck ENTLANG des Rands verschieben (Ziehen); die Position
wird in deck_settings.json ('dock_along') gemerkt und übersteht den Neustart.
Zieh-Zone ist das unsichtbare POLSTER neben der Kapsel (siehe HANDLE_PAD): dort klappt
der Hover bewusst NICHT auf, sonst wäre der Griff im Moment des Greifens schon weg
(reveal() versteckt ihn) und es gäbe nichts mehr zu fassen – angedockt hat das Fenster
keine Titelleiste. Die Kapsel selbst bleibt vollständig fürs Aufklappen frei.

Entwurfsentscheidungen:
  • Reine Tk-Koordinaten (winfo_pointer*, winfo_screen*, geometry) – alle im
    selben Pixel-Raum, daher DPI-sicher (kein Mischen mit physischen Win32-Pixeln).
  • Reveal/Collapse GLEITEN quer zum Rand herein/heraus – NIE per -alpha-Fade: ein
    Fade auf einem overrideredirect-Fenster flackert unter Windows und der Poll-Loop
    würde ihn dauernd neu starten (Strobo). Animiert wird ausschliesslich die
    POSITION bei fester Groesse; eine Groessen-Animation würde den Canvas-Inhalt je
    Frame neu umbrechen (Reflow-Flackern). Das Fenster startet genau dort, wo der
    Griff sass (HANDLE_THICK ragt hervor) und faehrt heraus, der Griff wird also
    zum Deck – und gibt beim Ankommen seine Statusfarbe an dessen Rand ab
    (BORDER_LAND_*). Bewegt wird nicht entlang einer Kurve, sondern per gedaempfter
    FEDER (_spring_at, kritisch gedaempft): front-loaded statt symmetrisch, ohne
    Ueberschwingen, und beim Richtungswechsel wird nur das Ziel getauscht – Position
    und Geschwindigkeit laufen weiter. Ausfuehrlich bei REVEAL_RESPONSE_MS.
    Reihenfolge gegen Loecher am Rand: beim Aufklappen erst das Fenster an der
    Startposition einblenden, DANN den Griff verstecken – beim Einklappen erst den
    Griff zeigen, DANN das Fenster wegnehmen.
  • Der Slide verlaesst sich darauf, dass der ueber die Kante geschobene Teil
    abgeschnitten wird. Das tut aber nur ein ECHTER Bildschirmrand: liegt jenseits
    der Kante ein zweiter Monitor (Deck links am Primaerschirm, Nachbar links
    daneben), taucht der „versteckte" Teil dort auf und fliegt als Geisterbild mit –
    es sieht aus, als liefe die Animation doppelt. Darum schneiden wir ihn dann
    selbst weg (_apply_clip -> win_focus.clip_window). Nur dann: eine Fenster-Region
    ersetzt die weiche DWM-Rundung durch harte Ecken.
  • Der Slide muss ROBUST sein, nicht nur schön: ein halb ausgefahrenes Deck ist der
    eine Zustand, den es nicht geben darf (man sieht nichts und kommt an nichts mehr
    heran – angedockt gibt es keine Titelleiste). Vier Vorkehrungen dagegen, jede
    gegen einen real beobachteten Weg dorthin:
      – Notbremse (deadline): dauert ein Slide ungewoehnlich lange, springt er ans
        Ziel, statt auf halber Strecke zu warten.
      – Ein einziger Ausgang (_anim_finish): fertig, Notbremse und Fehler beim
        Bewegen laufen alle dort durch. Früher endete der Fehlerfall stillschweigend
        mitten in der Bewegung.
      – Watchdog im Poll: verschluckt Tk den eingeplanten Frame (modaler Dialog,
        fremdes update()), käme nie wieder einer – der Poll bemerkt es als einziger.
      – Nachmessen (_settle_expanded): ein geometry() ist nur eine ANFORDERUNG. Fiel
        der letzte Frame mit einem Resize oder deiconify zusammen, kam sie nie an und
        das Deck stand zu weit über der Kante („klappt nicht ganz aus").
  • Damit die Bewegung GLEICHMÄSSIG läuft, zählt weniger die Kurve als der Takt.
    Vier Dinge haben ihn zerhackt: Windows tickt Timer standardmäßig nur alle 15,6 ms
    (dagegen wf.timer_precision_begin für die Dauer des Slides), Tks `clock
    milliseconds` hat dieselbe Granularität und liefert abwechselnd 0 und 16 ms
    Fortschritt (dagegen perf_counter), der Kachel-Animator rendert im selben Thread
    ganze Bilder (dagegen anim.pause für die Dauer des Slides) – und der ZIELTAKT war
    selbst das Problem: 100 Frames/s auf einem 60-Hz-Schirm gehen nicht auf, also
    sprang die Schrittweite je angezeigtem Bild zwischen einfach und doppelt. Jetzt
    ist der Takt die Bildperiode des Monitors (frame_tick_ms). Dazu: pro Frame nur
    noch die Position setzen, nie die Größe – ein „WxH+X+Y" schickt Tk je Frame durch
    seinen Geometry-Manager.
  • Einklappen steuert der Poll-Loop (Zeiger verlässt das Fenster-Rechteck).
    Aufklappen läuft über die Events des Griff-Fensters UND – als zweiter, davon
    unabhängiger Weg – ebenfalls über den Poll: der Griff geht bei jedem Ein-/
    Ausklappen durch withdraw/deiconify, und taucht er unter einem STEHENDEN Zeiger
    auf, schickt Windows kein Mausereignis. Tk feuert dann weder <Enter> noch
    <Motion>, und früher tat sich schlicht nichts. Der Poll-Weg spart die Zieh-Zone
    aus und ist nach dem Einklappen kurz gesperrt, damit er nicht mit dem Ziehen
    kollidiert und nicht sofort wieder aufreißt.
  • Angedockt ist das Fenster rahmenlos (overrideredirect) → sauberer Slab am Rand.
    Buendig KLEBT es dort aber nicht: es bleibt EDGE_GAP weg, sonst ist der selbst
    gezeichnete Cyan-Rand an genau der Dockkante nicht zu sehen (siehe EDGE_GAP).
    Beim Lösen kehrt die native (Frost-)Titelleiste zurück.
  • Der Griff ist eine NEON-RÖHRE in der Farbe des dringlichsten Agenten-Status
    (set_glow, gefüttert vom Panel): amber = Rückfrage, grün = ungelesen, blau =
    arbeitet, grau = alle idle. Gezeichnet wird nur bei Größenänderung; die Farbe
    läuft ausschließlich über itemconfig – ein delete('all') je Frame flimmert am
    Rand. Der Puls-Timer läuft nur, solange der Griff sichtbar ist (= eingeklappt).

Fenster-Größe folgt weiter dem Inhalt (AgentDeck._fit_slim_window); on_resized()
rückt ein aufgeklapptes Fenster danach wieder an den Rand.

Schließen im angedockten Zustand: es gibt keine Titelleiste/kein ✕. Zum Beenden
im ⚙-Dialog „Am Rand andocken" auf „Aus" stellen – dann ist die Titelleiste
(inkl. Schließen) wieder da.
"""
import math
import os
import sys
import time
import tkinter as tk

from deck.domain import config as cfg
from deck.domain import paths as dp
from deck.render import capsule as hrender
from deck.render import fluid as hwave
from deck.platform import dpi
from deck.platform import focus as wf
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


class EdgeDock:
    """Andock-/Auto-Hide-Zustandsmaschine für das AgentDeck-Hauptfenster.

    Öffentliche API (vom Deck genutzt):
      apply_initial()   – beim Start den gespeicherten Rand anwenden
      set_edge(edge)    – Rand live setzen/wechseln/lösen (persistiert selbst)
      current_edge()    – aktueller Rand ("off" = schwebend)
      sliding()         – gleitet gerade (der Poll des Panels setzt dann aus)
      on_resized()      – nach Inhalts-Resize das aufgeklappte Fenster nachrücken
      set_glow()        – Neon-Farbe des Griffs (dringlichster Agenten-Status)
      reveal_for_request() – von außen aufklappen (Zweitstart-Wunsch), hält kurz offen
    """

    # Zustand des Schwappens, als KLASSEN-Voreinstellung: er ist optional (ohne ihn
    # zeichnet der Griff seinen Ruhezustand), und _paint_layered wird auch von außen
    # geprüft, ohne dass ein ganzes Dock gebaut wird. Ein fehlendes Attribut darf dort
    # kein AttributeError werden – die Erklärung zu beiden steht in __init__.
    _wave_t0 = None
    _last_bits = None

    def __init__(self, app):
        scale_metrics()               # Griffmasse in Geraetepixel (HiDPI, siehe oben)
        self.app = app
        self.root = app.root
        self.edge = "off"
        self.expanded = False
        self.handle = None            # tk.Toplevel des Griffs (None solange schwebend)
        self.handle_canvas = None
        self._poll_job = None
        self._reveal_job = None       # geplantes Hover-Aufklappen (verzögert)
        self._drag = None             # laufendes Griff-Ziehen (sonst None)
        self._outside_since = None    # ts (ms), seit wann der Zeiger das Fenster verließ
        self._hold_until = 0          # bis zu diesem ts (ms) NICHT einklappen (reveal_for_request)
        self._anchor = None           # (x, y) der Fenster-Ecke (freie Achse = Position am Rand)
        self._last_size = (0, 0)      # zuletzt am Rand gesetzte (W, H)
        self._handle_drawn = (0, 0)   # zuletzt gezeichnete Griff-Größe (spart Neuzeichnen beim Ziehen)
        self._anim = None             # laufender Slide (None = steht still)
        self._slide_target = None     # (x, y, w, h) des aufgeklappten Fensters (EDGE_GAP vom Rand)
        self._retarget = False        # Inhalt änderte sich MITTEN im Slide -> danach nachziehen
        self._reveal_lock = 0         # bis zu diesem ts (ms) kein Poll-Aufklappen (Anti-Flatter)
        self._land_job = None         # laufendes Nachleuchten des Rands (Landung)
        self._land_i = 0              # verbleibende Frames dieses Nachleuchtens
        self._land_color = BORDER_COLOR
        self._clip_on = False         # jenseits der Kante liegt ein Monitor -> beschneiden
        self._clip_px = 0             # derzeit weggeschnittene Breite (0 = keine Region)
        # Neon-Zustand des Griffs (Farbe kommt per set_glow vom Panel).
        self._layered = False         # Griff-Fenster trägt Alpha je Pixel (dann kein Canvas)
        self._handle_hwnd = None      # HWND des Griff-Fensters (für layered_push)
        self._img_size = (0, 0)       # Größe, für die das Bild gerendert wird
        self._neon = []               # Rückfall: Canvas-IDs der Röhren-Schichten (Halo -> Kern)
        self._grip_hot = False        # Zeiger steht im Polster (Zieh-Zone)
        self._glow_color = HANDLE_ACCENT
        self._glow_int = 0.0          # Ruhe-Intensität (0 = kein Agent -> nur NEON_FLOOR)
        self._glow_pulse = False      # atmet (Rückfrage/denkt)
        self._bloom = 0.0             # kurzes Aufblitzen bei dringlicherem Status
        self._pulse_i = 0             # Zähler des Atem-Zyklus
        self._glow_job = None         # laufender Puls-Timer (nur wenn der Griff sichtbar ist)
        self._handle_shown = False    # Griff gerade sichtbar (= eingeklappt)
        self._hot = False             # Zeiger steht auf dem Griff
        # Schwappen im Kern (handle_wave): Zeitpunkt des letzten Anstoßes. Eine ECHTE
        # Uhr, kein Frame-Zähler – fällt ein Frame aus, soll die Schwingung im Takt
        # bleiben und nicht stehen (dieselbe Lehre wie bei der Slide-Animation,
        # siehe _now_ms). None heißt „noch nie angestoßen".
        self._wave_t0 = None
        self._last_bits = None        # zuletzt geschobenes Bild (nicht zweimal pushen)

    # ── öffentlich ──────────────────────────────────────────
    def current_edge(self):
        return self.edge

    def sliding(self):
        """Gleitet das Deck gerade herein oder heraus?

        Das Panel fragt danach, um seinen Poll für die Dauer der Bewegung auszusetzen
        (siehe AgentDeck.refresh): der läuft im selben Thread und würde ihr Frames
        wegnehmen – dasselbe Argument, mit dem _anim_hold den Kachel-Animator anhält."""
        return self._anim is not None

    def apply_initial(self):
        """Beim Start den in den Settings gespeicherten Rand anwenden. Ist ein Rand
        gesetzt, wird sofort auf den Griff eingeklappt."""
        edge = self._norm(self.app.settings.get("dock_edge", "off"))
        if edge == "off":
            return
        self.edge = edge
        self._capture_anchor()
        self._apply_saved_along()
        self._enter_frameless()
        self._ensure_handle()
        self._collapse_now()           # beim Start ohne Slide (nichts ist sichtbar)
        self._start_poll()

    def set_edge(self, edge):
        """Rand live setzen (aus dem ⚙-Dialog). Persistiert 'dock_edge' selbst."""
        edge = self._norm(edge)
        if edge == self.edge:
            return
        old = self.edge
        self.edge = edge
        self.app.settings["dock_edge"] = edge
        self._save_settings()

        if edge == "off":
            self._undock()
            return

        if old == "off":
            # aus dem schwebenden Fenster heraus andocken: es ist gerade sichtbar →
            # als „aufgeklappt" übernehmen, der Poll klappt beim Verlassen ein.
            self._capture_anchor()
            self._apply_saved_along()
            self._persist_along()          # Position gleich festhalten
            self._enter_frameless()
            self._ensure_handle()
            self.expanded = True
            self._outside_since = None
            self._reposition_expanded()
            self._position_handle()        # Griff schon mal passend (noch versteckt)
            self._start_poll()
        else:
            # Rand→Rand: rahmenlos bleibt, nur neu ausrichten. Ein laufender Slide
            # gehört zum ALTEN Rand und muss weg – ihn aber nur abzubrechen ließe das
            # Fenster auf halber Strecke stehen, und zwar OHNE Griff (den hat reveal()
            # beim Losfahren versteckt): das Deck wäre weder zu sehen noch
            # hervorzuholen. Also einen definierten Zustand herstellen – war es am
            # Aufklappen, gilt es als offen, sonst als zu.
            if self._anim is not None:
                opening = self._anim["dir"] > 0
                self._anim_cancel()
                self.expanded = opening
            self._clear_clip()
            self._slide_target = None
            self._retarget = False
            if self.expanded:
                self._reposition_expanded()
                self._position_handle()
            else:
                self._collapse_now()       # setzt Griff + versteckt das Fenster

    def on_resized(self):
        """Vom Deck nach _fit_slim_window gerufen: wächst/schrumpft der Inhalt, das
        aufgeklappte Fenster wieder an den Rand rücken (EDGE_GAP davor)."""
        if self.edge == "off":
            return
        if self._anim is not None:
            # Waehrend des Gleitens NICHT umsteuern. Das Ziel mitten in der Bewegung
            # zu verschieben laesst das Deck sichtbar springen: Start- und Endpunkt
            # des Slides haengen beide daran, und ein Inhalts-Resize (neuer Agent)
            # aendert sie oft um dreistellige Pixelwerte. Nur merken – nachgezogen
            # wird, sobald der Slide durch ist (_anim_done).
            self._retarget = True
            return
        if self.expanded:
            self._reposition_expanded()

    def apply_ui_scale(self):
        """Monitorwechsel: die Griffmasse neu in Geraetepixel umrechnen und den
        Griff mit den neuen Massen neu setzen. Das Panel ruft das aus
        _sync_ui_scale; das aufgeklappte Fenster ruecken auf_resized/_reposition
        ohnehin nach."""
        global _tick_ms
        scale_metrics()
        self._handle_drawn = (0, 0)      # erzwingt ein Neuzeichnen mit neuer Groesse
        _tick_ms = None                  # anderer Monitor -> ggf. andere Bildrate
        hrender.clear_cache()            # Masken/Bilder gelten fuer die ALTEN Masse
        if self._anim is not None:
            # Mitten im Slide aendern sich gerade die Masse, aus denen er seine
            # Start- und Endposition rechnet. Nicht umsteuern (das spraenge sichtbar),
            # sondern danach neu ausrichten – wie beim Inhalts-Resize.
            self._retarget = True
        if self.edge != "off" and self.handle is not None:
            self._position_handle()      # setzt Geometrie + zeichnet die Roehre neu

    def set_glow(self, color, intensity=1.0, pulse=False, flash=False):
        """Neon-Farbe des Griff-Balkens setzen (vom Panel je Poll gerufen, siehe
        AgentDeck._update_dock_glow). `intensity` 0..1 = Ruhe-Leuchtkraft, `pulse` =
        atmen, `flash` = kurz aufblitzen (nur sinnvoll, wenn der Zustand DRINGLICHER
        wird – die Entscheidung trifft das Panel, das die Status-Semantik kennt).

        Absichtlich farb-, nicht statusbasiert: die Statusfarben leben zentral in
        GLOW_STYLE/LOST_GLOW im Panel; ein Import von dort wäre ein Zyklus."""
        if intensity <= 0:                       # kein Agent -> neutraler Cyan-Griff
            color, intensity, pulse = HANDLE_ACCENT, 0.0, False
        if (color, intensity, pulse) == (self._glow_color, self._glow_int, self._glow_pulse):
            return                               # nichts Neues -> kein Neuzeichnen
        self._glow_color = color
        self._glow_int = intensity
        self._glow_pulse = pulse
        # Nur aufblitzen, wenn der Griff gerade zu SEHEN ist: aufgeklappt bliebe der
        # Bloom sonst stehen und würde beim nächsten Einklappen verspätet abbrennen.
        # Dasselbe Ereignis stößt den Kern neu an – der Blitz sagt „jetzt", die Welle
        # danach sagt „gerade passiert" (siehe _wave_kick).
        if flash and self._handle_shown:
            self._bloom = NEON_BLOOM
            self._wave_kick()
        self._paint_handle()
        self._start_glow()

    # ── rahmenlos an/aus ────────────────────────────────────
    def _enter_frameless(self):
        try:
            self.root.overrideredirect(True)
        except tk.TclError:
            pass
        # Cyan-Rand selbst zeichnen (ersetzt die weggefallene native DWM-Kante).
        # highlightbackground UND highlightcolor gesetzt → sichtbar mit/ohne Fokus.
        try:
            self.root.configure(highlightthickness=BORDER_PX,
                                highlightbackground=BORDER_COLOR,
                                highlightcolor=BORDER_COLOR)
        except tk.TclError:
            pass
        self._refresh_hwnd()
        self._round_corners()
        self._reassert_topmost()

    def _round_corners(self):
        """Leicht runde Ecken auch im rahmenlosen Zustand (per DWM, weich gerendert).
        Ohne native Titelleiste kaeme sonst ein hart eckiger Slab heraus.

        Wird zusaetzlich bei jedem Aufklappen gesetzt: der Aufruf ist billig und
        idempotent, und so ist die Rundung selbst dann da, wenn Tk das HWND
        zwischenzeitlich neu erzeugt hat (dabei gehen DWM-Attribute verloren)."""
        try:
            wf.round_corners(self.app.my_hwnd, small=True)
        except Exception:
            pass

    def _undock(self):
        """Zurück in den schwebenden Zustand: Griff weg, Rahmen + native Titelleiste
        zurück, an die gemerkte Position stellen."""
        self._stop_poll()
        self._cancel_reveal()
        self._anim_cancel()
        self._cancel_border_flash()
        self._slide_target = None
        self._drag = None
        self._destroy_handle()
        # Beschneidung noch am RAHMENLOSEN Fenster aufheben: overrideredirect(False)
        # kann Tk das HWND neu bauen, die Region haftete dann am alten – das Fenster
        # bliebe für immer angeschnitten.
        self._clear_clip()
        try:
            self.root.configure(highlightthickness=0)   # gezeichneten Rand weg (DWM-Kante kommt zurück)
        except tk.TclError:
            pass
        try:
            self.root.overrideredirect(False)
        except tk.TclError:
            pass
        self._refresh_hwnd()
        hwnd = self.app.my_hwnd
        try:
            wf.style_titlebar(hwnd, dark=True, border="#7ecbff",
                              caption="#15151c", text="#cfd3dc", round_corners=True)
            wf.restrict_resize_to_corner(hwnd)
        except Exception:
            pass
        self._reassert_topmost()
        if self._anchor:
            try:
                self.root.geometry(f"+{self._anchor[0]}+{self._anchor[1]}")
            except tk.TclError:
                pass
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass
        self.expanded = False

    def _refresh_hwnd(self):
        try:
            self.root.update_idletasks()
            self.app.my_hwnd = wf.toplevel_hwnd(self.root.winfo_id())
        except Exception:
            pass

    def _reassert_topmost(self):
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass

    # ── Reveal / Collapse (Slide quer zum Rand) ─────────────
    def reveal(self):
        """Deck aus dem Griff heraus hervorgleiten lassen (endet EDGE_GAP vor dem Rand).

        Idempotent: laeuft der Slide schon in diese Richtung, passiert NICHTS. Frueher
        setzte jeder erneute Aufruf Ziel und Griff neu – und da <Motion> auf dem Griff
        dicht feuert, geschah das mehrmals mitten in der Bewegung (samt
        update_idletasks je Aufruf). Genau solche Fremdarbeit zwischen zwei Frames ist
        das, was man als Stottern sieht."""
        self._cancel_reveal()
        if self.edge == "off":
            return
        if self._anim is not None and self._anim["dir"] > 0:
            return                      # gleitet bereits heraus
        # „Steht schon offen" gilt nur, wenn das Fenster auch wirklich zu sehen ist.
        # Sonst haette sich der Zustand verhakt (Slide abgebrochen, Fenster
        # withdrawn) und das Deck waere per Hover nie wieder hervorzuholen.
        if self.expanded and self._anim is None and self._is_shown():
            self._settle_expanded()     # nur nachmessen und ggf. geradeziehen
            return
        self._slide_target = self._expanded_rect()
        self._last_size = self._slide_target[2:]
        self._update_clip_need()
        if not self._is_shown():
            # Erst an die Startposition (nur HANDLE_THICK ragt hervor), DANN einblenden:
            # umgekehrt blitzt ein Frame an der alten Position auf. Beschnitten wird
            # noch VOR dem Einblenden – sonst blitzt der Teil jenseits der Kante einen
            # Frame lang auf dem Nachbar-Monitor auf.
            x, y, w, h = self._slide_geom(0.0)
            try:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                self._apply_clip(0.0)
                self.root.update_idletasks()
                self.root.deiconify()
            except tk.TclError:
                return
        self._hide_handle()             # erst jetzt – so ist der Rand nie einen Frame leer
        self._round_corners()           # vor dem Slide: die Ecken sind gleich rund
        self._anim_to(+1)

    def reveal_for_request(self, hold_ms=REQUEST_HOLD_MS):
        """Aufklappen auf Zuruf von außen (Zweitstart-Wunsch, agent_deck) – und
        anders als beim Hover-Reveal eine Weile offen HALTEN.

        Beim Hover steht der Zeiger auf dem Griff und danach im Fenster, der Poll
        lässt es deshalb offen. Hier steht er irgendwo anders: ohne Haltefrist
        wäre das Deck nach COLLAPSE_DELAY_MS wieder weg, bevor man hinsieht."""
        if self.edge == "off":
            return
        self._hold_until = self._now_ms() + max(0, int(hold_ms))
        self.reveal()
        self._reassert_topmost()        # es soll VOR dem Fenster stehen, das gerade den Fokus hat

    def collapse(self):
        """Deck hinter den Rand zurückgleiten lassen und auf den Griff einklappen."""
        self._hold_until = 0            # tatsächlich eingeklappt -> Haltefrist verbraucht
        self._cancel_reveal()
        if self.edge == "off":
            return
        if self._anim is not None and self._anim["dir"] < 0:
            return                      # gleitet bereits zurück
        if not self.expanded and self._anim is None:
            if self._is_shown():
                self._collapse_now()    # Zustand hatte sich verhakt -> hart aufräumen
            return
        if self._slide_target is None:
            self._slide_target = self._expanded_rect()
        self._update_clip_need()        # auch ohne vorheriges reveal() (set_edge)
        self._anim_to(-1)

    def _collapse_now(self):
        """Ohne Animation einklappen (Start, Rand-Wechsel, Abbruch)."""
        self._anim_cancel()
        self._cancel_reveal()
        self.expanded = False
        self._outside_since = None
        self._hold_until = 0
        self._retarget = False
        self._reveal_lock = self._now_ms() + REVEAL_LOCK_MS
        self._cancel_border_flash()     # unsichtbares Fenster braucht kein Nachleuchten
        self._position_handle()
        self._show_handle()             # Griff zuerst zeigen, DANN das Fenster wegnehmen
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def _is_shown(self):
        try:
            return self.root.state() != "withdrawn"
        except tk.TclError:
            return False

    # ── Slide-Animation (nur Position, feste Größe) ─────────
    def _anim_hold(self):
        """Für die DAUER eines Slides zwei Dinge sichern, die sonst die Frames fressen:

        1. Die Windows-Timer-Auflösung (timeBeginPeriod). Ohne sie tickt Windows nur
           alle 15,6 ms – ein after(10) käme dann mal nach 15, mal nach 31 ms, und aus
           den geplanten ~17 Frames würden ~11 ungleichmäßige. Kein zeitbasierter
           Fortschritt kann das glätten, die Frames fehlen schlicht.
        2. Den Kachel-Animator. Der rendert im Bildmodus ganze Kachelbilder (Pillow)
           und braucht dafür gern länger, als zwischen zwei Slide-Frames liegt – Tk ist
           einthreadig, der Slide muss also warten. Für ~170 ms ausgesetztes Faden
           sieht niemand, ein ruckelnder Slide fällt sofort auf.

        Beides ist gezählt (siehe dort), Doppelaufrufe sind also harmlos."""
        try:
            wf.timer_precision_begin()
        except Exception:
            pass
        anim = getattr(self.app, "anim", None)
        if anim is not None:
            try:
                anim.pause()
            except Exception:
                pass

    def _anim_release(self):
        """Gegenstück zu _anim_hold – MUSS auf jedem Weg aus der Animation laufen,
        sonst bliebe der Prozess im 1-ms-Timer-Takt und die Kacheln für immer
        eingefroren. Darum gibt es mit _anim_finish nur einen einzigen Ausgang."""
        try:
            wf.timer_precision_end()
        except Exception:
            pass
        anim = getattr(self.app, "anim", None)
        if anim is not None:
            try:
                anim.resume()
            except Exception:
                pass

    def _anim_to(self, direction):
        """Slide starten – oder mitten in der Bewegung umkehren (+1 auf, -1 zu).

        Beim Umkehren wird NUR das Federziel getauscht; Position und Geschwindigkeit
        laufen weiter. Das Deck bremst also aus voller Fahrt ab und kehrt um, statt
        eine Kurve rückwärts abzuspulen – stetig auch in der Geschwindigkeit, und die
        Umkehr dauert von selbst nur so lange, wie der Restweg hergibt."""
        prev = self._anim
        # Erst halten, dann den Vorgänger abräumen: so fällt die Zählung beim
        # Umkehren nie auf null und es gibt kein timeEndPeriod/timeBeginPeriod-
        # Pingpong (samt neu startendem Kachel-Rendern) mitten in der Bewegung.
        self._anim_hold()
        self._anim_cancel()
        if prev is not None:
            pos, vel = prev["pos"], prev["vel"]
        else:
            pos, vel = (0.0, 0.0) if direction > 0 else (1.0, 0.0)
        now = self._now_ms()
        response = REVEAL_RESPONSE_MS if direction > 0 else COLLAPSE_RESPONSE_MS
        self._anim = {"dir": direction, "pos": pos, "vel": vel,
                      "target": 1.0 if direction > 0 else 0.0,
                      "omega": 2.0 * math.pi / (response / 1000.0),
                      "last": now, "job": None, "held": True, "sized": False,
                      # Takt EINMAL je Slide festhalten: er darf sich mitten in der
                      # Bewegung nicht ändern, und ein Win32-Aufruf je Frame wäre für
                      # eine Zahl verschwendet, die sich nur beim Monitorwechsel dreht.
                      "tick": frame_tick_ms(getattr(self.app, "my_hwnd", None)),
                      "deadline": now + ANIM_DEADLINE_MS}
        self._anim_step()

    def _anim_cancel(self):
        """Slide abbrechen, ohne einen Endzustand herzustellen (Rand-Wechsel, Abdocken,
        hartes Einklappen – die setzen ihn selbst)."""
        a, self._anim = self._anim, None
        if a is None:
            return
        if a.get("job"):
            try:
                self.root.after_cancel(a["job"])
            except tk.TclError:
                pass
        if a.get("held"):
            self._anim_release()

    def _anim_finish(self, a, direction):
        """Der EINZIGE Ausgang aus einer laufenden Animation: Zustand löschen,
        Haltegriffe freigeben, Endzustand herstellen. Alle drei Wege (regulär fertig,
        Notbremse, Fehler beim Bewegen) laufen hier durch – so bleibt weder ein halb
        ausgefahrenes Deck noch ein nicht freigegebener 1-ms-Timer zurück."""
        self._anim = None
        if a.get("held"):
            self._anim_release()
        self._anim_done(direction)

    def _anim_step(self):
        """Ein Frame: verstrichene Zeit → Feder weiterrechnen → Position setzen.
        Zeitbasiert (nicht pro Frame ein fester Schritt), damit die Bewegung stimmt,
        wenn Tk hinterherhinkt."""
        a = self._anim
        if a is None:
            return
        a["job"] = None
        t0 = self._now_ms()
        dt = max(0.0, t0 - a["last"])
        a["last"] = t0
        d, v = self._spring_at(a["pos"] - a["target"], a["vel"], a["omega"], dt / 1000.0)
        pos = a["target"] + d
        if pos < 0.0 or pos > 1.0:
            # Kann nur beim Umkehren aus voller Fahrt passieren (die Feder selbst
            # schwingt nicht über). Wie gegen eine Wand: hier ist Schluss, die
            # Restgeschwindigkeit verfällt – ein Panel, das über den Bildschirmrand
            # hinaus- oder vom Rand wegschwingt, sieht schlicht kaputt aus.
            pos, v = self._clamp(pos, 0.0, 1.0), 0.0
        a["pos"], a["vel"] = pos, v
        # Angekommen? Eine Feder erreicht ihr Ziel nur asymptotisch – unterhalb eines
        # Pixels ist die Bewegung aber nicht mehr zu sehen, also festsetzen.
        span = max(1, self._slide_span())
        done = abs(pos - a["target"]) * span <= SPRING_SETTLE_PX
        if not done and t0 >= a["deadline"]:
            done = True                               # Notbremse: ans Ziel statt hängen
        if done:
            a["pos"] = a["target"]
        if not self._slide_to(a["pos"], a):
            # Die Geometrie liess sich nicht setzen (Fenster gerade weg oder von Tk
            # neu gebaut). Frueher endete die Animation hier einfach – und das Deck
            # blieb sichtbar auf halber Strecke stehen. Stattdessen: Endzustand
            # herstellen, dann steht es wenigstens sauber offen oder zu.
            self._anim_finish(a, a["dir"])
            return
        if done:
            self._anim_finish(a, a["dir"])
            return
        # Selbstkorrigierender Takt: was dieser Frame gekostet hat, wird von der
        # Wartezeit abgezogen. Sonst summiert sich die Rechenzeit auf den Takt
        # (16 ms Warten + 9 ms Arbeit = 25 ms Abstand) und die Bewegung wird
        # ungleichmäßig, sobald die Frames unterschiedlich teuer sind – und beim
        # Aufklappen sind sie genau das (siehe ANIM_TICK_FALLBACK_MS).
        delay = int(round(a["tick"] - (self._now_ms() - t0)))
        try:
            a["job"] = self.root.after(max(1, delay), self._anim_step)
        except tk.TclError:
            self._anim_finish(a, a["dir"])

    def _slide_to(self, v, a=None):
        """Fenster auf den sichtbaren Anteil v setzen (Position + Beschneidung).
        Rückgabe False, wenn Tk die Geometrie nicht annahm."""
        x, y, w, h = self._slide_geom(v)
        # Bewegen und Beschneiden sind zwei Schritte; dazwischen kann ein Frame
        # gerendert werden. Reihenfolge deshalb immer so, dass NIE mehr Fenster
        # jenseits der Kante liegt als erlaubt: waechst der verdeckte Teil
        # (Einklappen), erst schneiden – schrumpft er (Aufklappen), erst bewegen.
        # Andersherum blitzte je Frame ein Streifen auf dem Nachbar-Monitor auf.
        grows = self._clip_for(v) > self._clip_px
        if grows:
            self._apply_clip(v)
        try:
            # Die GRÖSSE steht während des Slides fest – sie geht nur in den ersten
            # Frame. Jedes weitere "WxH+X+Y" schickt Tk durch seinen Geometry-Manager
            # (Inhalts-Layout neu rechnen), ein reines "+X+Y" verschiebt bloß. Bei
            # ~10 ms Takt ist eingesparte Arbeit pro Frame genau die Währung, in der
            # Ruckeln bezahlt wird.
            if a is not None and a.get("sized"):
                self.root.geometry(f"+{x}+{y}")
            else:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                if a is not None:
                    a["sized"] = True
        except tk.TclError:
            return False
        if not grows:
            self._apply_clip(v)
        return True

    def _anim_watchdog(self):
        """Vom Poll gerufen, solange eine Animation läuft: ist ihr Frame-Timer
        abhandengekommen, holt sie das hier zurück.

        Tk verschluckt eingeplante after-Jobs, wenn ein modaler Dialog oder ein
        fremdes update() dazwischenfährt – dann käme nie wieder ein Frame und das
        Deck stünde für immer halb draußen. Der Poll läuft unabhängig davon weiter
        und ist damit die einzige Instanz, die das überhaupt bemerken kann."""
        a = self._anim
        if a is None:
            return
        if a.get("job") is not None and self._now_ms() < a["deadline"] + POLL_MS:
            return
        self._anim_finish(a, a["dir"])

    def _anim_done(self, direction):
        # Hat sich der Inhalt während des Slides geändert (Agent kam/ging), wurde das
        # Nachziehen bewusst aufgeschoben – jetzt ist der Moment dafür.
        if self._retarget:
            self._retarget = False
            self._last_size = self._content_size()
            self._slide_target = self._expanded_rect()
        if direction > 0:
            self.expanded = True
            self._outside_since = None
            self._reassert_topmost()    # einmal am Ende – ein lift() pro Frame zuckt
            self._settle_expanded()
            self._flash_border()        # Landung quittieren (siehe BORDER_LAND_*)
        else:
            self._collapse_now()

    # ── Landung: Rand leuchtet kurz in der Griff-Farbe nach ─
    def _flash_border(self):
        """Der Rand des angekommenen Decks übernimmt kurz die Farbe des Griffs und
        verblasst auf sein Ruhe-Cyan – der glühende Balken am Rand gibt seine Farbe
        an das Deck ab, das aus ihm herausgefahren ist.

        Läuft NACH dem Slide, konkurriert also mit keinem Frame, und fasst nur die
        Rahmenfarbe an (ein configure() aufs Fenster, kein Canvas-Neuzeichnen)."""
        if self.edge == "off" or self.handle is None:
            return
        self._cancel_border_flash()
        self._land_color = _mix(self._glow_color, "#ffffff", BORDER_LAND_WHITE)
        self._land_i = BORDER_LAND_FRAMES
        self._border_tick()

    def _border_tick(self):
        self._land_job = None
        if self.edge == "off":
            return
        i = self._land_i
        # Anteil der Griff-Farbe, der noch im Rand steckt. Hoch potenziert, damit es
        # hell ANSPRINGT und dann lange leise ausklingt (linear wirkt wie ein Blinker).
        k = (i / float(BORDER_LAND_FRAMES)) ** 1.8
        col = _mix(BORDER_COLOR, self._land_color, k)
        try:
            self.root.configure(highlightbackground=col, highlightcolor=col)
        except tk.TclError:
            return
        if i <= 0:
            return
        self._land_i = i - 1
        try:
            self._land_job = self.root.after(BORDER_LAND_MS, self._border_tick)
        except tk.TclError:
            self._land_job = None

    def _cancel_border_flash(self):
        """Nachleuchten abbrechen und den Rand auf seine Ruhefarbe stellen."""
        if self._land_job:
            try:
                self.root.after_cancel(self._land_job)
            except tk.TclError:
                pass
            self._land_job = None
        self._land_i = 0

    def _settle_expanded(self):
        """Nach dem Aufklappen einmal NACHMESSEN, ob das Fenster wirklich am Ziel steht
        – und es sonst geradeziehen.

        Ein root.geometry() ist nur eine Anforderung; Tk führt sie im Leerlauf aus.
        Fiel der letzte Frame mit einem Inhalts-Resize (_fit_slim_window setzt selbst
        eine Geometrie), einem deiconify oder einem verschluckten Idle-Durchlauf
        zusammen, kam sie nie an – und das Deck stand sichtbar zu weit über der Kante:
        „klappt nicht ganz aus". Darauf zu vertrauen, dass der letzte Frame ankommt,
        ist die eine Annahme, die diese Animation nicht machen darf."""
        if self._slide_target is None:
            return
        x, y, w, h = self._slide_target
        try:
            self.root.update_idletasks()
            off = (abs(self.root.winfo_rootx() - x) > 1
                   or abs(self.root.winfo_rooty() - y) > 1
                   or abs(self.root.winfo_width() - w) > 1
                   or abs(self.root.winfo_height() - h) > 1)
        except tk.TclError:
            return
        if off:
            try:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            except tk.TclError:
                return
        self._clear_clip()          # am Ziel liegt nichts mehr jenseits der Kante

    def _slide_off(self, v):
        """Um wieviel px die Position beim sichtbaren Anteil v gegen das Ziel
        zurückliegt. Eigene Methode, weil ihn zwei Dinge brauchen: die Position
        (_slide_geom) und das Wegschneiden (_apply_clip, dort minus EDGE_GAP – das
        Ziel liegt selbst schon so weit von der Kante weg). Aus einer Quelle -> nie
        auseinandergelaufen.

        Das + EDGE_GAP im span ist genau diese Einrückung des Ziels: ohne es käme der
        Startstreifen um EDGE_GAP breiter heraus als der Griff, den er ersetzt."""
        return int(round(self._slide_span() * (1.0 - self._clamp(v, 0.0, 1.0))))

    def _slide_span(self):
        """Weglänge des Slides in px – vom Griffstreifen bis aufs Ziel. Auch die
        Feder braucht sie: ihre Abbruchschwelle ist in PIXELN gedacht (unter einem
        Pixel sieht man nichts mehr), gerechnet wird aber im Anteil 0..1."""
        if self._slide_target is None:
            return 0
        _x, _y, w, h = self._slide_target
        return max(0, (w if self._is_vertical() else h) - handle_thick() + EDGE_GAP)

    def _slide_geom(self, v):
        """Fenster-Geometrie beim sichtbaren Anteil v: 0 = nur HANDLE_THICK ragt über
        den Rand (genau die Griff-Position), 1 = aufgeklappt, EDGE_GAP vom Rand weg.
        Nur die Position wandert, die Größe steht."""
        if self._slide_target is None:
            self._slide_target = self._expanded_rect()
        x, y, w, h = self._slide_target
        off = self._slide_off(v)
        if self.edge == "left":
            return x - off, y, w, h
        if self.edge == "right":
            return x + off, y, w, h
        return x, y - off, w, h         # top

    # ── Beschneiden an der Kante (nur bei Nachbar-Monitor) ──
    def _edge_pos(self):
        """Bildschirmkoordinate der Andock-Kante (x bei links/rechts, y bei oben)."""
        return self.root.winfo_screenwidth() if self.edge == "right" else 0

    def _update_clip_need(self):
        """Je Slide einmal klären, ob überhaupt beschnitten werden muss: nur wenn
        JENSEITS der Andock-Kante ein weiterer Monitor liegt, würde der über die
        Kante hinausgeschobene Teil dort als Geisterbild mitfliegen. Sonst
        übernimmt der Bildschirmrand das Abschneiden von selbst – und das Fenster
        behält seine weiche DWM-Rundung (eine Region ersetzt sie durch harte Ecken)."""
        try:
            self._clip_on = wf.screen_beyond(self.edge, self._edge_pos())
        except Exception:
            self._clip_on = False

    def _clip_for(self, v):
        """Wieviel beim Slide-Fortschritt v weggeschnitten gehört (0 = nichts).

        Der Positions-Versatz MINUS EDGE_GAP: um diese Luft ist das Ziel schon von
        der Kante eingerückt, sie liegt also nie jenseits davon. Ohne das Abziehen
        wären dem Streifen am Rand die letzten EDGE_GAP px weggeschnitten.

        Das Ergebnis wird auf CLIP_QUANT AUFGERUNDET: jede Änderung kostet ein
        SetWindowRgn und damit ein Neuzeichnen des ganzen Fensters – ein anderer Wert
        je Frame wären ~17 Vollredraws pro Slide. Nach OBEN gerundet, nie nach unten:
        zu viel weggeschnitten heißt höchstens, dass der Streifen an der Kante ein
        paar Pixel schmaler ist; zu wenig hieße, dass ein Stück Fenster auf dem
        Nachbarmonitor aufblitzt – und genau das soll die Beschneidung verhindern."""
        if not self._clip_on:
            return 0
        cut = max(0, self._slide_off(v) - EDGE_GAP)
        if cut <= 0:
            return 0
        q = max(1, CLIP_QUANT)
        return int(math.ceil(float(cut) / q) * q)

    def _apply_clip(self, v):
        """Den Teil jenseits der Kante wegschneiden – passend zum Slide-Fortschritt v.
        Unveränderte Breite -> kein Aufruf: SetWindowRgn zeichnet das Fenster neu."""
        cut = self._clip_for(v)
        if cut == self._clip_px:
            return
        # Eine noch nicht ausgefuehrte geometry()-Anforderung ZUERST anwenden lassen.
        # SetWindowRgn ueberholt sie sonst: Tk haelt das Fenster danach fuer
        # verschoben und verwirft die Bewegung – der Slide fror auf der
        # Startposition ein und nur der Schnitt lief weiter.
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return
        extent = 0
        if self._slide_target is not None:
            _x, _y, w, h = self._slide_target
            extent = w if self._is_vertical() else h
        try:
            wf.clip_window(self.app.my_hwnd, self.edge, cut, extent)
        except Exception:
            pass
        self._clip_px = cut

    def _clear_clip(self):
        """Beschneidung aufheben (bündig aufgeklappt, abgedockt, Rand gewechselt)."""
        if not self._clip_px:
            return
        try:
            wf.clip_window(self.app.my_hwnd, self.edge, 0)
        except Exception:
            pass
        self._clip_px = 0

    @staticmethod
    def _spring_at(d0, v0, omega, dt):
        """Kritisch gedämpfte Feder um dt Sekunden weiterrechnen – ANALYTISCH, nicht
        Schritt für Schritt integriert. Rein: Abstand zum Ziel und Geschwindigkeit
        jetzt. Raus: beides nach dt.

        Bei Dämpfungsgrad genau 1 hat die Bewegungsgleichung die geschlossene Lösung
            d(t) = (d0 + (v0 + ω·d0)·t) · e^(−ω·t)
        (die doppelte Nullstelle des charakteristischen Polynoms), abgeleitet
            v(t) = (v0 − ω·(v0 + ω·d0)·t) · e^(−ω·t).

        Die geschlossene Form ist hier nicht Angeberei, sondern Robustheit: eine
        Schritt-für-Schritt-Integration wird bei großem dt instabil und müsste in
        Teilschritte zerlegt werden – genau dann, wenn das System ohnehin schon
        klemmt (ausgefallene Frames, Standby). Die Formel ist bei JEDEM dt exakt;
        ein sehr großes dt liefert sauber „steht am Ziel"."""
        e = math.exp(-omega * dt)
        c = v0 + omega * d0
        return (d0 + c * dt) * e, (v0 - omega * c * dt) * e

    # ── Poll-Loop (nur Einklappen beobachten) ───────────────
    def _start_poll(self):
        if self._poll_job is None:
            self._schedule_poll()

    def _stop_poll(self):
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    def _schedule_poll(self):
        try:
            self._poll_job = self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            self._poll_job = None

    def _poll(self):
        self._poll_job = None
        if self.edge == "off":
            return
        try:
            self._poll_once()
        except tk.TclError:
            return
        self._schedule_poll()

    def _poll_once(self):
        if self._anim is not None:
            # Während des Gleitens nicht dazwischenfahren – aber nachsehen, ob die
            # Bewegung überhaupt noch lebt (der Poll ist die einzige Instanz, die
            # einen abhandengekommenen Frame-Timer bemerken kann).
            self._anim_watchdog()
            return
        if not self.expanded:
            self._poll_reveal()
            return
        # Modal-Dialog offen oder Kachel-Drag → aufgeklappt lassen (der Dialog hängt
        # als Kind an root; Einklappen würde ihn mit wegziehen).
        if getattr(self.app, "_modal", False) or self._app_dragging():
            self._outside_since = None
            return
        # Von außen aufgeklappt (reveal_for_request): Haltefrist abwarten, der Zeiger
        # ist ja noch nicht hier. _outside_since dabei zurücksetzen -> nach Fristende
        # gilt wieder die volle COLLAPSE_DELAY_MS, es klappt nicht schlagartig zu.
        if self._hold_until and self._now_ms() < self._hold_until:
            self._outside_since = None
            return
        px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
        if self._pointer_in_window(px, py):
            self._outside_since = None
            return
        now = self._now_ms()
        if self._outside_since is None:
            self._outside_since = now
        elif now - self._outside_since >= COLLAPSE_DELAY_MS:
            self.collapse()

    def _poll_reveal(self):
        """Eingeklappt: aufklappen, sobald der Zeiger auf dem Griff steht – als
        ZWEITER, ereignisunabhängiger Weg neben den <Enter>/<Motion>-Bindings.

        Auf die Bindings allein ist kein Verlass. Der Griff ist ein rahmenloses
        Topmost-Fenster, das bei jedem Ein-/Ausklappen durch withdraw/deiconify geht.
        Taucht es unter einem STEHENDEN Zeiger auf, schickt Windows kein
        Mausereignis – Tk feuert dann weder <Enter> noch <Motion>, und der Griff
        bleibt tot, bis man die Maus bewegt. Genau das fühlt sich an wie „klappt gar
        nicht erst auf". Ebenso verschluckt: das erste Ereignis nach einem Fokus-
        wechsel auf ein anderes Topmost-Fenster.

        Der Poll läuft ohnehin fürs Einklappen; ihn auch in die Gegenrichtung schauen
        zu lassen kostet nichts und macht das Aufklappen unabhängig von der
        Ereignis-Laune des Fenstermanagers. Die Zieh-Zone bleibt ausgespart – dort
        wird gegriffen, nicht aufgeklappt (siehe Modul-Kopf)."""
        if self._drag or self.handle is None or not self._handle_shown:
            return
        if self._reveal_job is not None:
            return                      # Hover-Weg hat es schon in der Mache
        if self._now_ms() < self._reveal_lock:
            return                      # gerade erst eingeklappt (Anti-Flatter)
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
        except tk.TclError:
            return
        hx, hy, hw, hh = self._handle_geom()
        if not (hx <= px < hx + hw and hy <= py < hy + hh):
            return
        if self._across(px - hx, py - hy, hw, hh) >= capsule_extent():
            return                      # Polster: hier wird gegriffen, nicht geöffnet
        self.reveal()

    def _app_dragging(self):
        try:
            return bool(self.app._dragging())
        except Exception:
            return False

    @staticmethod
    def _now_ms():
        """Monotone Uhr in Millisekunden (float).

        Bewusst perf_counter statt Tks `clock milliseconds`: das liest die Systemuhr
        und hat damit deren Granularität von ~15,6 ms. Bei einem Frame-Takt von 10 ms
        liefert sie für aufeinanderfolgende Frames abwechselnd 0 und 16 ms – der
        zeitbasierte Fortschritt stand also einen Frame still und machte im nächsten
        einen Doppelschritt. Das war Stottern, das die UHR erzeugte, nicht die
        Bewegung, und es blieb selbst dann, wenn Tk seine Frames pünktlich lieferte.
        perf_counter ist monoton (springt nicht bei Zeitumstellung/NTP) und
        mikrosekundengenau."""
        return time.perf_counter() * 1000.0

    def _pointer_in_window(self, px, py):
        try:
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
        except tk.TclError:
            return False
        if w <= 1 or h <= 1:
            w, h = self._last_size if self._last_size[0] else (w, h)
        m = INSIDE_MARGIN
        return (x - m) <= px <= (x + w + m) and (y - m) <= py <= (y + h + m)

    # ── Geometrie ───────────────────────────────────────────
    def _capture_anchor(self):
        try:
            self.root.update_idletasks()
            self._anchor = (self.root.winfo_rootx(), self.root.winfo_rooty())
        except tk.TclError:
            self._anchor = (100, 100)

    def _content_size(self):
        try:
            self.root.update_idletasks()
            w = self.root.winfo_reqwidth()
            h = self.root.winfo_reqheight()
        except tk.TclError:
            w, h = self._last_size if self._last_size[0] else (300, 200)
        return max(1, w), max(1, h)

    def _is_vertical(self):
        return self.edge in ("left", "right")

    def _get_along(self):
        """Position ENTLANG des Rands: y bei links/rechts, x bei oben."""
        x, y = self._anchor or (100, 100)
        return y if self._is_vertical() else x

    def _set_along(self, v):
        x, y = self._anchor or (100, 100)
        if self._is_vertical():
            self._anchor = (x, int(v))
        else:
            self._anchor = (int(v), y)

    def _handle_center_along(self):
        """Position der Griff-MITTE entlang des Rands (y bei links/rechts, x bei oben).
        Der Griff sitzt top-aligned am Anker → Mitte = Anker + halbe Grifflänge."""
        return self._get_along() + self._handle_len() / 2

    def _expanded_rect(self):
        """(x, y, w, h) für das angedockte, aufgeklappte Fenster – EDGE_GAP vom Rand.

        Auf der freien Achse wird das Fenster GLEICHMÄSSIG um die Griff-Mitte
        aufgeklappt (genauso viel über wie unter dem Griff) und am Bildschirmrand
        geklemmt, damit nichts abgeschnitten wird. Geklemmt wird dort auf EDGE_GAP,
        nicht auf 0: ein sehr hohes/breites Deck liegt sonst zusätzlich an der
        Quer-Kante an und verliert dort denselben Rand wie an der Dockkante."""
        w, h = self._content_size()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        g = EDGE_GAP
        ax, ay = self._anchor or (100, 100)
        if self.edge in ("left", "right"):
            x = g if self.edge == "left" else sw - w - g
            y = self._clamp(self._handle_center_along() - h / 2, g, max(g, sh - h - g))
        elif self.edge == "top":
            y = g
            x = self._clamp(self._handle_center_along() - w / 2, g, max(g, sw - w - g))
        else:
            x, y = ax, ay
        return int(x), int(y), int(w), int(h)

    def _reposition_expanded(self):
        self._slide_target = self._expanded_rect()
        x, y, w, h = self._slide_target
        self._last_size = (w, h)
        try:
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass
        self._clear_clip()          # am Ziel -> nichts liegt mehr jenseits der Kante

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    @staticmethod
    def _norm(edge):
        return edge if edge in EDGES else "off"

    # ── Position merken (dock_along) ────────────────────────
    def _apply_saved_along(self):
        saved = self.app.settings.get("dock_along")
        if isinstance(saved, (int, float)):
            self._set_along(int(saved))

    def _persist_along(self):
        self.app.settings["dock_along"] = int(self._get_along())
        self._save_settings()

    def _save_settings(self):
        try:
            self.app.store.save_settings()
        except Exception:
            pass

    # ── Griff-Balken ────────────────────────────────────────
    def _ensure_handle(self):
        if self.handle is not None:
            return
        h = tk.Toplevel(self.root)
        try:
            h.overrideredirect(True)
            h.attributes("-topmost", True)
        except tk.TclError:
            pass
        h.configure(bg=HANDLE_BG)
        c = tk.Canvas(h, bg=HANDLE_BG, highlightthickness=0, bd=0, cursor="hand2")
        c.pack(fill="both", expand=True)
        # Hover blendet ein – ausser in der Zieh-Zone (Mitte), die greift statt
        # aufzuklappen; ein reiner Klick blendet auch dort ein. <Motion> braucht es
        # zusätzlich zu <Enter>, damit ein Zonenwechsel INNERHALB des Griffs greift
        # (Tk feuert <Enter> nur beim Betreten des Fensters). Presse-Grab liefert
        # Motion/Release auch außerhalb des schmalen Griffs (Tk hält den Grab bis
        # zum Loslassen).
        c.bind("<Enter>", self._h_enter)
        c.bind("<Motion>", self._h_hover_motion)
        c.bind("<Leave>", self._h_leave)
        c.bind("<ButtonPress-1>", self._h_press)
        c.bind("<B1-Motion>", self._h_motion)
        c.bind("<ButtonRelease-1>", self._h_release)
        self.handle = h
        self.handle_canvas = c
        self._handle_drawn = (0, 0)
        self._enable_alpha()
        self._hide_handle()

    def _enable_alpha(self, force=False):
        """Das Griff-Fenster auf Per-Pixel-Alpha umstellen (WS_EX_LAYERED).

        `force` legt den Layer-Zustand NEU an – nötig nach jedem Einblenden, siehe
        win_focus.layered_enable und _show_handle.

        Klappt das, zeichnet nicht mehr Tk in dieses Fenster, sondern wir schieben je
        Frame ein RGBA-Bild hinein (_paint_layered) – nur die Röhre und ihr Hof sind
        dann zu sehen, sonst der Desktop. Klappt es nicht (oder fehlt Pillow), bleibt
        es beim Canvas: dann zeichnet _draw_handle die alte Linien-Röhre auf den
        dunklen Grund.

        Das HWND muss dafür schon existieren, darum das update_idletasks – ein frisch
        erzeugtes Toplevel hat noch keins."""
        self._layered = False
        if not hrender.AVAILABLE or self.handle is None:
            return
        try:
            self.handle.update_idletasks()
            self._handle_hwnd = wf.toplevel_hwnd(self.handle.winfo_id())
            self._layered = wf.layered_enable(self._handle_hwnd, force=force)
        except Exception:
            self._handle_hwnd = None
            self._layered = False

    def _destroy_handle(self):
        self._stop_glow()
        self._handle_shown = False
        if self.handle is not None:
            try:
                self.handle.destroy()
            except tk.TclError:
                pass
        self.handle = None
        self.handle_canvas = None
        self._layered = False
        self._handle_hwnd = None
        self._img_size = (0, 0)
        self._neon = []
        self._grip_hot = False
        self._hot = False
        self._bloom = 0.0

    # ── Zonen quer zum Griff: Kapsel vs. Polster ────────────
    def _across(self, x, y, w=None, h=None):
        """Abstand des Punktes von der DOCKKANTE, quer zum Griff (px).

        Bei „links" und „oben" liegt die Kante bei 0, bei „rechts" am anderen Ende –
        dort wird gespiegelt gerechnet, genau wie der Renderer die Kapsel spiegelt. So
        gilt überall dieselbe Regel: klein = an der Bildschirmkante (Kapsel), groß =
        innen (Polster). Koordinaten sind CANVAS-relativ."""
        if self.edge == "right":
            if w is None:
                w = self._handle_drawn[0] or handle_thick()
            return (w - 1) - x
        return y if self.edge == "top" else x

    def _in_grip(self, ev):
        """True, wenn der Zeiger im unsichtbaren Polster steht – dort wird GEGRIFFEN
        (verschieben), nicht aufgeklappt. Auf der Kapsel ist es umgekehrt."""
        return self._across(ev.x, ev.y) >= capsule_extent()

    # ── Griff-Events: Hover / Klick / Ziehen ────────────────
    def _h_enter(self, ev):
        self._hover_zone(ev)

    def _h_hover_motion(self, ev):
        # Bewegung OHNE gedrückte Taste (mit Taste gewinnt das spezifischere
        # <B1-Motion>); der Guard in _hover_zone deckt den Rest ab.
        self._hover_zone(ev)

    def _hover_zone(self, ev):
        """Zone unter dem Zeiger auswerten.

        Im unsichtbaren Polster passiert bewusst NICHTS: kein Aufklappen (dort will man
        greifen, und reveal() würde den Griff genau im Moment des Zugriffs verstecken)
        und auch kein Aufleuchten – was man nicht sieht, soll auch nicht reagieren.
        Der einzige Hinweis dort ist der Zeiger, der auf 'fleur' wechselt.

        Auf der Kapsel ist es umgekehrt: sie hellt auf und das Deck klappt auf."""
        # Läuft schon ein Slide, ist hier NICHTS mehr zu entscheiden. <Motion> feuert
        # dicht, und jeder Durchlauf würde mitten in der Bewegung neu einfärben und
        # (über reveal) das Ziel neu ausmessen – Arbeit zwischen zwei Frames, die man
        # als Ruckeln sieht.
        if self.expanded or self._drag or self._anim is not None:
            return
        if self._in_grip(ev):
            self._set_accent_hot(False)   # unsichtbarer Bereich -> nichts leuchtet auf
            self._set_grip_hot(True)
            self._cancel_reveal()         # und nicht aufklappen: hier wird gegriffen
            return
        self._set_accent_hot(True)        # Zeiger auf der Kapsel -> sie hellt auf
        self._set_grip_hot(False)
        if self._reveal_job is not None:
            return          # läuft schon – NICHT neu aufsetzen: bei HOVER_REVEAL_MS=0
                            # würde jedes Motion-Event den Job abbestellen und neu
                            # legen, und bei durchgehender Mausbewegung käme er nie
                            # dran (Reschedule-Sturm) -> es klappte nie auf.
        try:
            self._reveal_job = self.root.after(HOVER_REVEAL_MS, self._reveal_from_hover)
        except tk.TclError:
            self._reveal_job = None

    def _h_leave(self, _ev):
        if self._drag:
            return          # Ziehen läuft (Tk hält den Grab über den Rand hinaus) ->
                            # Zeiger/Leuchten NICHT zurücksetzen, es geht ja weiter
        self._set_accent_hot(False)
        self._set_grip_hot(False)
        self._cancel_reveal()

    def _set_grip_hot(self, hot):
        """Zeiger im Polster (Zieh-Zone): Zeigerform auf 'fleur'. Das ist der EINZIGE
        Hinweis – die Zone ist unsichtbar und soll es bleiben, ein Aufleuchten dort
        würde eine Fläche behaupten, die man nicht sieht."""
        if hot == self._grip_hot:
            return
        self._grip_hot = hot
        if self.handle_canvas is not None:
            try:
                self.handle_canvas.configure(cursor="fleur" if hot else "hand2")
            except tk.TclError:
                pass
        self._paint_handle()

    def _set_accent_hot(self, hot):
        """Röhren-Kern aufhellen/verbreitern, solange der Zeiger auf dem Griff steht.
        Bleibt in der aktuellen STATUSFARBE (nur mehr Weißanteil) – ein festes Cyan
        würde die Rückfrage-/Ungelesen-Farbe genau im Moment des Hinschauens
        überschreiben. Reine itemconfig-Änderung (kein Neuzeichnen) → flackerfrei."""
        if hot == self._hot:
            return
        self._hot = hot
        self._paint_handle()

    def _reveal_from_hover(self):
        self._reveal_job = None
        if not self._drag:
            self.reveal()

    def _cancel_reveal(self):
        if self._reveal_job:
            try:
                self.root.after_cancel(self._reveal_job)
            except tk.TclError:
                pass
            self._reveal_job = None

    def _h_press(self, ev):
        # Greifen: geplantes Hover-Aufklappen abbrechen, Ziehen vorbereiten (noch
        # kein Ziehen, bis die Schwelle überschritten ist). NUR in der Zieh-Zone –
        # ausserhalb ist der Griff schon am Aufklappen, da würde ein Drag ins Leere
        # laufen; der Press zählt dort als Klick (-> _h_release klappt auf).
        self._cancel_reveal()
        if not self._in_grip(ev):
            return
        self._drag = {"start": self._pointer_along(),
                      "anchor0": self._get_along(), "moved": False}

    def _h_motion(self, _ev):
        d = self._drag
        if not d:
            return
        delta = self._pointer_along() - d["start"]
        if not d["moved"] and abs(delta) < DRAG_THRESH:
            return
        d["moved"] = True
        length = self._handle_len()
        limit = (self.root.winfo_screenheight() if self._is_vertical()
                 else self.root.winfo_screenwidth()) - length
        self._set_along(self._clamp(d["anchor0"] + delta, 0, max(0, limit)))
        self._position_handle()

    def _h_release(self, _ev):
        d = self._drag
        self._drag = None
        if d and d["moved"]:
            self._persist_along()      # neue Position am Rand merken
        else:
            self.reveal()              # reiner Klick → sofort aufklappen

    def _pointer_along(self):
        return (self.root.winfo_pointery() if self._is_vertical()
                else self.root.winfo_pointerx())

    # ── Griff zeichnen / positionieren ──────────────────────
    def _handle_len(self):
        win_w, win_h = self._last_size if self._last_size[0] else self._content_size()
        base = win_h if self._is_vertical() else win_w
        return int(self._clamp(base, HANDLE_MIN_LEN, HANDLE_MAX_LEN))

    def _handle_geom(self):
        """(x, y, w, h) des Griffs: quer handle_thick() dünn, längs an der Fenster-
        Oberkante/-Vorderkante (= Anker) ausgerichtet, begrenzt aufs Sichtbare."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        length = self._handle_len()
        along = self._get_along()
        thick = handle_thick()
        if self._is_vertical():
            y = int(self._clamp(along, 0, max(0, sh - length)))
            x = 0 if self.edge == "left" else sw - thick
            return x, y, thick, length
        x = int(self._clamp(along, 0, max(0, sw - length)))
        return x, 0, length, thick

    def _position_handle(self):
        if self.handle is None:
            return
        x, y, w, h = self._handle_geom()
        try:
            self.handle.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            return
        if (w, h) != self._handle_drawn:   # nur bei Größenänderung neu zeichnen (kein Flackern beim Ziehen)
            self._draw_handle(w, h)
            self._handle_drawn = (w, h)

    def _draw_handle(self, w, h):
        """Die Griff-Grafik ANLEGEN (nur bei Größenänderung). Gefärbt wird hier nichts
        – das macht _paint_handle je Frame; ein delete('all') pro Frame flimmert am
        Rand.

        Zwei Wege: trägt das Fenster Alpha je Pixel (_enable_alpha), zeichnet Tk hier
        gar nichts – das Bild kommt per Win32 ins Fenster (_paint_layered), und nur
        Kapsel und Bloom sind zu sehen. Sonst der alte Linien-Rückfall aus drei
        deckungsgleichen Röhren-Linien auf dunklem Grund.

        Der Rückfall liegt an DERSELBEN Stelle wie die Kapsel (also um den Abstand von
        der Dockkante versetzt, nicht in der Fenstermitte). Sonst wäre das Sichtbare
        woanders als die Zonen (_in_grip rechnet mit capsule_extent) – man würde neben
        dem Leuchten aufklappen und auf ihm greifen."""
        c = self.handle_canvas
        if c is None:
            return
        c.delete("all")
        c.configure(width=w, height=h)
        self._neon = []
        self._img_size = (w, h)
        if self._layered:
            self._paint_handle()
            return
        pad = 6
        mid = capsule_extent() - HANDLE_THICK / 2.0    # Mitte der Kapsel-Spur
        if self._is_vertical():
            cx = (w - 1) - mid if self.edge == "right" else mid
            self._neon = [c.create_line(cx, pad, cx, h - pad, width=lw, capstyle="round")
                          for lw, _fade in NEON_LAYERS]
        else:
            self._neon = [c.create_line(pad, mid, w - pad, mid, width=lw, capstyle="round")
                          for lw, _fade in NEON_LAYERS]
        self._paint_handle()

    # ── Neon: färben + atmen ────────────────────────────────
    def _eff_intensity(self):
        """Aktuelle Leuchtkraft: Ruhe-Intensität (bei 'pulse' atmend) – nie unter
        NEON_FLOOR, damit der Griff greifbar bleibt – plus das abklingende Aufblitzen."""
        base = self._glow_int * (self._pulse_factor() if self._glow_pulse else 1.0)
        return max(base, NEON_FLOOR) + self._bloom

    def _pulse_factor(self):
        """Sanftes Atmen 0.60..1.00 (Cosinus) – dieselbe Kurve wie GlowAnimator."""
        ang = 2 * math.pi * (self._pulse_i % NEON_PULSE_TICKS) / NEON_PULSE_TICKS
        return 0.60 + 0.40 * (0.5 - 0.5 * math.cos(ang))

    # ── Schwappen im Kern ───────────────────────────────────
    def _wave_seconds(self):
        """Sekunden seit dem letzten Anstoß (echte Uhr, siehe _wave_t0)."""
        if self._wave_t0 is None:
            self._wave_t0 = self._now_ms()
        return (self._now_ms() - self._wave_t0) / 1000.0

    def _wave_profile(self, n):
        """Das Wellen-Profil für ein Bild der Länge n – oder None.

        None heißt „Ruhezustand": zwischen zwei Stößen steht das Wasser still, und
        dann ist das Bild dasselbe, das der Griff ohne dieses ganze Modul hätte. Das
        ist nicht bloß eine Ersparnis, sondern die Bedingung dafür, dass der
        BILD-CACHE überhaupt noch etwas taugt (siehe handle_render.handle_bits): in
        der Ruhephase trifft er wieder.

        Der Griff wird auch ohne Röhren-Bild gezeichnet (Linien-Rückfall ohne Pillow);
        dort gibt es keine Schicht, in der eine Welle Platz hätte – deshalb hängt das
        Schwappen am Alpha-Pfad."""
        if not (WAVE_ON and self._layered) or n < 8:
            return None
        t = self._wave_seconds()
        if hwave.quiet(t):
            return None
        return hwave.profile(n, t)

    def _wave_kick(self):
        """Neu anstoßen: die Schwingung fängt von vorn an.

        Gerufen, wenn der Status DRINGLICHER wird – dieselbe Stelle, an der der Griff
        heute kurz aufblitzt (NEON_BLOOM) und die Sache damit nach 1,5 s vorbei ist.
        Mit dem Stoß läuft die Bewegung noch Sekunden weiter: aus einem Aufblitzen
        wird eine Spur."""
        self._wave_t0 = self._now_ms()

    def _paint_handle(self):
        """Griff in der aktuellen Statusfarbe einfärben. Reine itemconfig-/bg-Änderung
        auf vorhandenen Items -> flackerfrei."""
        c = self.handle_canvas
        if c is None or not (self._layered or self._neon):
            return
        eff = self._eff_intensity()
        col = self._glow_color
        if self._layered:
            self._paint_layered(col, eff)
            return
        bg = neon_tint(col, eff)
        try:
            self.handle.configure(bg=bg)
            c.configure(bg=bg)
            for item, (lw, fade) in zip(self._neon, NEON_LAYERS):
                c.itemconfig(item, fill=neon_color(col, fade, eff, self._hot),
                             width=(lw + 1 if (self._hot and fade <= 0) else lw))
        except tk.TclError:
            pass                                      # Griff gerade neu gezeichnet -> egal

    def _paint_layered(self, col, eff):
        """Alpha-Pfad: das RGBA-Bild für (Größe, Farbe, Leuchtkraft, Zeiger) holen und
        per Win32 ins Griff-Fenster schieben.

        Die Zieh-Zone taucht hier NICHT auf: sie ist das unsichtbare Polster und hat
        bewusst keine Darstellung – nur die Zeigerform verrät sie (_set_grip_hot).

        Scheitert das Schieben, fällt der Griff EINMALIG auf den Linien-Pfad zurück
        und wird neu aufgebaut. Das ist der Fall, den man nicht stillschweigend
        durchgehen lassen darf: ein layered Fenster, in das niemand ein Bild schiebt,
        ist vollständig unsichtbar – der Griff wäre weg, nicht nur hässlich.

        WICHTIG – der Grund für die erste Zeile: ein VERSTECKTES layered Fenster nimmt
        kein Bild an, UpdateLayeredWindow lehnt mit ERROR_INVALID_PARAMETER ab. Genau
        das passierte hier, denn _collapse_now positioniert (und zeichnet) den Griff,
        BEVOR _show_handle ihn einblendet: der allererste Schub scheiterte, der Griff
        fiel auf den Linien-Pfad zurück und blieb dort. Auf ein unsichtbares Fenster zu
        malen ist ohnehin sinnlos – also gar nicht erst versuchen; _show_handle ruft
        _paint_handle, sobald das Fenster wirklich steht."""
        w, h = self._img_size
        if w < 4 or h < 4 or not self._handle_shown:
            return
        # Die Länge des Profils ist die KANONISCHE Höhe des Bildes (am oberen Rand
        # liegt der Griff quer, dort sind Breite und Höhe getauscht). Bewusst aus
        # derselben Quelle wie der Renderer, damit die beiden nie auseinanderlaufen.
        prof = self._wave_profile(hrender._canon(w, h, self.edge)[1])
        bits = hrender.handle_bits(w, h, self.edge, HANDLE_THICK, col, eff,
                                   hot=self._hot, prof=prof)
        if bits is not None:
            # Im selben Bild steckt nichts Neues -> nicht schieben. Zwischen zwei
            # Wellen-Stößen steht das Wasser still, dort liefert handle_bits genau
            # dasselbe (gecachte) Objekt wieder; ohne diese Zeile ginge trotzdem
            # 30x je Sekunde ein UpdateLayeredWindow an Windows.
            if bits is self._last_bits:
                return
            if wf.layered_push(self._handle_hwnd, bits, w, h):
                self._last_bits = bits
                return
            # Zweiter Versuch mit frisch gemessenem HWND und NEU angelegtem Layer-
            # Zustand (force): Tk baut Fenster gelegentlich neu auf, und nach einem
            # Ein-/Ausblenden ist der Layer-Zustand verworfen, obwohl das Bit noch
            # steht – ohne force liefe der Versuch ins Leere (genau daran ist es
            # einmal gescheitert, siehe win_focus.layered_enable).
            self._enable_alpha(force=True)
            if self._layered and wf.layered_push(self._handle_hwnd, bits, w, h):
                self._last_bits = bits
                return
        # Aufgeben heisst: der Griff sieht ab jetzt anders aus als entworfen (dunkler
        # Kasten mit Linien statt freistehender Kapsel). Das darf nicht stumm
        # passieren – sonst sucht man den Fehler im Entwurf statt in Win32.
        self._report_layer_failure(w, h)
        self._layered = False
        self._handle_drawn = (0, 0)
        self._draw_handle(w, h)
        self._handle_drawn = (w, h)

    def _report_layer_failure(self, w, h):
        """Den Rückfall auf den Linien-Pfad AKTENKUNDIG machen.

        Warum eine Datei und nicht nur stderr: das Deck läuft normal unter `pythonw`,
        und dort gibt es keine Konsole – ein print() verschwindet ersatzlos. Genau
        daran lag es, dass dieser Rückfall lange unbemerkt blieb: der Griff sah anders
        aus als entworfen und nichts sagte, warum. Die Datei wird bei jedem Fehlschlag
        überschrieben (kein Wachstum) und ist der erste Ort, an dem man nachsieht, wenn
        der Griff wieder als dunkler Kasten erscheint."""
        msg = (f"layered_push fehlgeschlagen: {wf.LAST_ERROR}\n"
               f"  Bild {w}x{h}, Kante {self.edge}, sichtbar={self._handle_shown}\n"
               f"  {wf.layer_probe(self._handle_hwnd)}\n"
               f"  -> Griff zeichnet ab jetzt den Linien-Rückfall (dunkler Balken)\n")
        try:
            print("[edge_dock] " + msg, file=sys.stderr, flush=True)
        except Exception:
            pass                      # ohne Konsole (pythonw) gibt es kein stderr
        try:
            with open(LAYER_ERR_PATH, "w", encoding="utf-8") as f:
                f.write(msg)
        except OSError:
            pass                      # Diagnose darf den Griff nie kippen

    def _glow_needed(self):
        """Timer nur, solange es wirklich etwas zu bewegen gibt: der Griff ist sichtbar
        (= eingeklappt) und es atmet, ein Aufblitzen klingt ab – oder der Kern schwappt.

        Das Schwappen läuft dauerhaft, also läuft der Timer ab jetzt immer, solange der
        Griff zu sehen ist. Was das kostet, ist gemessen: ein Wellenbild 0,40 ms bei
        100 % und 0,81 ms bei 150 % Skalierung – bei 33 ms Takt also 1,2 bis 2,4 %. In
        der Ruhephase zwischen zwei Stößen fällt auch das weg (_wave_profile gibt dort
        None, das Bild kommt aus dem Cache und wird nicht einmal geschoben). Und sichtbar
        ist der Griff nur EINGEKLAPPT – dann ist keine einzige Kachel zu sehen, mit der
        er sich die Rechenzeit teilen müsste; während des Slides pausiert er ohnehin
        (siehe _glow_tick)."""
        if not self._handle_shown:
            return False
        return (self._glow_pulse or self._bloom >= 0.01
                or (WAVE_ON and self._layered))

    def _start_glow(self):
        if self._glow_job is None and self._glow_needed():
            try:
                self._glow_job = self.root.after(NEON_MS, self._glow_tick)
            except tk.TclError:
                self._glow_job = None

    def _stop_glow(self):
        if self._glow_job:
            try:
                self.root.after_cancel(self._glow_job)
            except tk.TclError:
                pass
            self._glow_job = None

    def _glow_tick(self):
        self._glow_job = None
        self._pulse_i += 1
        self._bloom = self._bloom * NEON_DECAY if self._bloom >= 0.01 else 0.0
        # Während das Deck gleitet, NICHT malen: die Feder läuft im selben Thread und
        # braucht jeden Frame (dasselbe Argument wie _anim_hold für die Kacheln). Der
        # Griff ist dabei ohnehin gerade am Verschwinden oder noch nicht da. Die
        # Schwingung selbst läuft an der Uhr weiter und ist danach an der richtigen
        # Stelle – genau darum ist es eine Uhr und kein Frame-Zähler.
        if not self.sliding():
            self._paint_handle()
        self._start_glow()          # hört von selbst auf, sobald nichts mehr atmet

    def _show_handle(self):
        if self.handle is None:
            return
        try:
            self.handle.deiconify()
            self.handle.lift()
            self.handle.attributes("-topmost", True)
            # deiconify() ist nur eine ANFORDERUNG – Tk fuehrt sie im Leerlauf aus.
            # Das Bild darf aber erst danach hinein: ein noch verstecktes layered
            # Fenster lehnt UpdateLayeredWindow ab (siehe _paint_layered). Ohne dieses
            # eine update_idletasks() waere der Griff beim ersten Auftauchen leer.
            self.handle.update_idletasks()
        except tk.TclError:
            pass
        # Der Layer-Zustand ist nach dem Ein-/Ausblenden verworfen (das Bit steht noch,
        # aber Windows nimmt kein Bild mehr an) -> hier NEU anlegen, bevor gemalt wird.
        # Genau das war die Ursache dafuer, dass der Griff als dunkler Kasten mit einer
        # Linie erschien statt als Kapsel.
        if hrender.AVAILABLE:
            self._enable_alpha(force=True)
        self._handle_shown = True
        # Der Layer-Zustand ist neu, das Fenster war eben noch versteckt: was zuletzt
        # geschoben wurde, gilt nicht mehr. Ohne dieses Zurücksetzen hielte
        # _paint_layered ein identisches Bild für „schon drin" und der Griff bliebe
        # beim Auftauchen leer.
        self._last_bits = None
        self._paint_handle()        # mit dem aktuellen Status auftauchen, nicht mit dem alten
        self._start_glow()

    def _hide_handle(self):
        if self.handle is None:
            return
        self._set_accent_hot(False)   # nicht „hot" wegblenden (<Leave> kommt bei withdraw nicht sicher)
        self._set_grip_hot(False)     # dito: sonst klebt beim nächsten Auftauchen der fleur-Zeiger
        self._handle_shown = False
        self._stop_glow()            # aufgeklappt läuft kein Timer
        self._bloom = 0.0
        try:
            self.handle.withdraw()
        except tk.TclError:
            pass
