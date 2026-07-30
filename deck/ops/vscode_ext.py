"""Unser Eintrag in VS Codes Extension-Registratur: pruefen, reparieren, entfernen.

WARUM das noetig ist: Der Ordner `~/.vscode/extensions/agent-deck-bridge` zu haben
GENUEGT NICHT. VS Code fuehrt daneben eine Registratur - `extensions.json` im selben
Ordner -, und geladen wird, was DORT steht. Beides kann auseinanderlaufen: wird der
Ordner umbenannt oder geloescht, bleibt der Eintrag stehen und zeigt ins Leere. VS Code
meldet dann beim Start einmal

    Unable to read file '...\\agent-deck-bridge.testbackup\\package.json'

und laedt die Extension gar nicht - waehrend der richtige Ordner daneben liegt und
niemand ihn registriert hat. Genau dieser Zustand lag am 2026-07-30 vor.

Es ist dieselbe SORTE Fehler wie das `cmd /c` bei den Hooks: am Ordner ist nichts zu
sehen. `install.ps1 -Check` hat damals Datei und Hash geprueft und gruen gemeldet,
waehrend die Bruecke zum Panel tot war. Darum steht das Urteil hier - in Python, mit
Tests - und nicht als Absatz in der Doku.

CHIRURGISCH wie bei den Hooks: die Datei wird nicht geschrieben, sondern ergaenzt. In
derselben Registratur stehen ALLE Extensions des Nutzers; ein Vollneubau wuerde sie
deinstallieren. Unser Eintrag wird am Ordnernamen wiedererkannt (auch als
`agent-deck-bridge.irgendwas`) und umgebogen, statt einen zweiten daneben zu legen.

Aufruf (aus install.ps1, oder von Hand im Repo-Wurzelverzeichnis):
  python -m deck.ops.vscode_ext            registrieren / geradebiegen
  python -m deck.ops.vscode_ext --check    nur pruefen, Exit 1 bei einem Befund
  python -m deck.ops.vscode_ext --remove   unseren Eintrag herausnehmen
  python -m deck.ops.vscode_ext --extensions-dir <pfad>   gegen einen ANDEREN Ordner

--extensions-dir ist die Sicherung fuer den Test: ein Probelauf gegen eine Wegwerf-
Registratur biegt nicht die Extensions des Rechners um, auf dem geprueft wird.

Wichtig: VS Code liest die Registratur beim Start des Extension-Hosts. Eine Reparatur
wirkt erst nach "Developer: Reload Window" - und zwar pro Fenster.
"""
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from typing import Any

from deck.domain import paths

# So nennt VS Code uns in der Registratur (publisher "local" aus package.json), und so
# heisst der Ordner. Der Ordnername ist eine DOPPELUNG mit $ExtDst in install.ps1 - dass
# beide gleich bleiben, prueft tests/test_ops_vscode_ext.py.
EXT_ID = "local.agent-deck-bridge"
EXT_DIR = "agent-deck-bridge"

# Die Datei, in der VS Code fuehrt, was installiert ist.
REGISTRY = "extensions.json"


def extensions_dir(home: str | None = None) -> str:
    """Wo VS Code die Extensions des Nutzers ablegt."""
    base = home or os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(base, ".vscode", "extensions")


def registry_path(ext_dir: str) -> str:
    return os.path.join(ext_dir, REGISTRY)


