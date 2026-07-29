"""Das Griff-Fenster: anlegen, Zonen, Hover/Klick/Ziehen, zeichnen, Neon.

Der Griff ist eine freistehende Kapsel per Per-Pixel-Alpha. HAUPTFALLE:
withdraw -> deiconify verwirft den Layer-Zustand, danach scheitert
UpdateLayeredWindow mit Fehler 87, obwohl Bit, Groesse und Sichtbarkeit
stimmen - darum wird die Alpha-Schicht beim Zeigen erzwungen neu gesetzt.

Kapsel (16px) und Fenster (29px) sind NICHT gleich gross: alles Geometrische
laeuft ueber handle_thick(), nie ueber die Fensterbreite.
"""
import math
import tkinter as tk

from deck.dock.metrics import (
    DRAG_THRESH,
    HANDLE_BG,
    HANDLE_MAX_LEN,
    HANDLE_MIN_LEN,
    HANDLE_THICK,
    HOVER_REVEAL_MS,
    NEON_FLOOR,
    NEON_LAYERS,
    NEON_PULSE_TICKS,
    capsule_extent,
    handle_thick,
)
from deck.platform import focus as wf
from deck.platform import layered as wlayer
from deck.render import capsule as hrender


class HandleMixin:
    """Wird in EdgeDock eingemischt (siehe controller.py)."""

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
            self._layered = wlayer.layered_enable(self._handle_hwnd, force=force)
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
