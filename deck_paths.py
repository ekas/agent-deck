"""Ein Ort fuer den State-Ordner + atomares JSON-Lesen/Schreiben.

Frueher lag die STATE_DIR-Formel doppelt (deck_common.py UND report.py) und das
atomare .tmp+os.replace-Schreiben gleich drei Mal herum (deck_common, report,
statusline). Dieses Modul ist die eine Quelle. Reine stdlib, KEINE projekt-
eigenen Importe -> von ueberall gefahrlos importierbar, auch aus den Claude-Code-
Hooks in wechselnden Arbeitsverzeichnissen (das Skriptverzeichnis steht immer auf
sys.path, der Import haengt also nicht am cwd).
"""
import os
import json

# Slot-Zustaende liegen als kleine JSON-Dateien in diesem Ordner (siehe report.py).
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "claude-agent-deck", "state",
)


def state_path(slot):
    return os.path.join(STATE_DIR, slot + ".json")


def found_ticket_path(slot):
    """Marker-Datei, in die ein Agent bei der 'Im Chat suchen'-Zuweisung die selbst
    gefundene Ticket-ID schreibt (Klartext, eine Zeile). Das Deck kennt die ID dann
    nicht vorher und liest sie von hier fuer die Karten-Anzeige."""
    return os.path.join(STATE_DIR, slot + ".ticket")


def worktree_marker_path(slot):
    """Marker-Datei, in die ein Agent den absoluten Pfad des fuer sein Ticket
    angelegten git worktree schreibt (Klartext, eine Zeile). Beim Schliessen des
    Agenten raeumt das Deck genau diesen worktree wieder auf (worktree_cleanup)."""
    return os.path.join(STATE_DIR, slot + ".worktree")


def load_json(path, default=None):
    """JSON aus path lesen; fehlt/halb geschrieben/kaputt -> default (nie Exception)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    """Atomar schreiben: erst <path>.tmp, dann os.replace -> nie halbe Dateien.
    Legt den Zielordner bei Bedarf an."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
