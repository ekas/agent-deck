"""Kanten-, Span- und Ankerrechnerei, dazu die gemerkte Position am Rand.

Alles in reinen Tk-Koordinaten (die DPI-Umrechnung passiert in metrics).
EDGE_GAP ist kein Schoenheitsabstand: Windows 11 malt bei runden Ecken seinen
grauen Rand ueber die aeusserste Pixelreihe, buendig sitzt also nicht.
"""
import tkinter as tk

from deck.dock.metrics import EDGES, EDGE_GAP


class GeometryMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
