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
    (dagegen wtime.timer_precision_begin für die Dauer des Slides), Tks `clock
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
from deck.dock.animation import AnimationMixin
from deck.dock.clipping import ClippingMixin
from deck.dock.frameless import FramelessMixin
from deck.dock.geometry import GeometryMixin
from deck.dock.handle import HandleMixin
from deck.dock.metrics import BORDER_COLOR, HANDLE_ACCENT, scale_metrics
from deck.dock.poll import PollMixin
from deck.dock.reveal import RevealMixin
from deck.dock.wave import WaveMixin
from deck.domain import config as cfg
from deck.render import capsule as hrender


class EdgeDock(
        FramelessMixin, RevealMixin, AnimationMixin, ClippingMixin, PollMixin, GeometryMixin, HandleMixin, WaveMixin,
):
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
            self._bloom = cfg.BLOOM_ON_CHANGE
            self._wave_kick()
        self._paint_handle()
        self._start_glow()
