"""Auf- und Zuklappen anstossen.

„Oeffnet sich nicht mehr“ ist meistens nur „eingeklappt“: ein Zweitstart
klappt darum per Marker-Datei auf (reveal_for_request mit Haltefrist).
"""
import tkinter as tk
from typing import Any

from deck.dock.metrics import REQUEST_HOLD_MS, REVEAL_LOCK_MS


class RevealMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

    def reveal(self) -> None:
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

    def reveal_for_request(self, hold_ms=REQUEST_HOLD_MS) -> None:
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

    def collapse(self) -> None:
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

    def _collapse_now(self) -> None:
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

    def _is_shown(self) -> Any:
        try:
            return self.root.state() != "withdrawn"
        except tk.TclError:
            return False
