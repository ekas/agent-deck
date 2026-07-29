"""Hook-Reporter: von Claude Code Hooks aufgerufen, meldet den Agent-Zustand.

Aufruf (aus settings.json):   python report.py <status>

Der Slot (A1..B4) wird auf zwei Wegen ermittelt:
  1) AGENT_SLOT aus der Umgebung — von der Extension gesetzte Deck-Terminals.
  2) Fallback ueber die Prozess-Kette: Dieser Hook laeuft als Kind des Claude-
     Prozesses. Wir laufen die PPID-Kette hoch und suchen den ersten Vorfahren,
     der in einer von der Extension geschriebenen pidmap-<Fenster>.json steht.
     So melden auch SELBST gestartete Claude-Sessions (ohne AGENT_SLOT) Status.

Import nur der abhaengigkeitsarmen, reinen Leafs hookstate + deck_paths (kein
deck_common, kein tkinter): der Hook laeuft in wechselnden Arbeitsverzeichnissen
und darf NIE mit Fehler enden (sonst blockiert er den Agenten). Die Slot-
Aufloesung + der State-Ordner wohnen jetzt in hookstate/paths.
"""
import json
import os
import sys
import time

from deck.claude.hooks import resolve
from deck.domain import paths

# Claude-Code-permission_mode (aus dem Hook-stdin) -> Namen wie in config.MODE_CYCLE.
_MODE_MAP = {
    "default": "manual", "acceptedits": "accept", "plan": "plan",
    "auto": "auto", "bypasspermissions": "bypass", "dontask": "dontask",
}


