"""Rahmenlos an- und abdocken.

Angedockt gibt es KEINE Titelleiste - also auch kein Schliessen-Kreuz. Das
Deck verlaesst diesen Zustand nur ueber den Weg, der es hineingebracht hat.
"""
import tkinter as tk

from deck.dock.metrics import BORDER_COLOR, BORDER_PX
from deck.platform import focus as wf


class FramelessMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

    def _enter_frameless(self) -> None:
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

    def _round_corners(self) -> None:
        """Leicht runde Ecken auch im rahmenlosen Zustand (per DWM, weich gerendert).
        Ohne native Titelleiste kaeme sonst ein hart eckiger Slab heraus.

        Wird zusaetzlich bei jedem Aufklappen gesetzt: der Aufruf ist billig und
        idempotent, und so ist die Rundung selbst dann da, wenn Tk das HWND
        zwischenzeitlich neu erzeugt hat (dabei gehen DWM-Attribute verloren)."""
        try:
            wf.round_corners(self.app.my_hwnd, small=True)
        except Exception:
            pass

    def _undock(self) -> None:
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

    def _refresh_hwnd(self) -> None:
        try:
            self.root.update_idletasks()
            self.app.my_hwnd = wf.toplevel_hwnd(self.root.winfo_id())
        except Exception:
            pass

    def _reassert_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass
