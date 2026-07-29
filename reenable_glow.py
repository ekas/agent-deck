#!/usr/bin/env python3
"""Agent Deck - Glow (Border-Beam um den fokussierten Chat) wieder aktivieren.

Repliziert 1:1, was die VS-Code-Extension "Custom CSS and JS Loader" beim Befehl
"Enable Custom CSS and JS" macht: den Inhalt der in settings.json unter
`vscode_custom_css.imports` eingetragenen CSS/JS-Dateien als <style>/<script>-Block
in VS Codes `workbench.html` injizieren.

WARUM noetig: Jedes VS-Code-Update ersetzt die `workbench.html` (unter Windows
liegt sie inzwischen in einem versionierten Hash-Ordner, z.B.
`...\\Microsoft VS Code\\<hash>\\resources\\app\\out\\vs\\code\\electron-browser\\
workbench\\workbench.html`). Dadurch verschwindet der Patch -> der Glow ist weg,
obwohl die settings.json unveraendert korrekt bleibt. Dieses Skript patcht die
aktuelle Datei neu.

Aufruf (aus diesem Ordner, damit der Skriptpfad ASCII bleibt):
    python reenable_glow.py           # aktivieren / neu patchen (idempotent)
    python reenable_glow.py --off     # Patch entfernen (Backup zuruecklegen)

Danach in VS Code: Command Palette -> "Developer: Reload Window".
HINWEIS: Reload startet die Terminals dieses Fensters neu (inkl. laufender
Claude-Session). Ggf. das ANDERE Fenster zuerst neu laden.
"""

import os
import re
import sys
import glob
import uuid
import shutil
from urllib.parse import urlparse, unquote

import i18n

HERE = os.path.dirname(os.path.abspath(__file__))

# Fallback, falls settings.json nicht gelesen werden kann.
DEFAULT_IMPORTS = [os.path.join(HERE, "agent-deck-glow.css")]

START = "<!-- !! VSCODE-CUSTOM-CSS-START !! -->"
END = "<!-- !! VSCODE-CUSTOM-CSS-END !! -->"


# ── VS-Code-Installationen / workbench.html finden ──────────────────────────
def vscode_bases():
    bases = []
    # 1) expliziter Override fuer beliebige Install-Orte
    override = os.environ.get("AGENT_DECK_VSCODE_DIR")
    if override:
        bases.append(override)
    # 2) Standard-Installationspfade
    la = os.environ.get("LOCALAPPDATA")
    if la:
        bases.append(os.path.join(la, "Programs", "Microsoft VS Code"))
        bases.append(os.path.join(la, "Programs", "Microsoft VS Code Insiders"))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(env)
        if pf:
            bases.append(os.path.join(pf, "Microsoft VS Code"))
            bases.append(os.path.join(pf, "Microsoft VS Code Insiders"))
    # 3) beliebiger Ort / portable: aus dem `code`-CLI auf dem PATH ableiten
    #    (<base>\bin\code.cmd -> <base>).
    for cli in ("code", "code.cmd", "code-insiders", "code-insiders.cmd"):
        p = shutil.which(cli)
        if p:
            bases.append(os.path.dirname(os.path.dirname(os.path.realpath(p))))
    # dedupe (nach realpath) + nur existierende Verzeichnisse
    seen, out = set(), []
    for b in bases:
        rb = os.path.realpath(b)
        if rb not in seen and os.path.isdir(rb):
            seen.add(rb)
            out.append(rb)
    return out


def find_workbench_files():
    names = ("workbench.html", "workbench.esm.html", "workbench-dev.html")
    subdirs = ("electron-browser", "electron-sandbox")  # neu / alt
    found = []
    for base in vscode_bases():
        for sub in subdirs:
            for name in names:
                # ** deckt sowohl <base>/resources/... als auch <base>/<hash>/resources/... ab
                pat = os.path.join(base, "**", "out", "vs", "code", sub, "workbench", name)
                found += glob.glob(pat, recursive=True)
    seen, out = set(), []
    for f in found:
        rf = os.path.realpath(f)
        if rf not in seen and os.path.isfile(rf):
            seen.add(rf)
            out.append(rf)
    return out


