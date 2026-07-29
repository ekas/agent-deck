"""Golden-Master-Erzeugung: die PYTHON-Fassung als Orakel für den .NET-Port.

Dieses Skript jagt systematisch kombinierte Eingaben durch die Python-Funktionen und
schreibt Eingabe+Ausgabe nach tests/golden/*.json. Die C#-Tests lesen dieselben Dateien
und vergleichen ihre eigene Ausgabe dagegen.

Warum als Datei und nicht als Live-Aufruf: die Golden-Dateien überleben das Löschen der
Python-Fassung, laufen in der CI ohne Python und sind im Diff nachvollziehbar. Dieses
Skript ist das Wegwerf-Werkzeug, die JSON-Dateien sind das Bleibende.

Aufruf:  python tools/gen_golden.py
"""
import itertools
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "tests" / "golden"
OUT.mkdir(parents=True, exist_ok=True)

import status_model as sm            # noqa: E402
import canvas_kit as ck              # noqa: E402
import report as rp                  # noqa: E402
import statusline as sl              # noqa: E402
import edge_dock as ed               # noqa: E402

GLOW = {"idle": 1, "done": 1, "thinking": 1, "running": 1, "waiting": 1, "none": 1}
CYCLE = ["manual", "accept", "plan", "auto"]
STATUS = ["idle", "done", "thinking", "running", "waiting", "none", "lost", "bogus"]


def write(name, cases):
    path = OUT / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    print(f"  {name}.json  {len(cases)} Fälle")


# ── status_model ─────────────────────────────────────────────────────────
def gen_status_model():
    cases = []

    for ts, now, stale in itertools.product([0, 100, 1000], [0, 100, 1000, 1001], [0, 900]):
        cases.append({"fn": "is_fresh", "st": {"ts": ts}, "now": now, "stale": stale,
                      "out": sm.is_fresh({"ts": ts}, now, stale)})
    cases.append({"fn": "is_fresh", "st": None, "now": 0, "stale": 900,
                  "out": sm.is_fresh(None, 0, 900)})

    for status, fresh in itertools.product(STATUS + [None], [True, False]):
        cases.append({"fn": "normalize_status", "status": status, "fresh": fresh,
                      "out": sm.normalize_status(status, fresh, GLOW)})

    for status, fresh, conn in itertools.product(STATUS, [True, False], [True, False]):
        cases.append({"fn": "is_lost", "status": status, "fresh": fresh, "connected": conn,
                      "out": sm.is_lost(status, fresh, conn)})

    # Alle Teilmengen bis Größe 3 - mehr bringt nichts, die Funktion ist mengenbasiert.
    for n in range(0, 4):
        for combo in itertools.combinations(STATUS, n):
            cases.append({"fn": "dominant_status", "keys": list(combo),
                          "out": sm.dominant_status(list(combo))})

    for prev, key in itertools.product(STATUS, STATUS):
        cases.append({"fn": "escalated", "prev": prev, "key": key,
                      "out": sm.escalated(prev, key)})

    efforts = ["", "xhigh", "high", "medium", "low", "ultracode", None]
    for live, rem in itertools.product(efforts, efforts):
        if live is None:
            continue                      # live_eff ist in der Praxis immer ein String
        cases.append({"fn": "resolve_effort", "live": live, "remembered": rem,
                      "out": sm.resolve_effort(live, rem)})

    for rem, target, start in itertools.product(
            [None, 0, 1, 2, 3], CYCLE + ["bogus"], ["manual", "auto", "weird"]):
        out = sm.mode_steps(rem, target, CYCLE, start)
        cases.append({"fn": "mode_steps", "remembered": rem, "target": target, "start": start,
                      "out": list(out) if out else None})

    for prev_ts, mode, ts in itertools.product([0, 5, 9], CYCLE + ["bogus", None], [0, 5, 10]):
        st = {"ts": ts} if mode is None else {"mode": mode, "ts": ts}
        out = sm.adopt_hook_mode(prev_ts, st, CYCLE)
        cases.append({"fn": "adopt_hook_mode", "prev_ts": prev_ts, "mode": mode, "ts": ts,
                      "out": list(out) if out else None})

    write("status_model", cases)


# ── canvas_kit.mix ───────────────────────────────────────────────────────
def gen_color():
    farben = ["#000000", "#ffffff", "#23232b", "#7ecbff", "#ffc48a", "#6ee7a8",
              "#ff6b6b", "#8b8b99", "#010203", "#7f7f7f"]
    cases = []
    for c1, c2 in itertools.product(farben, farben):
        for t in [0.0, 0.06, 0.28, 0.3, 0.5, 0.5019, 0.7, 1.0, -0.5, 1.5]:
            cases.append({"c1": c1, "c2": c2, "t": t, "out": ck.mix(c1, c2, t)})
    write("color_mix", cases)


