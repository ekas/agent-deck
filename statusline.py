"""Agent-Deck statusLine: liefert Claude Code eine Statuszeile UND schreibt die
Live-Werte (Modell, Effort, Kontext-%, Nachricht-Tokens, Kosten) fuer das Deck
in state/<slot>.live.json.

Konfiguration in ~/.claude/settings.json:
  "statusLine": { "type": "command",
                  "command": "python \"C:\\...\\agent-deck\\statusline.py\"" }

Claude Code ruft dieses Kommando bei jeder Assistant-Message / /compact /
Permission-Mode-Wechsel auf und uebergibt ein JSON per stdin. Das Skript laeuft
als Kind der Claude-Session -> Slot-Aufloesung ueber hookstate (AGENT_SLOT oder
pidmap ueber die Prozesskette), genau wie der report-Hook. Gibt IMMER eine Zeile
aus (auch ohne Slot), damit die Statuszeile im Terminal nicht leer ist. Darf nie
mit Fehler enden.
"""
import os
import sys
import json
import time

import hookstate   # Slot-Aufloesung + State-Ordner (gemeinsamer Hook-Leaf)
import deck_paths


def _num(*vals):
    for v in vals:
        if isinstance(v, (int, float)):
            return v
    return None


def _msg_tokens(cw):
    """Tokens der aktuellen Nachricht (best effort ueber wechselnde Feldnamen)."""
    cur = cw.get("current_usage") or {}
    i = _num(cur.get("input_tokens"), cur.get("input"))
    o = _num(cur.get("output_tokens"), cur.get("output"))
    if i is not None or o is not None:
        return (i or 0) + (o or 0)
    return _num(cw.get("total_output_tokens"))


def _extract(data):
    model = None
    m = data.get("model")
    if isinstance(m, dict):
        model = m.get("display_name") or m.get("id")
    elif isinstance(m, str):
        model = m
    eff = data.get("effort")
    effort = eff.get("level") if isinstance(eff, dict) else (eff if isinstance(eff, str) else None)
    cw = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
    return {
        "model": model,
        "effort": effort,
        "ctx_pct": _num(cw.get("used_percentage")),
        "msg_tokens": _msg_tokens(cw),
        "cost_usd": _num(cost.get("total_cost_usd")),
        "ts": time.time(),
    }


def _line(rec):
    """Kompakte Statuszeile fuers Terminal."""
    parts = []
    if rec.get("model"):
        parts.append(str(rec["model"]))
    if rec.get("effort"):
        parts.append(f"effort {rec['effort']}")
    if rec.get("ctx_pct") is not None:
        parts.append(f"ctx {round(rec['ctx_pct'])}%")
    if rec.get("cost_usd") is not None:
        parts.append(f"${rec['cost_usd']:.2f}")
    return "  ·  ".join(parts)


def main():
    try:
        # Rohe Bytes selbst als UTF-8 dekodieren (siehe report._read_stdin_json):
        # sys.stdin.read() nimmt auf Windows die ANSI-Codepage und zerstoert Umlaute.
        if sys.stdin is None or sys.stdin.isatty():
            raw = ""
        else:
            raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    rec = _extract(data)

    try:
        base = hookstate.state_dir()
        slot = os.environ.get("AGENT_SLOT") or hookstate.slot_from_procs(base)
        if slot:
            deck_paths.save_json(os.path.join(base, slot + ".live.json"), rec)
    except Exception:
        pass  # Deck-State ist Beiwerk -> niemals die Statuszeile kaputt machen

    sys.stdout.write(_line(rec))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
