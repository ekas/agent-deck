"""Unsere Eintraege in Claudes settings.json: eintragen, pruefen, entfernen.

Warum das Code ist und keine Anleitung: Schritt 2 der Einrichtung war Handarbeit -
sechs JSON-Eintraege, in denen der absolute Pfad zu report.py steht. Genau dort sitzen
die drei Fallen, die das Projekt schon Stunden gekostet haben: ein fehlendes
`|| exit 0` blockiert den Agenten (Exit != 0 gilt bei UserPromptSubmit/PreToolUse als
Veto), ein `cmd /c` davor laesst die Kacheln STILL grau, und ein Pfad aus einer
frueheren Installation zeigt ins Leere. Wer einen absoluten Pfad von Hand in JSON
tippt, trifft eine davon - und keine faellt am Exit-Code auf.

CHIRURGISCH heisst: wir schreiben die Datei nicht, wir ergaenzen sie. Fremde Hooks
anderer Werkzeuge bleiben stehen; unsere werden am DATEINAMEN im Kommando
wiedererkannt und ersetzt. Darum ist ein zweiter Lauf ein Nulldurchgang, und ein
verschobenes Repo wird repariert statt verdoppelt - zwei Hook-Saetze wuerden in zwei
verschiedene state-Ordner melden, und das Deck haengt dann am falschen.

Aufruf (aus install.ps1, oder von Hand im Repo-Wurzelverzeichnis):
  python -m deck.claude.hook_setup            eintragen / auf Stand bringen
  python -m deck.claude.hook_setup --check    nur pruefen, Exit 1 bei einem Befund
  python -m deck.claude.hook_setup --remove   unsere Eintraege wieder herausnehmen
  python -m deck.claude.hook_setup --force    auch eine FREMDE statusLine ersetzen
"""
import copy
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from typing import Any

from deck.claude import settings as cset
from deck.domain import paths

# (Hook-Event, Status-Argument fuer report.py, Matcher). Die sechs Meldungen, aus denen
# das Deck den Zustand einer Kachel baut - Reihenfolge wie in docs/SETUP.md.
# Bewusst KEIN idle_prompt: "wartet" = Notification, "fertig" = Stop.
HOOKS: tuple[tuple[str, str, str | None], ...] = (
    ("SessionStart",     "idle",     None),
    ("UserPromptSubmit", "thinking", None),
    ("PreToolUse",       "running",  "*"),
    ("PostToolUse",      "thinking", "*"),
    ("Notification",     "waiting",  None),
    ("Stop",             "done",     None),
)

# Unsere Vertragsdateien im Wurzelverzeichnis. Ihre Namen sind der Marker, an dem wir
# eigene Eintraege wiedererkennen (siehe is_ours).
ENTRY_POINTS = ("report.py", "statusline.py")

# Die aeussere Schale. Sie ist der Unterschied zwischen "der Hook faengt seinen Fehler"
# und "der Hook startet gar nicht": fehlt die Datei oder ist `python` nicht auf dem PATH,
# kommt das Fangnetz IN report.py nicht mehr zum Zug - dann urteilt der Prozessstarter,
# und ohne diesen Anhang gilt sein Exit != 0 als Veto gegen Prompt bzw. Tool-Aufruf.
TAIL = "|| exit 0"

# Ein Kommando gehoert uns, wenn einer der Einsprungpunkte als eigenes Wegstueck darin
# steht - mit Grenze davor und dahinter, damit ein fremdes 'my_report.py' nicht traegt.
_OURS = re.compile(
    r'(?:^|[/"\s])(' + "|".join(re.escape(e) for e in ENTRY_POINTS) + r')(?:$|["\s])'
)


def hook_command(repo_root: str, status: str) -> str:
    """Das Kommando fuer einen Status-Hook - mit absolutem Pfad und aeusserer Schale."""
    return f'python "{os.path.join(repo_root, "report.py")}" {status} {TAIL}'


def statusline_command(repo_root: str) -> str:
    """Das statusLine-Kommando. OHNE `|| exit 0`: die Statuszeile ist kein Veto-Hook,
    ihre AUSGABE ist der Zweck - Claude Code zeigt bei einem Fehler einfach keine Zeile,
    und ein angehaengtes `exit 0` wuerde daran nichts verbessern."""
    return f'python "{os.path.join(repo_root, "statusline.py")}"'


def is_ours(command: str) -> bool:
    """Zeigt ein Kommando auf einen unserer Einsprungpunkte?

    Erkannt wird am DATEINAMEN, nicht am ganzen Pfad - so findet ein Lauf auch die
    Eintraege eines VERSCHOBENEN Repos und ersetzt sie, statt einen zweiten Satz
    daneben zu legen.
    """
    return bool(_OURS.search(command.replace("\\", "/").lower()))