def load(path: str) -> list[Any] | None:
    """Die Registratur lesen. None heisst "nicht lesbar oder kein JSON-Array" - und das
    ist ausdruecklich NICHT dasselbe wie eine leere Liste: auf None darf nicht
    geschrieben werden (siehe main), sonst deinstalliert ein Tippfehler in der Datei
    alle Extensions des Nutzers."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def save(entries: list[Any], path: str) -> None:
    """Atomar schreiben, im Format das VS Code selbst schreibt: eine Zeile, kompakt,
    UTF-8 OHNE BOM. ensure_ascii=False, weil in den Pfaden Umlaute stehen und VS Code
    sie dort unescaped fuehrt - ein "\\u00df" waere ein anderer String."""
    raw = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    tmp = path + ".deck-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    os.replace(tmp, path)


def _uri_path(fs_path: str) -> str:
    """C:\\Users\\x\\.vscode\\extensions\\y  ->  /c:/Users/x/.vscode/extensions/y

    Die Form ist nicht frei gewaehlt, sondern abgelesen: genau so schreibt VS Code den
    Wert fuer eine lokal hineinkopierte Extension - fuehrender Schraegstrich,
    Vorwaertsschraegstriche, KLEINER Laufwerksbuchstabe und die Umlaute unescaped. Das
    prozentkodierte Gegenstueck (`external`) fuehrt VS Code nur bei Eintraegen aus dem
    Marketplace mit; wir schreiben es darum nicht.
    """
    p = fs_path.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", p):
        return "/" + p[0].lower() + p[1:]
    return p if p.startswith("/") else "/" + p


def _location(ext_dir: str, rel: str) -> dict[str, Any]:
    """Der `location`-Wert. `$mid: 1` ist VS Codes Marker fuer eine serialisierte URI -
    ohne ihn kommt der Wert als gewoehnliches Objekt an und nicht als Pfad."""
    return {"$mid": 1, "path": _uri_path(os.path.join(ext_dir, rel)), "scheme": "file"}


def _norm(p: str) -> str:
    """Pfade vergleichbar machen: Windows unterscheidet weder Gross-/Kleinschreibung
    noch die Richtung der Schraegstriche, ein Stringvergleich aber schon."""
    return p.replace("\\", "/").lower()


def is_ours(entry: Any) -> bool:
    """Gehoert ein Eintrag uns?

    Erkannt wird an der ID und - wie bei den Hooks am Dateinamen - zusaetzlich am
    ORDNERNAMEN. So findet ein Lauf auch den Eintrag eines UMBENANNTEN Ordners
    (`agent-deck-bridge.testbackup`) und biegt ihn um, statt einen zweiten daneben zu
    legen. Zwei Eintraege waeren nicht harmlos: VS Code nimmt dann einen davon, und
    welchen, entscheidet die Reihenfolge in der Datei.
    """
    if not isinstance(entry, dict):
        return False
    ident = entry.get("identifier")
    if isinstance(ident, dict) and ident.get("id") == EXT_ID:
        return True
    rel = entry.get("relativeLocation")
    if isinstance(rel, str):
        head = rel.replace("\\", "/").split("/")[0]
        return head == EXT_DIR or head.startswith(EXT_DIR + ".")
    return False


def audit(entries: list[Any] | None, ext_dir: str, *,
          exists: Callable[[str], bool] = os.path.isfile) -> list[tuple[str, str]]:
    """Urteile ueber die Registratur: Liste von (grad, text), grad in {ok, warn, fail}.

    Rein bis auf die eine Dateisystem-Frage, und die kommt als Funktion herein - so ist
    jede Regel ohne Dateisystem pruefbar. `entries is None` heisst "Registratur nicht
    lesbar"; geprueft wird genau das, was am Ordner NICHT zu sehen ist.
    """
    out: list[tuple[str, str]] = []
    installiert = exists(os.path.join(ext_dir, EXT_DIR, "package.json"))

    if entries is None:
        out.append(("warn", f"{REGISTRY} nicht lesbar — VS Code noch nie gestartet? "
                            "Ob die Extension geladen wird, ist von hier nicht zu sehen"))
        return out

    mine = [e for e in entries if is_ours(e)]
    if not mine:
        if installiert:
            out.append(("fail", f"{EXT_DIR}\\ liegt da, steht aber nicht in {REGISTRY} — "
                                "VS Code laedt die Extension nicht"))
        else:
            out.append(("fail", "Extension weder installiert noch registriert"))
        return out

    if len(mine) > 1:
        out.append(("warn", f"{len(mine)} Eintraege fuer {EXT_ID} — VS Code nimmt einen "
                            "davon, und welchen, entscheidet die Reihenfolge"))

    for e in mine:
        rel = e.get("relativeLocation")
        if not isinstance(rel, str) or not rel:
            out.append(("fail", "Eintrag ohne relativeLocation"))
            continue
        if not exists(os.path.join(ext_dir, rel, "package.json")):
            out.append(("fail", f"Eintrag zeigt ins Leere ({rel}) — VS Code bricht das "
                                "Laden ab: 'Unable to read file ... package.json'"))
        elif rel != EXT_DIR:
            out.append(("warn", f"Eintrag zeigt auf {rel}, erwartet ist {EXT_DIR}"))
        # location und relativeLocation koennen auseinanderlaufen. Beide werden benutzt,
        # darum ist ein Widerspruch ein Befund und keine Kosmetik.
        loc = e.get("location")
        got = loc.get("path") if isinstance(loc, dict) else None
        if isinstance(got, str) and _norm(got) != _norm(_uri_path(os.path.join(ext_dir, rel))):
            out.append(("fail", f"location.path widerspricht relativeLocation ({got})"))

    if not out:
        out.append(("ok", f"{EXT_ID} ist auf {EXT_DIR}\\ registriert"))
    return out


def repair(entries: list[Any], ext_dir: str,
           version: str | None = None) -> tuple[list[Any], list[tuple[str, str]]]:
    """Genau EINEN korrekten Eintrag hinterlassen. Rein, kein IO.

    Rueckgabe (neue Liste, Meldungen als (grad, text)). Fremde Extensions bleiben an
    ihrem Platz UND in ihrer Reihenfolge - sie stehen in derselben Datei, und VS Code
    laedt sie daraus. Ein vorhandener Eintrag wird ERGAENZT, nicht ersetzt: was VS Code
    sonst noch hineingeschrieben hat, soll erhalten bleiben.
    """
    out: list[Any] = []
    notes: list[tuple[str, str]] = []
    gesetzt = False
    for e in entries:
        if not is_ours(e):
            out.append(e)
            continue
        if gesetzt:
            notes.append(("info", "zweiten Eintrag entfernt"))
            continue
        gesetzt = True
        fixed = dict(e)
        fixed["identifier"] = {"id": EXT_ID}
        if version:
            fixed["version"] = version
        fixed["location"] = _location(ext_dir, EXT_DIR)
        fixed["relativeLocation"] = EXT_DIR
        out.append(fixed)
        if fixed != e:
            notes.append(("info", f"Eintrag umgebogen: {e.get('relativeLocation')} "
                                  f"-> {EXT_DIR}"))
    if not gesetzt:
        out.append({"identifier": {"id": EXT_ID}, "version": version or "0.0.0",
                    "location": _location(ext_dir, EXT_DIR), "relativeLocation": EXT_DIR})
        notes.append(("info", f"Eintrag angelegt: {EXT_ID} -> {EXT_DIR}"))
    return out, notes


def remove(entries: list[Any]) -> tuple[list[Any], list[tuple[str, str]]]:
    """Unseren Eintrag herausnehmen; fremde Extensions bleiben unangetastet."""
    out = [e for e in entries if not is_ours(e)]
    n = len(entries) - len(out)
    return out, ([("info", f"{n} Eintrag aus {REGISTRY} entfernt")] if n else [])


def installed_version(ext_dir: str) -> str | None:
    """Die Version aus der package.json. Erst die INSTALLIERTE (das beschreibt die
    Registratur), sonst die des Repos - nach `paths.REPO_ROOT`, nie nach `__file__`."""
    for p in (os.path.join(ext_dir, EXT_DIR, "package.json"),
              os.path.join(paths.REPO_ROOT, "extension", "package.json")):
        try:
            with open(p, encoding="utf-8") as f:
                v = json.load(f).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(v, str) and v:
            return v
    return None


def _backup(path: str) -> str | None:
    """Kopie neben die Datei legen, bevor wir sie anfassen. Hier wiegt das schwerer als
    bei den Hooks: in dieser Datei stehen ALLE Extensions des Nutzers."""
    if not os.path.isfile(path):
        return None
    dst = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def _arg_value(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    ext_dir = _arg_value(args, "--extensions-dir") or extensions_dir()
    path = registry_path(ext_dir)
    porcelain = "--porcelain" in args        # Bilanzzeile fuer install.ps1
    before = load(path)

    if "--check" in args:
        findings = audit(before, ext_dir)
        for grad, text in findings:
            print(f"  {'[ok]  ' if grad == 'ok' else '[' + grad + ']'} {text}")
        fails = sum(1 for g, _t in findings if g == "fail")
        warns = sum(1 for g, _t in findings if g == "warn")
        if fails:
            print('         Geradebiegen: python -m deck.ops.vscode_ext '
                  '(danach in JEDEM Fenster "Developer: Reload Window")')
        if porcelain:
            print(f"## fails={fails} warns={warns}")
        return 1 if fails else 0

    if before is None:
        # Die Unterscheidung ist der Sicherheitsgurt: eine Datei, die DA ist aber nicht
        # als JSON-Array liest, wird NICHT ueberschrieben - sonst nimmt ein Lauf dem
        # Nutzer alle uebrigen Extensions weg. Neu anlegen nur, wenn es sie nicht gibt.
        if os.path.exists(path):
            print(f"  [fail] {REGISTRY} ist kein lesbares JSON-Array — nicht angefasst.")
            print(f"         Von Hand nachsehen: {path}")
            if porcelain:
                print("## fails=1 warns=0")
            return 1
        if not os.path.isdir(ext_dir):
            print("  [warn] Kein ~/.vscode/extensions — VS Code nicht installiert?")
            if porcelain:
                print("## fails=0 warns=1")
            return 0
        before = []

    if "--remove" in args:
        after, notes = remove(before)
    elif not os.path.isfile(os.path.join(ext_dir, EXT_DIR, "package.json")):
        # Registrieren, was nicht da ist, erzeugt genau den Geistereintrag, gegen den
        # dieses Modul geschrieben wurde.
        print(f"  [fail] {EXT_DIR}\\ ist nicht installiert — nichts zu registrieren.")
        if porcelain:
            print("## fails=1 warns=0")
        return 1
    else:
        after, notes = repair(before, ext_dir, installed_version(ext_dir))

    if after == before:
        print("  [ok]   Registratur ist schon auf Stand.")
    else:
        bak = _backup(path)
        save(after, path)
        print(f"  [ok]   {path}")
        if bak:
            print(f"         Sicherung: {bak}")
    for grad, text in notes:
        print(f"    {'·' if grad == 'info' else '[warn]'} {text}")
    if porcelain:
        print(f"## fails=0 warns={sum(1 for g, _t in notes if g == 'warn')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
