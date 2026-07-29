"""Windows: Fenster nach vorn holen + native Titelleiste umstylen (reines ctypes).

Wird von der App benutzt, um beim Klick auf eine Kachel das richtige VS-Code-
Fenster zu finden und nach vorn zu holen (find_window + focus_window) sowie die
eigene Titelleiste dunkel zu stylen (style_titlebar). Das Fokussieren des
einzelnen Terminals (Pane) macht die Extension per Broker – nicht mehr dieses
Modul.
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ── ctypes-Signaturen: Pflicht, nicht Kosmetik ───────────
# Ohne Deklaration nimmt ctypes fuer JEDEN Rueckgabewert `c_int` an und uebergibt
# Python-ints als C-`int` – beides 32 Bit. Handles (HWND/HDC/HGDIOBJ/HMONITOR) sind
# auf 64-Bit-Windows aber 64 Bit breit. Ein Handle wird so beim Holen abgeschnitten
# bzw. beim Weitergeben vorzeichenerweitert, und dann trifft die Operation ein
# ANDERES Objekt: ReleaseDC/DeleteDC geben im schlimmsten Fall einen fremden
# Geraetekontext frei. Das faellt nicht dort auf, wo es passiert – es korrumpiert
# den GDI-/Heap-Zustand des Prozesses, und der Absturz kommt spaeter an der
# aktivsten Allokationsstelle (hier: der Bildpfad des Glow-Timers, siehe
# glow_animator._paint_image_tile im Crash-Dump vom 2026-07-28).
#
# Darum werden ALLE benutzten Funktionen typisiert – auch die, bei denen es
# „bisher lief". Es lief, weil Handle-Werte meist klein sind; das ist Glueck, keine
# Zusage. Die Deklarationen stehen bewusst hier zusammen und nicht verstreut.
def _decl(lib, name, restype, *argtypes):
    """argtypes/restype setzen, aber an einer fehlenden Funktion nicht scheitern:
    der Name wird per getattr aufgeloest (eine aeltere Windows-Version kennt sie
    dann eben nicht, und der jeweilige Aufrufer faellt selbst zurueck). Ein
    `_decl(user32.Fehlt, …)` waere dagegen ein AttributeError beim IMPORT – das
    Panel wuerde ueberhaupt nicht mehr starten."""
    fn = getattr(lib, name, None)
    if fn is None:
        return
    if restype is not None:
        fn.restype = restype
    if argtypes:
        fn.argtypes = list(argtypes)


_HDC = wintypes.HDC
_HWND = wintypes.HWND
_HMONITOR = wintypes.HANDLE

# Geraetekontexte (laufen im Frame-Takt des Griffs -> siehe layered_push)
_decl(user32, "GetDC", _HDC, _HWND)
_decl(user32, "ReleaseDC", ctypes.c_int, _HWND, _HDC)
_decl(gdi32, "DeleteDC", wintypes.BOOL, _HDC)
_decl(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
_decl(gdi32, "GetDeviceCaps", ctypes.c_int, _HDC, ctypes.c_int)
# Monitor-/Anzeigeabfragen
_decl(user32, "MonitorFromWindow", _HMONITOR, _HWND, wintypes.DWORD)
_decl(user32, "GetMonitorInfoW", wintypes.BOOL, _HMONITOR, ctypes.c_void_p)
_decl(user32, "EnumDisplaySettingsW", wintypes.BOOL, wintypes.LPCWSTR,
      wintypes.DWORD, ctypes.c_void_p)
# Fenster-Grundfunktionen
_decl(user32, "IsWindow", wintypes.BOOL, _HWND)
_decl(user32, "ShowWindow", wintypes.BOOL, _HWND, ctypes.c_int)
_decl(user32, "BringWindowToTop", wintypes.BOOL, _HWND)
_decl(user32, "SetForegroundWindow", wintypes.BOOL, _HWND)
_decl(user32, "AttachThreadInput", wintypes.BOOL, wintypes.DWORD, wintypes.DWORD,
      wintypes.BOOL)
_decl(user32, "GetWindowThreadProcessId", wintypes.DWORD, _HWND,
      ctypes.POINTER(wintypes.DWORD))
_decl(user32, "GetLayeredWindowAttributes", wintypes.BOOL, _HWND,
      ctypes.POINTER(wintypes.COLORREF), ctypes.POINTER(ctypes.c_ubyte),
      ctypes.POINTER(wintypes.DWORD))
_decl(kernel32, "GetCurrentThreadId", wintypes.DWORD)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long   # HRESULT
except OSError:
    dwmapi = None   # sehr altes Windows -> Titelleiste bleibt Standard

# ── Fenster anhand des Titels finden ────────────────────
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_window(*needles):
    """HWND des ersten sichtbaren Fensters, dessen Titel ALLE needles enthaelt."""
    hits = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if t and all(nd.lower() in t.lower() for nd in needles):
                hits.append(hwnd)
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return hits[0] if hits else None


def list_titles(*needles):
    """Titel ALLER sichtbaren Fenster, deren Titel ALLE needles (case-insensitiv)
    enthaelt. Gedacht, um festzustellen, welche VS-Code-Fenster ueberhaupt noch offen
    sind (z.B. list_titles(VSCODE_MARKER)) – ein geschlossenes Fenster taucht nicht mehr
    auf, ein bloss neu ladendes/minimiertes schon (WS_VISIBLE bleibt bei minimiert)."""
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if t and all(nd.lower() in t.lower() for nd in needles):
                out.append(t)
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return out


user32.GetForegroundWindow.restype = wintypes.HWND


def foreground_hwnd():
    """HWND des aktuell aktiven Fensters (fuer 'klick-zum-Verbinden')."""
    return user32.GetForegroundWindow()


def title_of(hwnd):
    """Fenstertitel zu einem HWND (leerer String, wenn keiner)."""
    return _title(hwnd) if hwnd else ""


# ── Tk-Fensterhandle aufloesen ──────────────────────────
_GA_ROOT = 2
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


def toplevel_hwnd(child_id):
    """Von der Tk-winfo_id zum echten Top-Level-Fensterhandle (GA_ROOT)."""
    return user32.GetAncestor(child_id, _GA_ROOT) or child_id


# ── Native Titelleiste per DWM umstylen (Win11) ─────────
# Aus der grauen Standard-Leiste wird eine dunkle mit Cyan-Rand + runden Ecken.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20   # BOOL – dunkle Leiste (Win10 20H1+/Win11)
_DWMWA_WINDOW_CORNER_PREFERENCE = 33  # int  – 2 = runde Ecken
_DWMWA_BORDER_COLOR = 34              # COLORREF (0x00BBGGRR) – Fensterrand
_DWMWA_CAPTION_COLOR = 35             # COLORREF – Fuellung der Titelleiste
_DWMWA_TEXT_COLOR = 36                # COLORREF – Titeltext
_DWMWCP_ROUND = 2
_DWMWCP_ROUNDSMALL = 3                # kleiner Radius (~4 px) statt Standard (~8 px)


def _colorref(hexstr):
    """'#RRGGBB' -> Windows-COLORREF (0x00BBGGRR)."""
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def _dwm_set(hwnd, attr, value):
    if not dwmapi:
        return
    val = ctypes.c_int(int(value))
    try:
        dwmapi.DwmSetWindowAttribute(wintypes.HWND(int(hwnd)), attr,
                                     ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass   # aeltere Builds kennen das Attribut nicht -> leise ignorieren


def style_titlebar(hwnd, *, dark=True, border=None, caption=None,
                   text=None, round_corners=True):
    """Native Titelleiste (Win11-DWM) umstylen: Dark-Mode, farbiger Rand, runde
    Ecken, dunkle Caption + heller Titeltext. Jede Eigenschaft ist einzeln
    gekapselt und faellt auf aelteren Windows leise auf die Standard-Leiste
    zurueck (border/caption/text brauchen Win11 22000+)."""
    if not hwnd or not dwmapi:
        return
    if dark:
        _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        _dwm_set(hwnd, 19, 1)   # aeltere Builds nutzten Attribut 19 fuer Dark-Mode
    if round_corners:
        _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
    if border is not None:
        _dwm_set(hwnd, _DWMWA_BORDER_COLOR, _colorref(border))
    if caption is not None:
        _dwm_set(hwnd, _DWMWA_CAPTION_COLOR, _colorref(caption))
    if text is not None:
        _dwm_set(hwnd, _DWMWA_TEXT_COLOR, _colorref(text))


def round_corners(hwnd, *, small=True):
    """Ecken eines Fensters per DWM runden – auch bei RAHMENLOSEN Fenstern
    (overrideredirect), die keine native Titelleiste/DWM-Kante mehr haben.

    small=True nimmt den kleinen Radius (~4 px, „leicht rund"), sonst den
    Standard (~8 px). DWM rendert die Rundung weich/antialiased und blendet den
    selbstgezeichneten Rand mit aus – im Gegensatz zu SetWindowRgn, das die Ecken
    hart abtreppt und den Rand in der Kurve abreissen laesst.

    Faellt auf Windows ohne dieses Attribut (< Win11 22000) leise auf eckig
    zurueck."""
    if not hwnd:
        return
    _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
             _DWMWCP_ROUNDSMALL if small else _DWMWCP_ROUND)


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


def monitor_rects():
    """Rechtecke aller Monitore in Bildschirmkoordinaten [(l, t, r, b), …]."""
    out = []

    def cb(_hmon, _hdc, lprc, _data):
        r = lprc.contents
        out.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        user32.EnumDisplayMonitors(None, None, _MonEnumProc(cb), 0)
    except Exception:
        return []
    return out


def screen_beyond(side, pos):
    """Liegt JENSEITS der Kante noch echte Monitorflaeche? `side` ist 'left',
    'right' oder 'top', `pos` deren Bildschirmkoordinate (x bzw. y).

    Nur dann kann ein ueber die Kante hinausgeschobenes Fenster dort sichtbar
    werden. Der Zwischenraum, den unterschiedlich skalierte Monitore im
    Koordinatenraum hinterlassen, zaehlt bewusst nicht – dort rendert nichts."""
    for l, t, r, _b in monitor_rects():
        if (side == "left" and l < pos) or (side == "right" and r > pos) \
                or (side == "top" and t < pos):
            return True
    return False


def clip_window(hwnd, side, cut, extent=0):
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
    cut = int(round(max(0, min(cut, span))))
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


# ── Groesse nur an der Ecke unten-rechts ziehbar ────────
# Ein natives Fenster (WS_THICKFRAME) laesst sich an ALLEN Kanten/Ecken ziehen.
# Per WNDPROC-Subclassing fangen wir WM_NCHITTEST ab: DefWindowProc rechnet die
# Trefferzone aus, und jede Resize-Zone AUSSER der Ecke unten-rechts geben wir als
# toten Rand (HTBORDER) zurueck. Dadurch zieht nur noch die Ecke unten-rechts die
# Groesse; die Seiten/oberen Ecken sind inaktiv, das Bewegen an der Titelleiste
# (HTCAPTION) bleibt unberuehrt. Programmatisches root.geometry() ist nicht
# betroffen (NCHITTEST steuert nur das Ziehen mit der Maus).
_WM_NCHITTEST = 0x0084
_GWLP_WNDPROC = -4
_HTBORDER = 18
_HTBOTTOMRIGHT = 17
# Alle Rahmen-Trefferzonen, die DefWindowProc als "hier zieht die Groesse" liefert
# (HTLEFT..HTBOTTOMRIGHT, 10..17):
_HT_RESIZE = frozenset(range(10, 18))

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
# 64-bit: SetWindowLongPtrW; 32-bit-Python kennt nur die Nicht-Ptr-Variante.
if hasattr(user32, "SetWindowLongPtrW"):
    _set_wndproc, _get_wndproc = user32.SetWindowLongPtrW, user32.GetWindowLongPtrW
else:
    _set_wndproc, _get_wndproc = user32.SetWindowLongW, user32.GetWindowLongW
_set_wndproc.argtypes = [wintypes.HWND, ctypes.c_int, _WNDPROC]
_set_wndproc.restype = ctypes.c_void_p
_get_wndproc.argtypes = [wintypes.HWND, ctypes.c_int]
_get_wndproc.restype = ctypes.c_void_p

# Subclass-Zustand je Fenster festhalten: die WNDPROC-Callbacks (und die alte Proc
# fuer die Aufrufkette) duerfen NICHT vom GC eingesammelt werden – sonst ruft
# Windows in freigegebenen Speicher (Absturz).
_resize_hooks = {}


def restrict_resize_to_corner(hwnd):
    """Fenstergroesse nur noch an der Ecke unten-rechts ziehbar machen.

    Haengt sich per Subclassing in WM_NCHITTEST und wandelt jede Resize-Zone ausser
    HTBOTTOMRIGHT in HTBORDER (toter Rand) – Seiten und obere Ecken ziehen also
    nicht mehr, nur die Ecke unten-rechts vergroessert/verkleinert. WS_THICKFRAME
    bleibt, damit diese Ecke nativ zieht. Idempotent (zweiter Aufruf fuers selbe
    Fenster tut nichts) und faellt bei Fehlern leise zurueck (Fenster bleibt dann
    normal ziehbar)."""
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return
    if not hwnd or hwnd in _resize_hooks:
        return

    def _proc(h, msg, wp, lp):
        _cb, old = _resize_hooks[hwnd]        # gehaltene Callback- + Alt-Proc-Refs
        res = user32.CallWindowProcW(old, h, msg, wp, lp)
        if msg == _WM_NCHITTEST and res in _HT_RESIZE and res != _HTBOTTOMRIGHT:
            return _HTBORDER
        return res

    try:
        cb = _WNDPROC(_proc)
        old = _get_wndproc(wintypes.HWND(hwnd), _GWLP_WNDPROC)
        if not old:
            return
        _resize_hooks[hwnd] = (cb, old)       # ERST halten, DANN einhaengen
        _set_wndproc(wintypes.HWND(hwnd), _GWLP_WNDPROC, cb)
    except OSError:
        _resize_hooks.pop(hwnd, None)


# ── Fenster in den Vordergrund holen ────────────────────
_SW_RESTORE = 9
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL


def focus_window(hwnd):
    """Holt hwnd zuverlaessig nach vorn (AttachThreadInput-Trick gegen die
    Windows-Sperre, die das Klauen des Vordergrunds normal verhindert).

    Restauriert NUR, wenn das Fenster minimiert ist - ein maximiertes/Vollbild-
    Fenster behaelt so seine Groesse (SW_RESTORE wuerde es sonst verkleinern)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fg_thr = user32.GetWindowThreadProcessId(fg, None)
    tgt_thr = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(cur, fg_thr, True)
    user32.AttachThreadInput(cur, tgt_thr, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(cur, fg_thr, False)
        user32.AttachThreadInput(cur, tgt_thr, False)


def windows_of_pid(pid):
    """Alle sichtbaren Top-Level-Fenster dieses Prozesses (Liste von hwnd).

    Reine Abfrage OHNE Nebenwirkung – wichtig fuer den Waechter (watchdog.py):
    der muss "lebt das Panel noch?" beantworten koennen, ohne dabei ein Fenster
    nach vorn zu holen (focus_pid tut genau das und wuerde dem Nutzer alle paar
    Minuten den Fokus klauen)."""
    hits = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                hits.append(hwnd)
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return hits


def has_window(pid):
    """True, wenn der Prozess mindestens ein sichtbares Fenster hat (nebenwirkungsfrei)."""
    return bool(windows_of_pid(pid))


def focus_pid(pid):
    """Erstes sichtbares Top-Level-Fenster des Prozesses <pid> nach vorn holen.
    True, wenn eins gefunden und fokussiert wurde, sonst False.

    Gedacht fuer den Single-Instance-Guard (single_instance.py): eine Zweit-
    Instanz holt so das bereits laufende Panel nach vorn, statt ein zweites
    (totes) Panel zu oeffnen. Anhand der PID statt des Titels, damit kein fremdes
    Fenster mit aehnlichem Titel getroffen wird."""
    hits = windows_of_pid(pid)
    if not hits:
        return False
    focus_window(hits[0])
    return True