# ── edge_dock._spring_at ─────────────────────────────────────────────────
def gen_spring():
    import math
    cases = []
    for response in [190.0, 150.0]:
        omega = 2.0 * math.pi / (response / 1000.0)
        for d0 in [-1.0, -0.5, -0.13, 0.0, 0.13, 0.5, 1.0]:
            for v0 in [-8.0, -1.0, 0.0, 1.0, 8.0]:
                for dt in [0.001, 0.008, 0.016, 0.05, 0.5, 30.0]:
                    d, v = ed.EdgeDock._spring_at(d0, v0, omega, dt)
                    cases.append({"response": response, "d0": d0, "v0": v0, "dt": dt,
                                  "d": d, "v": v})
    write("spring", cases)


# ── report.py: die Fallunterscheidungen der Hooks ────────────────────────
def gen_report():
    payloads = [
        {},
        {"permission_mode": "default"},
        {"permission_mode": "acceptEdits"},
        {"permission_mode": "PLAN"},
        {"permission_mode": "bypassPermissions"},
        {"permission_mode": "dontask"},
        {"permission_mode": "unbekannt"},
        {"effort": "high"},
        {"effort": {"level": "xhigh"}},
        {"effort": {"nix": 1}},
        {"prompt": "  Was ist kaputt?  "},
        {"prompt": "   "},
        {"prompt": "x" * 600},
        {"prompt": "ä" * 600},
        {"tool_name": "Bash", "tool_input": {"command": "npm test"}},
        {"tool_name": "Bash", "tool_input": {"command": "erste\nzweite"}},
        {"tool_name": "Bash", "tool_input": {"command": "0123456789" * 6}},
        {"tool_name": "Read", "tool_input": {"file_path": "C:\\x\\y.cs"}},
        {"tool_name": "Grep", "tool_input": {"pattern": "foo.*bar"}},
        {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}},
        {"tool_name": "Task", "tool_input": {"description": "etwas tun"}},
        {"tool_name": "Read"},
        {"notification_type": "permission_prompt"},
        {"notification_type": "idle_prompt"},
        {"notification_type": "elicitation_dialog"},
        {"notification_type": "agent_needs_input"},
        {"notification_type": "agent_completed"},
        {"message": "Claude needs your permission"},
        {"message": "Allow Bash?"},
        {"message": "nichts davon"},
        {"hook_event_name": "SessionStart", "source": "startup"},
        {"hook_event_name": "SessionStart", "source": "resume"},
        {"hook_event_name": "SessionStart"},
        {"hook_event_name": "Stop"},
    ]
    cases = []
    for p in payloads:
        cases.append({
            "payload": p,
            "mode": rp._mode_of(p),
            "effort": rp._effort_of(p),
            "prompt": rp._prompt_of(p),
            "activity": rp._activity_of(p),
            "real_query": rp._is_real_query(p),
        })
    write("report_hook", cases)


# ── statusline.py ────────────────────────────────────────────────────────
def gen_statusline():
    payloads = [
        {},
        {"model": "claude-opus-5"},
        {"model": {"display_name": "Opus 5"}},
        {"model": {"id": "claude-opus-5"}},
        {"model": {"display_name": "Opus 5", "id": "x"}},
        {"effort": "high"},
        {"effort": {"level": "xhigh"}},
        {"context_window": {"used_percentage": 42.7}},
        {"context_window": {"used_percentage": 42.5}},     # Rundung genau auf .5
        {"context_window": {"used_percentage": 43.5}},     # zweite .5-Probe
        {"context_window": {"used_percentage": 0}},
        {"context_window": {"current_usage": {"input_tokens": 1200, "output_tokens": 300}}},
        {"context_window": {"current_usage": {"input": 1200, "output": 300}}},
        {"context_window": {"current_usage": {"input_tokens": 1200}}},
        {"context_window": {"total_output_tokens": 900}},
        {"cost": {"total_cost_usd": 0.1534}},
        {"cost": {"total_cost_usd": 0.125}},               # Rundung genau auf .5
        {"cost": {"total_cost_usd": 0.135}},
        {"cost": {"total_cost_usd": 0}},
        {"cost": {"total_cost_usd": 12.999}},
        {"model": {"display_name": "Opus 5"}, "effort": {"level": "xhigh"},
         "context_window": {"used_percentage": 42.7,
                            "current_usage": {"input_tokens": 1200, "output_tokens": 300}},
         "cost": {"total_cost_usd": 0.1534}},
    ]
    cases = []
    for p in payloads:
        rec = sl._extract(p)
        rec.pop("ts", None)                # Zeitstempel ist nicht vergleichbar
        cases.append({"payload": p, "rec": rec, "line": sl._line(rec)})
    write("statusline", cases)


if __name__ == "__main__":
    print("Golden-Master aus der Python-Fassung:")
    gen_status_model()
    gen_color()
    gen_spring()
    gen_report()
    gen_statusline()
    print(f"-> {OUT.relative_to(ROOT)}")
