"""Ein Fenster an der Bildschirmkante beschneiden (SetWindowRgn).

Gebraucht beim Ein- und Ausfahren des Randdocks: ohne Beschnitt ragt der Slide auf den
Nachbar-Monitor jenseits der Kante, und die Animation sieht doppelt aus.

Falle: eine neu gesetzte Region annulliert Tks geometry, wenn kein update_idletasks
dazwischen liegt - darum wird die Region gequantelt und nur bei echter Aenderung neu
gesetzt.
"""
import ctypes
from ctypes import wintypes
from typing import Any

from deck.platform.win32 import gdi32, user32

# ── Fenster an der Andock-Kante beschneiden ─────────────
# Die Slide-Animation des Edge-Docks schiebt das Fenster ueber den Bildschirmrand
# hinaus – und verlaesst sich darauf, dass der Teil jenseits der Kante einfach
# abgeschnitten wird. Das stimmt nur, solange DORT nichts mehr ist: liegt ein
# ZWEITER MONITOR jenseits der Kante, taucht der „versteckte" Teil auf ihm auf und
# fliegt dort als Geisterbild mit. Also schneiden wir ihn selbst weg (SetWindowRgn).
#
# Bewusst nur wenn noetig (screen_beyond): eine Fenster-Region ersetzt die
# Fensterform und nimmt dabei die weiche DWM-Rundung (round_corners) mit – an einem
# echten Bildschirmrand waere das ein Verlust ohne Gegenwert.
_MonEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """Rechtecke aller Monitore in Bildschirmkoordinaten [(l, t, r, b), …]."""
    out: list[tuple[int, int, int, int]] = []

    def cb(_hmon: Any, _hdc: Any, lprc: Any, _data: Any) -> bool:
        r = lprc.contents
        out.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        user32.EnumDisplayMonitors(None, None, _MonEnumProc(cb), 0)
    except Exception:
        return []
    return out


def screen_beyond(side: str, pos: int) -> bool:
    """Liegt JENSEITS der Kante noch echte Monitorflaeche? `side` ist 'left',
    'right' oder 'top', `pos` deren Bildschirmkoordinate (x bzw. y).

    Nur dann kann ein ueber die Kante hinausgeschobenes Fenster dort sichtbar
    werden. Der Zwischenraum, den unterschiedlich skalierte Monitore im
    Koordinatenraum hinterlassen, zaehlt bewusst nicht – dort rendert nichts."""
    for left, top, right, _bottom in monitor_rects():
        if (side == "left" and left < pos) or (side == "right" and right > pos) \
                or (side == "top" and top < pos):
            return True
    return False


def clip_window(hwnd: int, side: str, cut: float, extent: float = 0) -> None:
    """Die ersten `cut` px des Fensters an `side` unsichtbar machen; `cut` <= 0
    nimmt die Beschneidung wieder zurueck (und damit die DWM-Rundung zurueck).

    `cut`/`extent` duerfen in Tk-Koordinaten kommen: ist `extent` (die
    Fensterausdehnung quer zum Rand im SELBEN Raum) angegeben, wird `cut` auf die
    per GetWindowRect gemessene Groesse umgerechnet – damit ist es einerlei, ob
    Windows den Prozess DPI-virtualisiert."""
    if not hwnd:
        return
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return
    span = w if side in ("left", "right") else h
    if extent > 0 and span != extent:      # anderer Pixel-Raum -> anteilig umrechnen
        cut = cut * float(span) / float(extent)
    cut = round(max(0, min(cut, span)))
    if cut <= 0:
        user32.SetWindowRgn(hwnd, None, True)     # Region weg = wieder ganzes Fenster
        return
    if side == "left":
        box = (cut, 0, w, h)
    elif side == "right":
        box = (0, 0, w - cut, h)
    else:                                          # top
        box = (0, cut, w, h)
    rgn = gdi32.CreateRectRgn(*box)
    if not rgn:
        return
    # Ab hier gehoert die Region dem System – nur wenn SetWindowRgn sie NICHT
    # uebernommen hat, muessen wir sie selbst freigeben (sonst GDI-Leck je Frame).
    if not user32.SetWindowRgn(hwnd, rgn, True):
        gdi32.DeleteObject(rgn)