# ── Imports aus settings.json lesen ─────────────────────────────────────────
def fileurl_to_path(u):
    if u.startswith("file:"):
        p = unquote(urlparse(u).path)
        if re.match(r"^/[A-Za-z]:", p):  # Windows: /C:/... -> C:/...
            p = p[1:]
        return p
    return u


def read_imports():
    appdata = os.environ.get("APPDATA")
    candidates = []
    if appdata:
        candidates.append(os.path.join(appdata, "Code", "User", "settings.json"))
        candidates.append(os.path.join(appdata, "Code - Insiders", "User", "settings.json"))
    for sp in candidates:
        try:
            with open(sp, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue
        imports = _extract_imports(raw)
        if imports:
            return [fileurl_to_path(x) for x in imports]
    return DEFAULT_IMPORTS


def _extract_imports(raw):
    # Gezielt das imports-Array greifen und die Strings rausziehen. Bewusst KEIN
    # generisches JSONC-Parsing: "//" in file:///-URLs wuerde ein naiver
    # Kommentar-Stripper zerstoeren. file-URLs enthalten keine " -> [^"] reicht.
    m = re.search(r'"vscode_custom_css\.imports"\s*:\s*\[(.*?)\]', raw, re.S)
    if not m:
        return None
    return re.findall(r'"([^"]*)"', m.group(1))


# ── Patchen (identisch zur Extension) ───────────────────────────────────────
def clear_existing(html):
    html = re.sub(START + r".*?" + END + r"\n*", "", html, flags=re.S)
    html = re.sub(r"<!-- !! VSCODE-CUSTOM-CSS-SESSION-ID [\w-]+ !! -->\n*", "", html)
    html = re.sub(
        r"<script>/\* eslint-env browser \*/.*?__CUSTOM_CSS_JS_INDICATOR_CLS.*?</script>\n*",
        "", html, flags=re.S,
    )
    return html


def build_inject(imports):
    parts = []
    for p in imports:
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError ist KEIN OSError -> hier explizit mitfangen,
            # sonst bricht ein Nicht-UTF-8-Import den ganzen Patch ab.
            print("  ! Import nicht lesbar / kein UTF-8, uebersprungen: %s (%s)" % (p, e))
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext == ".css":
            parts.append("<style>%s</style>" % content)
        elif ext == ".js":
            parts.append("<script>%s</script>" % content)
        else:
            print("  ! Unbekannter Typ, uebersprungen: %s" % p)
    return "".join(parts)


def _sweep_backups(d, keep=None):
    for it in os.listdir(d):
        if it.endswith(".bak-custom-css") and it != keep:
            try:
                os.remove(os.path.join(d, it))
            except OSError:
                pass


def patch(wb, imports):
    with open(wb, "r", encoding="utf-8") as f:
        html = f.read()
    html = clear_existing(html)

    d = os.path.dirname(wb)
    session = str(uuid.uuid4())
    backup = os.path.join(d, "workbench.%s.bak-custom-css" % session)
    # Backup = das BEREINIGTE html (vor CSP-Entfernung), wie die Extension.
    with open(backup, "w", encoding="utf-8") as f:
        f.write(html)

    # CSP-<meta> entfernen (sonst koennte 'require-trusted-types-for' stoeren).
    html = re.sub(
        r'<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?/>', "", html, count=1
    )

    inject = build_inject(imports)
    block = (
        "<!-- !! VSCODE-CUSTOM-CSS-SESSION-ID %s !! -->\n%s\n%s%s\n</head>"
        % (session, START, inject, END)
    )
    new_html, n = re.subn(r"</head>", lambda _m: block, html, count=1)
    if n == 0:
        print("  ! Kein </head> gefunden - uebersprungen")
        os.remove(backup)
        return False

    # Atomar schreiben: erst in eine temp-Datei, dann os.replace (auf derselben
    # Platte atomar). So bleibt die live geladene workbench.html bei einem
    # Abbruch/Fehler unversehrt, statt halb geschrieben zu sein.
    tmp = wb + ".glow-tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_html)
        os.replace(tmp, wb)
    except Exception:
        for stray in (tmp, backup):  # eigene Reste aufraeumen, dann Fehler melden
            try:
                os.remove(stray)
            except OSError:
                pass
        raise

    # Erst nach erfolgreichem Schreiben aeltere Backups wegraeumen -> genau ein
    # aktuelles Backup pro Datei (die alten waeren durch clear_existing eh tot).
    _sweep_backups(d, keep=os.path.basename(backup))
    return True


def unpatch(wb):
    with open(wb, "r", encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"VSCODE-CUSTOM-CSS-SESSION-ID ([0-9a-fA-F-]+)", html)
    if not m:
        print("  (kein Patch vorhanden)")
        return False
    d = os.path.dirname(wb)
    bak = os.path.join(d, "workbench.%s.bak-custom-css" % m.group(1))
    if os.path.isfile(bak):
        with open(bak, "r", encoding="utf-8") as f:
            restored = f.read()
    else:
        restored = clear_existing(html)  # kein Backup mehr -> wenigstens Patch raus
    # Atomar zurueckschreiben (siehe patch()).
    tmp = wb + ".glow-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(restored)
    os.replace(tmp, wb)
    _sweep_backups(d)
    return True


# ── Programmatische API fuers Deck (kein print / kein sys.exit) ─────────────
def status():
    """(installed, n_workbench): ist der Glow-Patch aktuell in mindestens einer
    workbench.html vorhanden, und wie viele workbench-Dateien gibt es ueberhaupt?
    n_workbench == 0 -> keine VS-Code-Installation gefunden."""
    wbs = find_workbench_files()
    installed = False
    for wb in wbs:
        try:
            with open(wb, "r", encoding="utf-8") as f:
                if "VSCODE-CUSTOM-CSS-SESSION-ID" in f.read():
                    installed = True
                    break
        except OSError:
            pass
    return installed, len(wbs)


def set_glow(enabled):
    """Glow in ALLEN gefundenen workbench.html an- (enabled=True) bzw. abschalten,
    idempotent. Rueckgabe (ok, total, error): ok = Anzahl erfolgreich gepatchter/
    entfernter Dateien, total = gefundene workbench-Dateien, error = None oder eine
    kurze Meldung (VS Code offen / keine Rechte). Fuer den Aufruf aus dem Deck – wirft
    nicht, gibt nichts aus."""
    wbs = find_workbench_files()
    if not wbs:
        return 0, 0, i18n.L("Keine VS-Code-Installation gefunden.",
                            "No VS Code installation found.")
    imports = read_imports() if enabled else None
    ok, err = 0, None
    for wb in wbs:
        try:
            done = patch(wb, imports) if enabled else unpatch(wb)
            if done:
                ok += 1
        except PermissionError:
            err = i18n.L("Keine Schreibrechte – VS Code schließen und erneut versuchen.",
                         "No write permission – close VS Code and try again.")
        except Exception as e:  # noqa: BLE001 - dem Aufrufer melden, nie crashen
            err = str(e)
    return ok, len(wbs), err


def main():
    off = "--off" in sys.argv
    wbs = find_workbench_files()
    if not wbs:
        print("Keine VS-Code workbench.html gefunden. Ist VS Code installiert?")
        sys.exit(1)

    imports = read_imports()
    print(("Deaktiviere" if off else "Aktiviere") + " Glow.")
    if not off:
        print("Imports:")
        for p in imports:
            print("  - %s%s" % (p, "   (FEHLT!)" if not os.path.isfile(p) else ""))
    print("%d workbench-Datei(en) gefunden:" % len(wbs))

    ok = 0
    for wb in wbs:
        print("* " + wb)
        try:
            done = unpatch(wb) if off else patch(wb, imports)
            if done:
                ok += 1
                print("  -> ok")
        except PermissionError:
            print("  ! Keine Schreibrechte. VS Code schliessen bzw. Terminal als Admin.")
        except Exception as e:  # noqa: BLE001 - Diagnose ausgeben, nie hart crashen
            print("  ! Fehler: %s" % e)

    print("\nFertig (%d/%d)." % (ok, len(wbs)))
    if not off and ok:
        print("Jetzt in VS Code: Command Palette -> 'Developer: Reload Window'.")
        print("(Reload startet die Terminals dieses Fensters neu.)")


if __name__ == "__main__":
    main()
