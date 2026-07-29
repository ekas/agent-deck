"""Per-Pixel-Alpha: ein fertiges RGBA-Bild direkt ins Fenster schieben.

So entsteht die freistehende Neon-Kapsel des Griffs - ohne Rechteck, ohne Hintergrund.

HAUPTFALLE: withdraw -> deiconify verwirft den Layer-Zustand des Fensters. Danach
scheitert UpdateLayeredWindow mit Fehler 87 (ERROR_INVALID_PARAMETER), obwohl Bittiefe,
Groesse und Sichtbarkeit stimmen - die Ursache ist der verlorene Zustand, nicht das Bild.
Darum muss die Alpha-Schicht beim Zeigen erzwungen neu gesetzt werden (layered_enable mit
force). Ein Test ohne verstecktes Fenster beweist hier NICHTS.

Die Bytes muessen vormultipliziertes BGRA sein, die Hoehe negativ (top-down).
"""
from ctypes import wintypes
import ctypes

from deck.platform.win32 import gdi32
from deck.platform.win32 import user32


# ── Per-Pixel-Alpha (fuer den Griff-Balken) ─────────────
# Tk kennt nur zwei Arten Transparenz: das GANZE Fenster halbdurchsichtig (-alpha)
# oder EINE Farbe komplett ausgestanzt (-transparentcolor). Beides ist
# SetLayeredWindowAttributes, und fuer einen weichen Leuchthof reicht es nicht: der
# Rand eines Verlaufs besteht aus MISCHPIXELN, und ein Farb-Key laesst genau die
# stehen – als dunkler Saum rings um das Leuchten.
#
# Windows kann es trotzdem: WS_EX_LAYERED zusammen mit UpdateLayeredWindow nimmt
# eine Bitmap MIT Alphakanal je Pixel. Das Fenster wird dann vollstaendig aus
# unserem Bild gezeichnet – Tk malt in so ein Fenster nicht mehr hinein (es bekommt
# kein WM_PAINT mehr), was hier gerade recht ist: der Inhalt kommt ohnehin aus
# Pillow.
#
# Drei Dinge, die man dabei wissen muss:
#  • Das Alpha muss VORMULTIPLIZIERT sein (jeder Farbkanal schon mit dem Alpha
#    verrechnet), sonst bekommen weiche Kanten einen hellen Saum.
#  • Die Bitmap liegt als BGRA vor, nicht RGBA, und mit NEGATIVER Hoehe, sonst
#    steht das Bild auf dem Kopf (Windows-Bitmaps laufen von unten nach oben).
#  • Der Maus-Hit-Test folgt dem Alpha: wo es 0 ist, klickt man durch. Fuer den
#    Griff ist das erwuenscht (nur das Leuchten ist anfassbar) – das Aufklappen
#    darf sich dann aber nicht allein auf Tk-Events verlassen, siehe
#    edge_dock._poll_reveal, der genau dafuer schon da ist.
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_ULW_ALPHA = 0x02
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_BI_RGB = 0
_DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


try:
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
except (AttributeError, OSError):
    pass


def layer_probe(hwnd):
    """Zustand eines Fensters aus Sicht der Layer-APIs, als Text – fuer die Fehlersuche,
    wenn layered_push scheitert. Genau die drei Dinge, die UpdateLayeredWindow ablehnen
    lassen: Fenster weg, WS_EX_LAYERED nicht (mehr) gesetzt, oder das Fenster steckt im
    Attribut-Modus (SetLayeredWindowAttributes, siehe layered_push)."""
    try:
        alive = bool(user32.IsWindow(hwnd))
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        key, alpha, flags = wintypes.COLORREF(), ctypes.c_ubyte(), wintypes.DWORD()
        attr = bool(user32.GetLayeredWindowAttributes(
            hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)))
        return (f"hwnd={hwnd} lebt={alive} exstyle=0x{ex:X} "
                f"layered={bool(ex & _WS_EX_LAYERED)} "
                f"rect={r.right - r.left}x{r.bottom - r.top} "
                f"attr_modus={attr}(alpha={alpha.value},flags=0x{flags.value:X})")
    except Exception as e:                             # noqa: BLE001 (Diagnose)
        return f"probe fehlgeschlagen: {e}"


