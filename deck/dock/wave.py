"""Der Kern der Kapsel schwappt, und das Neon atmet.

Gemessene Einsicht: die Warm-Schicht allein ist der schwaechste Hebel (21 von
255 Graustufen, weil 16% Weiss auf Vollfarbe trifft und die Deckung in der
Mitte auf 255 klemmt). Es braucht Koerper-Helligkeit, Weissglut und Bloom je
Zeile. Wellenbilder NICHT cachen; _last_bits haelt die
UpdateLayeredWindow-Aufrufe unten.
"""
import sys
import tkinter as tk

from deck.platform import focus as wf
from deck.platform import layered as wlayer
from deck.render import capsule as hrender
from deck.render import capsule_masks as cmask
from deck.render import fluid as hwave

from deck.dock.metrics import HANDLE_THICK, LAYER_ERR_PATH, NEON_DECAY, NEON_LAYERS, NEON_MS, WAVE_ON, neon_color, neon_tint


class WaveMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
        prof = self._wave_profile(cmask._canon(w, h, self.edge)[1])
        bits = hrender.handle_bits(w, h, self.edge, HANDLE_THICK, col, eff,
                                   hot=self._hot, prof=prof)
        if bits is not None:
            # Im selben Bild steckt nichts Neues -> nicht schieben. Zwischen zwei
            # Wellen-Stößen steht das Wasser still, dort liefert handle_bits genau
            # dasselbe (gecachte) Objekt wieder; ohne diese Zeile ginge trotzdem
            # 30x je Sekunde ein UpdateLayeredWindow an Windows.
            if bits is self._last_bits:
                return
            if wlayer.layered_push(self._handle_hwnd, bits, w, h):
                self._last_bits = bits
                return
            # Zweiter Versuch mit frisch gemessenem HWND und NEU angelegtem Layer-
            # Zustand (force): Tk baut Fenster gelegentlich neu auf, und nach einem
            # Ein-/Ausblenden ist der Layer-Zustand verworfen, obwohl das Bit noch
            # steht – ohne force liefe der Versuch ins Leere (genau daran ist es
            # einmal gescheitert, siehe win_focus.layered_enable).
            self._enable_alpha(force=True)
            if self._layered and wlayer.layered_push(self._handle_hwnd, bits, w, h):
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
        msg = (f"layered_push fehlgeschlagen: {wlayer.LAST_ERROR}\n"
               f"  Bild {w}x{h}, Kante {self.edge}, sichtbar={self._handle_shown}\n"
               f"  {wlayer.layer_probe(self._handle_hwnd)}\n"
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
