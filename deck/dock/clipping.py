"""Das Fenster an der Bildschirmkante beschneiden.

Ohne das ragt der Slide auf den Nachbar-Monitor jenseits der Kante und die
Animation sieht doppelt aus. Falle: eine Region pro Frame annulliert Tks
geometry, wenn kein update_idletasks dazwischen liegt.
"""
import math
import tkinter as tk

from deck.dock.metrics import CLIP_QUANT, EDGE_GAP
from deck.platform import clip as wclip


class ClippingMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
            self._clip_on = wclip.screen_beyond(self.edge, self._edge_pos())
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
            wclip.clip_window(self.app.my_hwnd, self.edge, cut, extent)
        except Exception:
            pass
        self._clip_px = cut

    def _clear_clip(self):
        """Beschneidung aufheben (bündig aufgeklappt, abgedockt, Rand gewechselt)."""
        if not self._clip_px:
            return
        try:
            wclip.clip_window(self.app.my_hwnd, self.edge, 0)
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