def _read_stdin_json():
    """Das an den Hook uebergebene JSON lesen. Robust: kein/kaputtes stdin -> {}.
    Claude Code liefert IMMER UTF-8 -> wir dekodieren die ROHEN Bytes selbst als
    UTF-8. sys.stdin.read() wuerde auf (deutschem) Windows die ANSI-Codepage
    (cp1252) nehmen und aus Umlauten Mojibake machen (oe -> Ã¶, ss -> ÃŸ), das dann
    kaputt in der State-Datei und im Hover-Tooltip landet."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _mode_of(data):
    pm = data.get("permission_mode")
    return _MODE_MAP.get(str(pm).lower(), str(pm).lower()) if pm else None


def _effort_of(data):
    e = data.get("effort")
    if isinstance(e, dict):
        return e.get("level")
    return e if isinstance(e, str) else None


def _prompt_of(data):
    """Text der zuletzt von MIR abgeschickten Frage – nur beim UserPromptSubmit-Event
    im stdin (`prompt`). Whitespace-getrimmt und auf 500 Zeichen gekuerzt: haelt die
    State-Datei klein und den Hover-Tooltip im Deck kurz. Kein/kein-String -> None."""
    p = data.get("prompt")
    if not isinstance(p, str):
        return None
    p = p.strip()
    if not p:
        return None
    return p[:500].rstrip() + "…" if len(p) > 500 else p


def _activity_of(data):
    """Kurzbeschreibung des gerade genutzten Tools (nur bei Pre/PostToolUse gesetzt)."""
    tn = data.get("tool_name")
    if not tn:
        return None
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    detail = (ti.get("command") or ti.get("file_path") or ti.get("path")
              or ti.get("pattern") or ti.get("url") or ti.get("description") or "")
    detail = str(detail).splitlines()[0].strip() if detail else ""
    if len(detail) > 42:
        detail = detail[:42] + "…"
    return f"{tn}: {detail}" if detail else str(tn)


# Notification-Typen, die WIRKLICH eine Entscheidung von mir verlangen -> "Rückfrage".
# Claude Codes Notification-Hook feuert fuer viele Faelle (permission_prompt,
# idle_prompt, auth_success, elicitation_dialog/-complete/-response,
# agent_needs_input, agent_completed) und liefert den Fall im stdin-Feld
# `notification_type`. Nur diese drei sind eine echte Rueckfrage.
_WAIT_NOTIFY = {"permission_prompt", "elicitation_dialog", "agent_needs_input"}


def _is_real_query(data):
    """Ist ein Notification-Event eine echte Rueckfrage (Auswahl/Bestaetigung noetig)?
    Bevorzugt das dokumentierte `notification_type`-Feld; fehlt es (aeltere Claude-
    Code-Version ohne den Typ), faellt es auf die Permission-Meldung zurueck
    ('… Allow …' bzw. '… permission …'). idle_prompt & Co. gelten so NICHT als
    Rueckfrage -> eine fertige/idle Kachel kippt nicht mehr faelschlich auf 'waiting'."""
    nt = data.get("notification_type")
    if isinstance(nt, str) and nt:
        return nt in _WAIT_NOTIFY
    low = str(data.get("message") or "").lower()
    return ("allow" in low) or ("permission" in low)


def main():
    status = sys.argv[1] if len(sys.argv) > 1 else "thinking"
    base = resolve.state_dir()
    slot = os.environ.get("AGENT_SLOT") or resolve.slot_from_procs(base)
    if not slot:
        return  # kein Slot zuordenbar -> nichts melden

    data = _read_stdin_json()

    # Der Notification-Hook meldet stur "waiting", feuert aber fuer MEHRERE Faelle.
    # Ist es KEINE echte Rueckfrage (z.B. idle_prompt: Eingabe ~60s brach), den
    # Zustand unangetastet lassen -> die Kachel behaelt done/idle statt faelschlich
    # auf "Rückfrage" zu kippen.
    if status == "waiting" and not _is_real_query(data):
        return

    # Der SessionStart-Hook (Status "idle") feuert bei JEDEM Session-Beginn: echter
    # Start (startup), aber auch resume/clear/compact. Nur der echte Start soll die
    # Kachel zuruecksetzen -> bei /clear, Compaction oder Resume den bestehenden
    # Zustand (z.B. gruenes "ungelesen") NICHT ueberschreiben. Fehlt `source` (aeltere
    # Claude-Version), als Start behandeln. Diesen frischen "idle"-Report nutzt das
    # Deck zugleich als Bereit-Signal fuer den Auto-Startmodus neuer Agenten.
    if data.get("hook_event_name") == "SessionStart" \
            and data.get("source") not in (None, "", "startup"):
        return

    dst = paths.state_path(slot)
    prev = paths.load_json(dst, {}) or {}

    rec = {"slot": slot, "status": status, "ts": time.time()}
    # Felder, die nur manche Events liefern -> vorhandene Werte erhalten.
    mode = _mode_of(data) or prev.get("mode")
    if mode:
        rec["mode"] = mode
    eff = _effort_of(data) or prev.get("effort")
    if eff:
        rec["effort"] = eff
    # Aktivitaet: bei Tool-Nutzung setzen, bei Stop leeren, sonst beibehalten.
    ev = data.get("hook_event_name")
    act = _activity_of(data)
    if ev == "Stop" or status == "done":
        act = ""
    elif act is None:
        act = prev.get("activity", "")
    rec["activity"] = act
    sid = data.get("session_id") or prev.get("session_id")
    if sid:
        rec["session_id"] = sid
    # Arbeitsverzeichnis des Agenten (= Repo-Root, stabil ueber die Session; ein `cd`
    # im Bash-Tool aendert das cwd DIESES Hook-Prozesses nicht). Nur so kann das Deck
    # beim Schliessen den git worktree eines Ticket-Branches per `git worktree list`
    # finden, falls der Agent die Pfad-Marker-Datei mal nicht geschrieben hat.
    cwd = prev.get("cwd")
    try:
        cwd = os.getcwd() or cwd
    except OSError:
        pass
    if cwd:
        rec["cwd"] = cwd
    # Letzte User-Frage: nur der UserPromptSubmit-Event liefert `prompt` -> sonst den
    # gemerkten Wert behalten, damit der Hover-Tooltip auch bei "denkt"/"fertig" die
    # zuletzt gestellte Frage zeigt.
    prompt = _prompt_of(data) or prev.get("prompt")
    if prompt:
        rec["prompt"] = prompt

    paths.save_json(dst, rec)   # atomar: .tmp + os.replace


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Hooks duerfen niemals crashen
