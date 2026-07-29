"""Zeit-Takt fuer Animationen: Timer-Aufloesung und Bildwiederholrate.

Beides gehoert zusammen, weil beides denselben Zweck hat - eine Animation, die nicht
stottert. Windows tickt standardmaessig nur alle 15,6 ms; ohne timeBeginPeriod(1) faellt
jeder Frame-Timer auf dieses Raster und die Bewegung ruckelt sichtbar.

Und der Zieltakt ist die Bildperiode des MONITORS, nicht ein fester Wert: 100 fps auf
einem 60-Hz-Schirm sind kein glatteres Bild, sondern verworfene Frames.
"""
from ctypes import wintypes
import ctypes

from deck.platform.win32 import gdi32
from deck.platform.win32 import user32


# ── Timer-Aufloesung waehrend einer Animation anheben ───
# Windows tickt seine Timer standardmaessig nur alle 15,625 ms (64 Hz). Ein
# root.after(8) kommt deshalb NICHT nach 8 ms zurueck, sondern nach 15,6 – und je
# nachdem, wo der Aufruf im laufenden Tick landet, auch mal erst nach 31. Fuer
# eine 170-ms-Animation heisst das: statt ~21 gleichmaessiger Frames kommen ~11
# ungleichmaessige. Genau das sieht man als Stottern, und kein noch so sauberer
# zeitbasierter Fortschritt kann es glaetten – die Frames FEHLEN einfach.
#
# timeBeginPeriod(1) senkt die Periode auf 1 ms. Seit Windows 10 2004 wirkt das
# nur noch fuer den eigenen Prozess (frueher global), ist also unbedenklich – es
# muss aber wieder freigegeben werden, sonst bleibt der Prozess dauerhaft im
# schnellen Takt und kostet unnoetig Strom. Darum die Zaehlung: erst das letzte
# end() gibt frei, ein doppeltes begin() (Slide waehrend eines Slides) kann also
# nichts durcheinanderbringen.
#
# Quelle der Zahlen: Bruce Dawson, „Windows Timer Resolution: The Great Rule
# Change" (randomascii) und die timeBeginPeriod-Doku bei Microsoft.
try:
    _winmm = ctypes.WinDLL("winmm")
except OSError:
    _winmm = None
_timer_depth = 0


def timer_precision_begin(ms=1):
    """1-ms-Timer anfordern (fuer die Dauer einer Animation). Immer paarweise mit
    timer_precision_end() verwenden."""
    global _timer_depth
    if _winmm is None:
        return
    if _timer_depth == 0:
        try:
            _winmm.timeBeginPeriod(int(ms))
        except Exception:
            return
    _timer_depth += 1


def timer_precision_end(ms=1):
    """Die mit timer_precision_begin() angeforderte Aufloesung wieder freigeben."""
    global _timer_depth
    if _winmm is None or _timer_depth <= 0:
        return
    _timer_depth -= 1
    if _timer_depth == 0:
        try:
            _winmm.timeEndPeriod(int(ms))
        except Exception:
            pass


# ── Bildwiederholrate des Bildschirms ───────────────────
# Die eine Zahl, an der ein Animations-Takt haengt: mehr Frames zu rechnen, als der
# Monitor Bilder zeigt, ist nicht bloss verschenkt – es macht die Bewegung SCHLECHTER
# (ausfuehrlich bei edge_dock.frame_tick_ms).
#
# Monitorgenau, nicht nur der primaere Bildschirm: Laptop-Panel und externer Schirm
# laufen oft mit verschiedenen Raten, und das Deck klebt am Rand irgendeines von
# beiden. Der Weg dorthin ist EnumDisplaySettingsW ueber den Geraetenamen des
# Monitors unter dem Fenster.
#
# Bewusst NICHT DwmGetCompositionTimingInfo: das liefert die Periode exakter, verlangt
# aber eine ~200-Byte-Struktur, deren Layout zwischen Windows-Versionen gewachsen ist –
# passt cbSize nicht aufs Byte, gibt es nur E_INVALIDARG (hier gemessen, ohne jeden
# Hinweis auf die Ursache). Fuer einen Takt in ganzen Millisekunden reicht die
# ganzzahlige Rate.
_VREFRESH = 116                  # GetDeviceCaps-Index (Rueckfall: primaerer Schirm)
_ENUM_CURRENT_SETTINGS = -1
_MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class _DEVMODEW(ctypes.Structure):
    """Nur bis dmDisplayFrequency ausgefuellt gebraucht – der Rest muss trotzdem da
    sein, weil dmSize die GANZE Struktur meint (sizeof == 220)."""
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


def refresh_hz(hwnd=None, default=60):
    """Bilder je Sekunde des Monitors unter `hwnd` (ohne hwnd: primaerer Schirm).

    `default`, wenn Windows nur „Hardware-Standard" meldet (0 oder 1) oder der Weg
    nicht gangbar ist – eine falsche Rate darf hier nie zu einem Fehler fuehren, sie
    macht eine Animation hoechstens weniger glatt."""
    hz = 0
    if hwnd:
        try:
            mon = user32.MonitorFromWindow(wintypes.HWND(int(hwnd)),
                                           _MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                dm = _DEVMODEW()
                dm.dmSize = ctypes.sizeof(_DEVMODEW)
                if user32.EnumDisplaySettingsW(info.szDevice, _ENUM_CURRENT_SETTINGS,
                                               ctypes.byref(dm)):
                    hz = int(dm.dmDisplayFrequency)
        except Exception:
            hz = 0
    if hz <= 1:                                   # Rueckfall: primaerer Bildschirm
        try:
            dc = user32.GetDC(None)
            if dc:
                try:
                    hz = int(gdi32.GetDeviceCaps(dc, _VREFRESH))
                finally:
                    user32.ReleaseDC(None, dc)
        except Exception:
            hz = 0
    return hz if hz > 1 else default