def command_path(command: str) -> str | None:
    """Den Skriptpfad aus einem Hook-Kommando ziehen (gequotet oder nicht)."""
    m = re.search(r'"([^"]+\.py)"', command) or re.search(r"(\S+\.py)", command)
    return m.group(1) if m else None


def _strip_ours(groups: list[Any]) -> tuple[list[Any], list[str]]:
    """Unsere Eintraege aus den Gruppen EINES Events entfernen.

    Gibt (verbleibende Gruppen, entfernte Kommandos) zurueck. Eine Gruppe, die dadurch
    leer wird, faellt mit weg; eine, die schon vorher leer war, bleibt stehen - die
    gehoert jemand anderem, und fremde Struktur raeumen wir nicht auf.
    """
    rest: list[Any] = []
    removed: list[str] = []
    for g in groups:
        inner = g.get("hooks") if isinstance(g, dict) else None
        if not isinstance(inner, list):
            rest.append(g)                      # keine Hook-Gruppe -> nicht anfassen
            continue
        keep = []
        for h in inner:
            cmd = h.get("command") if isinstance(h, dict) else None
            if isinstance(cmd, str) and is_ours(cmd):
                removed.append(cmd)
            else:
                keep.append(h)
        if len(keep) == len(inner):
            rest.append(g)                      # nichts von uns drin
        elif keep:
            ng = dict(g)
            ng["hooks"] = keep
            rest.append(ng)                     # fremde Kommandos in derselben Gruppe
    return rest, removed


