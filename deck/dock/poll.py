"""Zeiger beobachten, um einzuklappen.

Beobachtet wird NUR das Einklappen; das Aufklappen kommt vom Hover auf dem
Griff. Weil Tk ein <Enter> verschlucken kann, fragt _poll_reveal zusaetzlich
nach - sonst reagiert der Griff gelegentlich gar nicht.
"""
import time
import tkinter as tk

from deck.dock.metrics import COLLAPSE_DELAY_MS, INSIDE_MARGIN, POLL_MS, capsule_extent


class PollMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
