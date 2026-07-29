"""Lesen/Schreiben von Claude Codes globaler ~/.claude/settings.json.

Das Einstellungs-Fenster des Decks steuert damit die vier Defaults fuer NEU
gestartete Claude-Agenten:
  • model            -> Modell (Opus / Fable; immer als Alias = neueste Version)
  • permissions.defaultMode -> Start-Permission-Modus (auto/default/acceptEdits/plan)
  • effortLevel      -> Reasoning-Effort (low/medium/high/xhigh)
  • language         -> Antwortsprache (english/german)

Wichtig: Wir MERGEN immer in die bestehende Datei (Hooks, statusLine, Plugins,
permissions.allow etc. bleiben unangetastet) und schreiben atomar + eingerueckt
(die Datei wird auch von Hand editiert). Reine Stdlib, kein Deck-Import -> von
ueberall gefahrlos nutzbar (auch aus den Tests).
"""
import json
import os

# %USERPROFILE%\.claude\settings.json — dieselbe globale Datei, in der die
# report.py-Hooks und die statusLine stehen (global, weil die VS-Code-Fenster
# verschiedene Ordner haben; siehe SETUP.md Schritt 2).
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")

# (Anzeige-Label, in settings.json geschriebener Wert). Reihenfolge = Dropdown-
# Reihenfolge; das ERSTE Paar ist zugleich der Fallback fuer unbekannte/fehlende
# Werte.
#
# BEWUSST NUR ALIASSE, KEINE VERSIONEN – weder im Wert noch im Label:
# `claude --model` loest "opus"/"fable"/"sonnet"/"haiku" laut `claude --help`
# immer auf das NEUESTE Modell der jeweiligen Reihe auf (verifiziert 2026-07-27:
# `--model opus[1m]` -> claude-opus-5[1m], `--model opus` -> claude-opus-5).
# Eine feste ID (z.B. "claude-opus-4-8") oder ein Versions-Label wuerde beim
# naechsten Modell-Release veralten und muesste nachgepflegt werden.
# "opus[1m]" = neuestes Opus mit 1M-Kontext (derselbe Wert, den auch der
# Opus-Button des Decks per /model schickt).
MODEL_CHOICES = [("Opus (1M)", "opus[1m]"), ("Fable", "fable"),
                 ("Sonnet", "sonnet"), ("Haiku", "haiku")]
MODE_CHOICES = [("Auto", "auto"), ("Standard", "default"),
                ("Accept Edits", "acceptEdits"), ("Plan", "plan")]
LANG_CHOICES = [("Englisch", "english"), ("Deutsch", "german")]

# Effort ist ein Sonderfall: "ultracode" ist KEIN effortLevel-Wert, sondern der
# separate Boolean `ultracode` (= xhigh + Dynamic Workflows). Die Auswahl bildet
# darum auf ZWEI settings.json-Felder ab: (effortLevel, ultracode). Reihenfolge =
# Dropdown-Reihenfolge. Gueltige effortLevel (verifiziert an Claude Code 2.1.218 via
# `claude --effort <x>`): low, medium, high, xhigh, max – "ultracode" ist NICHT dabei.
EFFORT_CHOICES = ["Ultracode", "max", "xhigh", "high", "medium", "low"]
_EFFORT_MAP = {
    "Ultracode": ("xhigh", True),
    "max": ("max", False),
    "xhigh": ("xhigh", False),
    "high": ("high", False),
    "medium": ("medium", False),
    "low": ("low", False),
}


def effort_spec(label):
    """Anzeige-Label -> (effortLevel, ultracode). Fallback: xhigh ohne ultracode."""
    return _EFFORT_MAP.get(label, ("xhigh", False))


def effort_label(effort, ultracode):
    """(effortLevel, ultracode) aus der Datei -> Anzeige-Label. ultracode gewinnt;
    sonst per effortLevel; nichts passt -> 'xhigh'."""
    if ultracode:
        return "Ultracode"
    for lbl in EFFORT_CHOICES:
        lvl, uc = _EFFORT_MAP[lbl]
        if not uc and lvl == effort:
            return lbl
    return "xhigh"


def load(path=None):
    """settings.json als Dict lesen; fehlt/kaputt/kein Objekt -> {} (nie Exception)."""
    path = path or SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data, path=None):
    """Atomar + eingerueckt schreiben: erst <path>.tmp, dann os.replace -> nie halbe
    Dateien. ensure_ascii=False haelt Umlaute/ß in den Hook-Pfaden lesbar (wie bisher).
    Legt den Zielordner bei Bedarf an."""
    path = path or SETTINGS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_values(path=None):
    """Die vier gesteuerten Werte aus der Datei ziehen (roh; nicht gesetzt -> None)."""
    d = load(path)
    perms = d.get("permissions") if isinstance(d.get("permissions"), dict) else {}
    return {
        "model": d.get("model"),
        "mode": perms.get("defaultMode"),
        "effort": d.get("effortLevel"),
        "ultracode": bool(d.get("ultracode")),
        "language": d.get("language"),
    }


def write_values(model=None, mode=None, effort=None, ultracode=None,
                 language=None, path=None):
    """Nur die gesteuerten Keys in die BESTEHENDE Datei mergen; alles andere bleibt.
    Ein Argument = None -> diesen Key nicht anfassen. Gibt das geschriebene Dict zurueck."""
    d = load(path)
    if model is not None:
        d["model"] = model
    if effort is not None:
        d["effortLevel"] = effort
    if ultracode is not None:
        d["ultracode"] = bool(ultracode)
    if language is not None:
        d["language"] = language
    if mode is not None:
        perms = d.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
        perms["defaultMode"] = mode
        d["permissions"] = perms
    save(d, path)
    return d


def label_to_value(choices, label):
    """Anzeige-Label -> settings.json-Wert (Fallback: erster Eintrag)."""
    for lbl, val in choices:
        if lbl == label:
            return val
    return choices[0][1]


def value_to_label(choices, value, *, contains=False):
    """settings.json-Wert -> Anzeige-Label. Erst exakt, dann (bei contains=True) per
    Teilstring auf dem Basis-Namen vor '[' (z.B. 'opus' aus 'opus[1m]' oder aus einer
    vollen ID wie 'claude-opus-5'). Nichts passt / value=None -> erster Eintrag."""
    if value is not None:
        s = str(value).strip()
        for lbl, val in choices:
            if val == s:
                return lbl
        if contains and s:
            sl = s.lower()
            for lbl, val in choices:
                base = val.split("[")[0].lower()
                if base and (base in sl or sl in base):
                    return lbl
    return choices[0][0]
