"""Ein Ort fuer alle Pfade: Repo-Wurzel, State-Ordner, atomares JSON.

Frueher lag die STATE_DIR-Formel doppelt (deck_common.py UND report.py) und das
atomare .tmp+os.replace-Schreiben gleich drei Mal herum (deck_common, report,
statusline). Dieses Modul ist die eine Quelle. Reine stdlib, KEINE projekt-
eigenen Importe -> von ueberall gefahrlos importierbar, auch aus den Claude-Code-
Hooks in wechselnden Arbeitsverzeichnissen (das Skriptverzeichnis steht immer auf
sys.path, der Import haengt also nicht am cwd).
"""
import json
import os
from typing import Any

# Wurzel des Repos: drei Ebenen ueber dieser Datei (deck/domain/paths.py).
#
# Wer Dateien NEBEN dem Code ablegt oder liest - bindings.json und die uebrigen
# Laufzeit-JSONs, assets/robot.ico, agent-deck-glow.css, der Panel-Einstieg fuer
# den Waechter - fragt hier und rechnet NICHT selbst mit __file__. Sonst zeigt
# jede Modulverschiebung ins Leere, und das faellt nicht auf: die Laufzeitdateien
# entstehen einfach neu am falschen Ort, waehrend die alten mit allen Fenster-
# Zuordnungen unsichtbar liegenbleiben.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Slot-Zustaende liegen als kleine JSON-Dateien in diesem Ordner (siehe report.py).
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "claude-agent-deck", "state",
)


def state_path(slot: str) -> str:
    return os.path.join(STATE_DIR, slot + ".json")


def found_ticket_path(slot: str) -> str:
    """Marker-Datei, in die ein Agent bei der 'Im Chat suchen'-Zuweisung die selbst
    gefundene Ticket-ID schreibt (Klartext, eine Zeile). Das Deck kennt die ID dann
    nicht vorher und liest sie von hier fuer die Karten-Anzeige."""
    return os.path.join(STATE_DIR, slot + ".ticket")


def worktree_marker_path(slot: str) -> str:
    """Marker-Datei, in die ein Agent den absoluten Pfad des fuer sein Ticket
    angelegten git worktree schreibt (Klartext, eine Zeile). Beim Schliessen des
    Agenten raeumt das Deck genau diesen worktree wieder auf (worktree_cleanup)."""
    return os.path.join(STATE_DIR, slot + ".worktree")


def load_json(path: str, default: Any = None) -> Any:
    """JSON aus path lesen; fehlt/halb geschrieben/kaputt -> default (nie Exception)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    """Atomar schreiben: erst <path>.tmp, dann os.replace -> nie halbe Dateien.
    Legt den Zielordner bei Bedarf an."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
