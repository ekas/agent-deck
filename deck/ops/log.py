"""Blackbox fuers Panel: die eine Datei, in der ein Absturz eine Spur hinterlaesst.

Das Deck laeuft im Alltag unter *pythonw* (start.bat) – also OHNE Konsole. Damit
gehen stdout/stderr ins Nichts: sys.stderr ist dort sogar None. Ein stiller Tod
(unbehandelte Exception, verlassener mainloop) war deshalb komplett unsichtbar,
und ein harter Tcl-Panic (Tcl ruft abort(); der Windows-Event-Log meldet dann nur
"pythonw.exe / tcl86t.dll / 0x80000003" ohne eine Zeile Python) liess sich nicht
zuordnen. Genau diese drei Loecher schliesst dieses Modul:

  * faulthandler  -> schreibt bei SIGABRT/SIGSEGV den C-nahen Stack ALLER Threads.
                     Das ist der Beweis fuer den Tcl-Panic-Fall: man sieht, welcher
                     Thread Tcl angefasst hat.
  * excepthook    -> unbehandelte Exceptions, im Main-Thread UND in Daemon-Threads.
  * atexit-Marke  -> "normales Ende". Fehlt sie am Log-Ende (und es steht kein
                     faulthandler-Dump da), wurde der Prozess von AUSSEN beendet
                     (kill/Abmeldung) – ein wichtiger Unterschied beim Suchen.

Wichtig fuer den Konsolen-Start (start_debug.bat): ein ECHTES stderr wird nicht
umgebogen, dort will man die Fehler ja sehen. Umgelenkt wird nur der pythonw-Fall
(stderr == None). Das Log bekommt in beiden Faellen alles Wesentliche.

Reine stdlib, importiert nur deck_paths -> von ueberall gefahrlos importierbar.
Best effort: ein Fehler in der Diagnose darf das Panel NIE mitnehmen.
"""
import atexit
import faulthandler
import os
import sys
import threading
import time
import traceback

from deck.domain import paths as dp

# Neben panel.lock (…/claude-agent-deck/panel.log).
LOG_PATH = os.path.join(os.path.dirname(dp.STATE_DIR), "panel.log")
# Darueber wird beim Start EINMAL rotiert (panel.log.1). Klein halten: die Datei
# soll man im Zweifel ganz lesen koennen.
MAX_BYTES = 512 * 1024

# Die offene Log-Datei MUSS leben, solange der Prozess laeuft: faulthandler haelt
# nur den Dateideskriptor: geht das Objekt in den GC, schreibt es ins Leere.
_fh = None
_installed = False


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _rotate():
    """Beim Start einmal rotieren, wenn das Log zu gross geworden ist."""
    try:
        if os.path.getsize(LOG_PATH) < MAX_BYTES:
            return
    except OSError:
        return
    try:
        os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def install(marks=True):
    """Diagnose einschalten. Idempotent; Fehler werden geschluckt.

    Bewusst SEHR früh aufrufen (vor dem Tk-Aufbau), damit auch ein Fehlstart im
    Log landet.

    marks=False fuer Programme, die NICHT das Panel sind (watchdog.py): sie
    bekommen Logdatei und Fehler-Hooks, setzen aber KEINE Start-/Ende-Marke.
    Das ist wichtig, nicht kosmetisch – watchdog.last_end() liest genau diese
    Marken, um den letzten Panel-Lauf zu beurteilen. Schreibt der Waechter selbst
    eine, beurteilt er seinen eigenen Lauf: er saehe immer "mitten abgebrochen"
    und wuerde ein bewusst geschlossenes Deck wieder hochholen."""
    global _fh, _installed
    if _installed:
        return
    _installed = True
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _rotate()
        # buffering=1 = zeilengepuffert -> im Log steht immer der aktuelle Stand,
        # auch wenn der Prozess gleich hart stirbt.
        # Bleibt ABSICHTLICH offen, solange das Panel lebt: faulthandler schreibt
        # seinen Stack im Absturzfall in genau dieses Handle. Ein Context-Manager
        # waere hier falsch - beim Verlassen des Blocks gaebe es kein Log mehr.
        _fh = open(LOG_PATH, "a", encoding="utf-8",  # noqa: SIM115
                   errors="replace", buffering=1)
    except OSError:
        _fh = None
        return

    if marks:
        note(f"--- Panel-Start (PID {os.getpid()}, Python {sys.version.split()[0]}) ---")

    # Harte Abstuerze: Tcl_Panic -> abort() -> SIGABRT landet hier mit Stack.
    try:
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass

    # Ohne Konsole (pythonw) sind stdout/stderr None -> ins Log biegen. Damit
    # landen auch die print(..., file=sys.stderr)-Diagnosen (z.B. edge_dock) hier.
    try:
        if sys.stderr is None:
            sys.stderr = _fh
        if sys.stdout is None:
            sys.stdout = _fh
    except Exception:
        pass

    def _hook(exc_type, exc, tb):
        note("UNBEHANDELTE EXCEPTION (Main-Thread):")
        _write("".join(traceback.format_exception(exc_type, exc, tb)))

    def _thread_hook(args):
        note(f"UNBEHANDELTE EXCEPTION im Thread '{getattr(args.thread, 'name', '?')}':")
        _write("".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))

    try:
        sys.excepthook = _hook
        threading.excepthook = _thread_hook
    except Exception:
        pass

    # Diese Marke unterscheidet "sauber beendet" von "von aussen abgeschossen".
    if marks:
        try:
            atexit.register(lambda: note("--- Panel-Ende (normaler Exit) ---"))
        except Exception:
            pass


def _write(text):
    if _fh is None:
        return
    try:
        _fh.write(text if text.endswith("\n") else text + "\n")
    except Exception:
        pass


def note(msg):
    """Eine Zeile ins Log (mit Zeitstempel). Immer erlaubt, auch ohne install()."""
    _write(f"[{_stamp()}] {msg}")


def exc(where):
    """Den gerade behandelten Fehler protokollieren – fuer except-Zweige, die
    absichtlich weiterlaufen (das Deck soll an einer Kleinigkeit nicht sterben,
    die Ursache aber auch nicht verschweigen)."""
    note(f"Fehler in {where}:")
    _write(traceback.format_exc())


def hook_tk(root):
    """Tk-Callback-Fehler ins Log holen.

    Tkinter faengt Fehler in Callbacks (Timer, Klicks, Bindings) selbst ab und
    schreibt sie nach stderr – unter pythonw also ins Leere. Das Panel lief nach
    so einem Fehler scheinbar normal weiter, ein Timer war aber tot. Ab jetzt
    steht jeder dieser Fehler im Log."""
    try:
        root.report_callback_exception = lambda t, v, tb: (
            note("Fehler in Tk-Callback:"),
            _write("".join(traceback.format_exception(t, v, tb))))
    except Exception:
        pass