def merge(settings: dict[str, Any], repo_root: str, *,
          force_statusline: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Hooks + statusLine in ein GELESENES settings-Dict mergen. Rein, kein IO.

    Rueckgabe: (neues Dict, Klartext-Meldungen fuer den Nutzer). Ob geschrieben werden
    muss, entscheidet der Aufrufer am Vergleich `neu != alt` - nicht an der Meldungs-
    liste, denn die enthaelt auch Hinweise zu Dingen, die wir bewusst NICHT aendern.
    """
    out = copy.deepcopy(settings)
    notes: list[str] = []

    raw = out.get("hooks")
    hooks: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for event, status, matcher in HOOKS:
        raw_groups = hooks.get(event)
        before = list(raw_groups) if isinstance(raw_groups, list) else []
        rest, removed = _strip_ours(before)
        entry: dict[str, Any] = {}
        if matcher:
            entry["matcher"] = matcher
        entry["hooks"] = [{"type": "command", "command": hook_command(repo_root, status)}]
        after = rest + [entry]
        if after != before:
            hooks[event] = after
            notes.append(f"{event} -> report.py {status}"
                         + (f" (ersetzt: {len(removed)} alter Eintrag)" if removed else ""))
    if hooks:
        out["hooks"] = hooks

    want = statusline_command(repo_root)
    sl = out.get("statusLine")
    cur = sl.get("command") if isinstance(sl, dict) else None
    if isinstance(cur, str) and cur.strip() and not is_ours(cur) and not force_statusline:
        # Eine fremde Statuszeile platt zu machen waere eine sichtbare Verschlechterung
        # fuer den Nutzer. Also stehenlassen und sagen, was dadurch fehlt.
        notes.append("statusLine gehoert einem anderen Werkzeug und bleibt stehen — "
                     "Modell, Effort und Kontext-% bleiben auf den Kacheln leer "
                     "(mit --force ersetzen)")
    elif cur != want:
        out["statusLine"] = {"type": "command", "command": want}
        notes.append("statusLine -> statusline.py")
    return out, notes


def remove(settings: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Unsere Eintraege wieder herausnehmen; fremde bleiben unangetastet."""
    out = copy.deepcopy(settings)
    notes: list[str] = []
    raw = out.get("hooks")
    hooks: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        rest, removed = _strip_ours(groups)
        if not removed:
            continue
        notes.append(f"{event}: {len(removed)} Eintrag entfernt")
        if rest:
            hooks[event] = rest
        else:
            del hooks[event]        # das Event kannte nur uns
    if hooks:
        out["hooks"] = hooks
    elif "hooks" in out:
        del out["hooks"]
    sl = out.get("statusLine")
    cur = sl.get("command") if isinstance(sl, dict) else None
    if isinstance(cur, str) and is_ours(cur):
        del out["statusLine"]
        notes.append("statusLine entfernt")
    return out, notes


def _our_commands(settings: dict[str, Any], event: str) -> list[str]:
    """Unsere Kommandos, die unter einem Event stehen (meist genau eines)."""
    raw = settings.get("hooks")
    groups = raw.get(event) if isinstance(raw, dict) else None
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        inner = g.get("hooks") if isinstance(g, dict) else None
        for h in inner if isinstance(inner, list) else []:
            cmd = h.get("command") if isinstance(h, dict) else None
            if isinstance(cmd, str) and is_ours(cmd):
                out.append(cmd)
    return out


def audit(settings: dict[str, Any], repo_root: str, *,
          exists: Callable[[str], bool] = os.path.isfile) -> list[tuple[str, str]]:
    """Urteile ueber die Eintraege: Liste von (grad, text), grad in {ok, warn, fail}.

    Rein bis auf die Dateiexistenz, und die kommt als Funktion herein - so ist jede
    Regel ohne Dateisystem pruefbar. Geprueft wird genau das, was am Exit-Code NICHT
    auffaellt: die drei dokumentierten Fallen und ein Pfad in ein anderes Repo.
    """
    out: list[tuple[str, str]] = []
    want_root = os.path.normcase(os.path.abspath(repo_root))
    for event, status, _matcher in HOOKS:
        cmds = _our_commands(settings, event)
        if not cmds:
            out.append(("fail", f"{event}: kein Hook eingetragen — "
                                f"Kacheln melden kein '{status}'"))
            continue
        if len(cmds) > 1:
            out.append(("warn", f"{event}: {len(cmds)} Eintraege zeigen auf report.py — "
                                "zwei Melder schreiben gegeneinander"))
        cmd = cmds[0]
        if re.search(r"\bcmd\b\s*/c", cmd, re.I):
            out.append(("fail", f"{event}: 'cmd /c' im Kommando — die POSIX-Shell macht "
                                "aus /c den Pfad C:\\, python wird nie aufgerufen "
                                "(Exit 0, aber kein Status)"))
        if not cmd.rstrip().endswith(TAIL):
            out.append(("fail", f"{event}: '{TAIL}' fehlt — ein Hook, der nicht startet, "
                                "blockiert Prompt bzw. Tool-Aufruf"))
        if f" {status} " not in f" {cmd} ":
            out.append(("warn", f"{event}: meldet nicht '{status}'"))
        p = command_path(cmd)
        if not p:
            out.append(("fail", f"{event}: kein .py-Pfad im Kommando erkennbar"))
        elif not exists(p):
            out.append(("fail", f"{event}: {p} gibt es nicht"))
        elif os.path.normcase(os.path.dirname(os.path.abspath(p))) != want_root:
            out.append(("warn", f"{event}: zeigt auf ein anderes Repo ({p})"))

    sl = settings.get("statusLine")
    cur = sl.get("command") if isinstance(sl, dict) else None
    if not isinstance(cur, str) or not cur.strip():
        out.append(("warn", "statusLine fehlt — Modell, Effort und Kontext-% bleiben "
                            "auf den Kacheln leer"))
    elif not is_ours(cur):
        out.append(("warn", "statusLine gehoert einem anderen Werkzeug — Modell, Effort "
                            "und Kontext-% bleiben leer"))
    else:
        p = command_path(cur)
        if p and not exists(p):
            out.append(("fail", f"statusLine: {p} gibt es nicht"))
    if not out:
        out.append(("ok", f"{len(HOOKS)} Hooks und die statusLine zeigen auf {repo_root}"))
    return out


def _backup(path: str) -> str | None:
    """Kopie neben die Datei legen, bevor wir sie anfassen. Nur bei echter Aenderung
    aufgerufen - sonst sammelt jeder Lauf eine weitere Sicherung an."""
    if not os.path.isfile(path):
        return None
    dst = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    root = paths.REPO_ROOT
    path = cset.SETTINGS_PATH
    before = cset.load(path)

    if "--check" in args:
        findings = audit(before, root)
        for grad, text in findings:
            print(f"  {'[ok]  ' if grad == 'ok' else '[' + grad + ']'} {text}")
        return 1 if any(g == "fail" for g, _ in findings) else 0

    if "--remove" in args:
        after, notes = remove(before)
    else:
        after, notes = merge(before, root, force_statusline="--force" in args)

    if after == before:
        print(f"  Nichts zu tun — {path} ist schon auf Stand.")
    else:
        bak = _backup(path)
        cset.save(after, path)
        print(f"  {path}")
        if bak:
            print(f"  Sicherung: {bak}")
    for n in notes:
        print(f"    · {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
