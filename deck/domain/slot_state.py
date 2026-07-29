"""Gemeinsame Basis fuers Deck: Status-Dateien lesen/schreiben.

Jeder Agent meldet seinen Zustand ueber report.py in eine kleine JSON-Datei
(state/<slot>.json). Die App liest diesen Ordner regelmaessig aus. Datei-basiert,
robust, keine Sockets - dieselbe Idee wie im Claude-Usage-Monitor. Der State-
Ordner und das atomare Schreiben kommen aus deck_paths (eine Quelle).
"""
import os
import time

from deck.domain.paths import STATE_DIR, load_json, save_json, state_path


def write_state(slot, status):
    """Schreibt den Zustand atomar (deck_paths.save_json: erst .tmp, dann os.replace).
    Vorhandene Zusatzfelder (mode/effort/prompt/session_id/…) bleiben erhalten -> das
    'als gelesen markieren' (done->idle beim Anklicken) verliert die letzte Frage
    NICHT, sondern flippt nur Status + ts."""
    rec = load_json(state_path(slot), {}) or {}
    rec.update({"slot": slot, "status": status, "ts": time.time()})
    save_json(state_path(slot), rec)


def clear_state(slot):
    """Zustands-Datei eines Slots entfernen (beim Schliessen eines Agenten). Verhindert,
    dass ein spaeter WIEDERVERWENDETER Slot-Name — die Extension vergibt <Fenster><max+1>,
    recycelt also den Namen des geschlossenen hoechsten Agenten — den alten Status/Modus
    aus der liegengebliebenen Datei erbt (report.py traegt vorhandene Felder sonst fort).
    Fehlt die Datei schon -> still ok."""
    try:
        os.remove(state_path(slot))
    except OSError:
        pass


def read_all():
    """Liefert {slot: {status, ts, mode, effort, activity}} aus state/<slot>.json.
    Ignoriert .live.json (statusLine) und pidmap-*.json."""
    out = {}
    try:
        for fn in os.listdir(STATE_DIR):
            if not fn.endswith(".json") or fn.endswith(".live.json") or fn.startswith("pidmap-"):
                continue
            d = load_json(os.path.join(STATE_DIR, fn))   # kaputt/halb -> None
            if isinstance(d, dict):
                out[d.get("slot", fn[:-5])] = d
    except FileNotFoundError:
        pass  # Ordner existiert noch nicht -> noch keine Meldungen
    return out


def read_live():
    """Live-Werte aus state/<slot>.live.json (von statusline.py): {slot: {...}}."""
    out = {}
    suffix = ".live.json"
    try:
        for fn in os.listdir(STATE_DIR):
            if not fn.endswith(suffix):
                continue
            d = load_json(os.path.join(STATE_DIR, fn))
            if isinstance(d, dict):
                out[fn[:-len(suffix)]] = d
    except FileNotFoundError:
        pass
    return out


def read_found_tickets():
    """Vom Agenten selbst gemeldete Ticket-IDs aus state/<slot>.ticket (Klartext, eine
    Zeile) – genutzt bei der 'Im Chat suchen'-Zuweisung, bei der das Deck die ID nicht
    vorher kennt. {slot: id}. Kaputte/leere Dateien werden ignoriert; der Wert wird auf
    60 Zeichen begrenzt (die Karte kuerzt zusaetzlich)."""
    out = {}
    suffix = ".ticket"
    try:
        for fn in os.listdir(STATE_DIR):
            if not fn.endswith(suffix):
                continue
            try:
                with open(os.path.join(STATE_DIR, fn), encoding="utf-8") as f:
                    val = f.read().strip()
            except OSError:
                continue
            if val:
                out[fn[:-len(suffix)]] = val.splitlines()[0].strip()[:60]
    except FileNotFoundError:
        pass
    return out


def read_found_worktrees():
    """Vom Agenten gemeldete worktree-Pfade aus state/<slot>.worktree (Klartext, eine
    Zeile mit dem absoluten Pfad). Beim Schliessen des Agenten raeumt das Deck genau
    diesen worktree auf. {slot: pfad}. Kaputte/leere Dateien werden ignoriert; der
    Pfad wird auf 4096 Zeichen begrenzt (Pfade duerfen Leerzeichen enthalten)."""
    out = {}
    suffix = ".worktree"
    try:
        for fn in os.listdir(STATE_DIR):
            if not fn.endswith(suffix):
                continue
            try:
                with open(os.path.join(STATE_DIR, fn), encoding="utf-8") as f:
                    val = f.read().strip()
            except OSError:
                continue
            if val:
                out[fn[:-len(suffix)]] = val.splitlines()[0].strip()[:4096]
    except FileNotFoundError:
        pass
    return out