def layered_enable(hwnd, force=False):
    """Das Fenster fuer Per-Pixel-Alpha vorbereiten (WS_EX_LAYERED setzen).

    `force` legt das Bit NEU an: erst loeschen, dann wieder setzen. Das ist noetig,
    nachdem das Fenster versteckt und wieder eingeblendet wurde – dabei verwirft
    Windows den Layer-Zustand, und UpdateLayeredWindow lehnt danach mit
    ERROR_INVALID_PARAMETER ab, OBWOHL WS_EX_LAYERED noch gesetzt ist. Genau diese
    Falle hat den Griff einmal komplett gekostet (dunkler Kasten statt Kapsel), und
    sie ist von aussen nicht zu sehen: Stil, Groesse und Fenster sind alle in Ordnung.
    Gemessen gilt ausserdem: ein SetWindowLongW mit demselben Wert genuegt NICHT, es
    braucht den echten Wechsel 0 -> 1.

    Rueckgabe True, wenn das Bit anschliessend wirklich steht – geprueft durch ERNEUTES
    Lesen, nicht am Rueckgabewert von SetWindowLongW: der liefert den VORHERIGEN Stil,
    und 0 ist dort ein gueltiger Wert, taugt also nicht als Erfolgsmeldung."""
    if not hwnd:
        return False
    try:
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if ex & _WS_EX_LAYERED:
            if not force:
                return True
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex & ~_WS_EX_LAYERED)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
        return bool(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_LAYERED)
    except Exception:
        return False


LAST_ERROR = ""   # Grund des letzten layered_push-Fehlschlags (siehe dort)


def _layer_fail(step):
    """Fehlschlag festhalten. Ein stiller Rueckfall ist hier das Schlimmste, was
    passieren kann: das Fenster sieht dann anders aus als gedacht, und niemand weiss
    warum. Der Aufrufer gibt LAST_ERROR aus, wenn er aufgibt."""
    global LAST_ERROR
    LAST_ERROR = f"{step} (GetLastError={ctypes.get_last_error()})"
    return False


def layered_push(hwnd, bits, w, h):
    """Ein Bild MIT Alphakanal ins Fenster schieben (UpdateLayeredWindow).

    `bits` sind w*h*4 Bytes BGRA mit VORMULTIPLIZIERTEM Alpha. Die Fenster-POSITION
    bleibt unberuehrt (pptDst = NULL) – sie gehoert Tk bzw. der Slide-Animation, ein
    Setzen von hier aus wuerde sich mit ihr streiten. Rueckgabe False bei jedem
    Fehlschlag; der Aufrufer faellt dann auf einen anderen Zeichenweg zurueck und
    kann in LAST_ERROR nachsehen, woran es lag."""
    if not hwnd:
        return _layer_fail("kein HWND")
    if w <= 0 or h <= 0 or len(bits) != w * h * 4:
        return _layer_fail(f"Bildgroesse passt nicht ({w}x{h}, {len(bits)} Bytes)")
    screen = memdc = hbmp = old = None
    try:
        screen = user32.GetDC(None)
        if not screen:
            return _layer_fail("GetDC")
        memdc = gdi32.CreateCompatibleDC(screen)
        if not memdc:
            return _layer_fail("CreateCompatibleDC")
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h                 # negativ = von oben nach unten
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = _BI_RGB
        ppv = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), _DIB_RGB_COLORS,
                                      ctypes.byref(ppv), None, 0)
        if not hbmp or not ppv:
            return _layer_fail("CreateDIBSection")
        ctypes.memmove(ppv, bits, len(bits))
        old = gdi32.SelectObject(memdc, hbmp)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        if user32.UpdateLayeredWindow(
                hwnd, screen, None, ctypes.byref(size), memdc, ctypes.byref(src),
                0, ctypes.byref(blend), _ULW_ALPHA):
            global LAST_ERROR
            LAST_ERROR = ""      # Erfolg -> alten Grund nicht stehen lassen
            return True
        return _layer_fail("UpdateLayeredWindow")
    except Exception as e:                             # noqa: BLE001 (Grund festhalten)
        return _layer_fail(f"Ausnahme {type(e).__name__}: {e}")
    finally:
        # Jede angeforderte GDI-Resource wieder hergeben: das laeuft im Puls-Takt
        # des Griffs, ein Leck je Frame waere nach Minuten sichtbar.
        try:
            if memdc and old:
                gdi32.SelectObject(memdc, old)
            if hbmp:
                gdi32.DeleteObject(hbmp)
            if memdc:
                gdi32.DeleteDC(memdc)
            if screen:
                user32.ReleaseDC(None, screen)
        except Exception:
            pass
