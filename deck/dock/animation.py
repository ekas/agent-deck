"""Der Slide quer zum Rand - und die Landung, die den Rand kurz nachleuchten
laesst.

HIER LIEGEN DIE DREI SICHERUNGEN, UND SIE GEHOEREN ZUSAMMEN: ein halb
ausgefahrenes Deck ist der eine unzulaessige Zustand (angedockt gibt es keine
Titelleiste, man kommt an nichts mehr heran). Deshalb gibt es genau EINEN
Ausgang aus der Animation (_anim_finish), eine Deadline als Notbremse und
einen Watchdog gegen den ausgefallenen Frame-Timer. Keine der drei
wegoptimieren - jede hat schon einmal gegriffen.

Die Kurve ist eine kritisch gedaempfte Feder (_spring_at), kein smoothstep:
eine symmetrische Kurve liest sich mechanisch. Der Takt haengt an der
Bildperiode des Monitors (frame_tick_ms), nicht an einem festen Intervall.
"""
import math
import tkinter as tk

from deck.dock.metrics import (
    ANIM_DEADLINE_MS,
    BORDER_COLOR,
    BORDER_LAND_FRAMES,
    BORDER_LAND_MS,
    BORDER_LAND_WHITE,
    COLLAPSE_RESPONSE_MS,
    EDGE_GAP,
    POLL_MS,
    REVEAL_RESPONSE_MS,
    SPRING_SETTLE_PX,
    frame_tick_ms,
    handle_thick,
)
from deck.platform import timing as wtime
from deck.render.kit import mix as _mix


class AnimationMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
            wtime.timer_precision_begin()
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
            wtime.timer_precision_end()
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
        delay = round(a["tick"] - (self._now_ms() - t0))
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
        return round(self._slide_span() * (1.0 - self._clamp(v, 0.0, 1.0)))

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
