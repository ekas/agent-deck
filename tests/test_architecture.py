"""Die Schichtgrenzen von deck/ - hier erzwungen, nicht nur in CLAUDE.md behauptet.

Eine Schichttabelle in der Doku veraltet still: sie wird gelesen, wenn man sie schon
nicht mehr braucht, und nicht gelesen, wenn man gerade dagegen verstoesst. Dieser Test
liest die echten Importe und wird rot, sobald eine Abhaengigkeit nach oben zeigt.

Die Ordnung ist: domain und platform wissen von niemandem. Darauf baut render (Bilder),
net (Broker), claude (Hooks, Usage) und ops (Betrieb). Darauf dock (Andocken), und ganz
oben ui, das alles kennen darf.
"""
import ast
import os

import helpers  # setzt sys.path und die Deck-Sprache

# Was jede Schicht importieren DARF. Die Tabelle ist ABSICHTLICH knapp gehalten: sie
# listet, was heute wirklich importiert wird, nicht was denkbar waere. Ein neuer Import
# macht den Test also auch dann rot, wenn er die Ordnung einhaelt - und genau das ist
# der Zweck. Dann traegt man ihn hier ein und hat einmal darueber nachgedacht.
ALLOWED = {
    "domain":   set(),
    "platform": set(),
    "render":   {"domain", "platform"},
    "net":      {"domain"},
    "claude":   {"domain", "i18n"},
    "ops":      {"domain", "platform", "i18n"},
    "dock":     {"domain", "platform", "render"},
    "ui":       {"domain", "platform", "render", "net", "claude", "ops", "dock", "i18n"},
    # i18n liegt bewusst auf der Paketwurzel und ist die EINZIGE Ausnahme nach oben:
    # der Sprachregler des Decks steht in Claudes settings.json, also muss i18n dort
    # lesen. Ein Querschnittsmodul wie Logging - jede Schicht darf es benutzen.
    "i18n":     {"claude"},
}

DECK = os.path.join(helpers.ROOT, "deck")


def _layer_of(relpath):
    """'domain/paths.py' -> 'domain';  'i18n.py' -> 'i18n'."""
    parts = relpath.split("/")
    return parts[0] if len(parts) > 1 else parts[0][:-3]


def _targets(node):
    """Alle deck-Module, die EIN Import-Knoten anzieht.

    Die Form `from deck import i18n` nennt als Modul nur 'deck' - das Ziel steht in den
    importierten Namen. Wer das uebersieht, ist blind fuer genau die Kante, um die es
    hier geht (i18n ist die eine Ausnahme nach oben).
    """
    out = []
    if isinstance(node, ast.ImportFrom) and node.module:
        out.append(node.module)
        if node.module == "deck":
            out += [f"deck.{a.name}" for a in node.names]
    elif isinstance(node, ast.Import):
        out += [a.name for a in node.names]
    return out


def _imports():
    """[(quell-schicht, ziel-schicht, datei, zeile)] fuer alle deck-internen Importe."""
    out = []
    for base, dirs, files in os.walk(DECK):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, DECK).replace("\\", "/")
            src_layer = _layer_of(rel)
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                for mod in _targets(node):
                    parts = mod.split(".")
                    if parts[0] != "deck" or len(parts) < 2:
                        continue
                    dst = parts[1]
                    if dst == src_layer:
                        continue
                    out.append((src_layer, dst, rel, node.lineno))
    return out


def test_jede_schicht_kennt_nur_die_unter_ihr():
    """Kein Import zeigt nach oben. Verstoesse werden einzeln benannt."""
    bad = []
    for src, dst, rel, line in _imports():
        assert src in ALLOWED, f"unbekannte Schicht {src!r} ({rel}) - ALLOWED ergaenzen"
        if dst not in ALLOWED[src]:
            bad.append(f"{rel}:{line}  {src} -> {dst}")
    assert not bad, "Schichtverletzung:\n  " + "\n  ".join(bad)


def test_die_tabelle_beschreibt_die_wirklichkeit():
    """Jede Schicht in ALLOWED existiert auch als Ordner (bzw. als i18n-Modul).

    Sonst bleibt eine Regel fuer ein umbenanntes Paket stehen und deckt nichts mehr ab.
    """
    for layer in ALLOWED:
        ist_ordner = os.path.isdir(os.path.join(DECK, layer))
        ist_modul = os.path.isfile(os.path.join(DECK, layer + ".py"))
        assert ist_ordner or ist_modul, f"ALLOWED nennt {layer!r}, das es nicht gibt"


def test_domain_bleibt_ohne_anzeige():
    """domain/ ist der getestete Kern: kein tkinter, kein Pillow, kein Win32.

    Die Faustregel des Repos lautet, dass Rechnen ohne Bildschirm testbar bleibt. Ein
    tkinter-Import in domain/ hebelt sie aus, und zwar unbemerkt - der Import
    funktioniert ja.
    """
    verboten = ("tkinter", "PIL", "ctypes")
    bad = []
    for name in sorted(os.listdir(os.path.join(DECK, "domain"))):
        if not name.endswith(".py"):
            continue
        path = os.path.join(DECK, "domain", name)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            for mod in mods:
                if mod.split(".")[0] in verboten:
                    bad.append(f"domain/{name}:{node.lineno}  {mod}")
    assert not bad, "Anzeige-Abhaengigkeit in domain/:\n  " + "\n  ".join(bad)


def test_jeder_import_findet_seinen_namen():
    """`from deck.x import y` muss ein y treffen, das es gibt.

    Diese Prüfung existiert wegen eines konkreten Beinahe-Unfalls: beim Aufteilen von
    usage.py wanderten severity_color und tooltip_text nach usage_view, aber
    ui/bottombar.py holte sie weiter aus usage — und zwar in einem Import INNERHALB einer
    Funktion. Syntaxprüfung und Testlauf sahen davon nichts; geknallt wäre es erst beim
    Zeichnen der Bottom-Bar, also im Betrieb.

    Geprüft wird statisch (ohne die Module zu importieren): Was bindet die Zieldatei auf
    Modulebene?
    """
    bound = {}          # 'deck.claude.usage' -> {gebundene Namen}
    for base, dirs, files in os.walk(DECK):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, DECK).replace("\\", "/")
            mod = "deck." + rel[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[:-len(".__init__")]
            tree = ast.parse(open(path, encoding="utf-8").read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.Import):
                    names |= {a.asname or a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names |= {a.asname or a.name for a in node.names}
            bound[mod] = names

    bad = []
    for base, dirs, files in os.walk(helpers.ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv")]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, helpers.ROOT).replace("\\", "/")
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if node.module not in bound:
                    continue
                for a in node.names:
                    if a.name == "*":
                        continue
                    # Ein Untermodul ist auch ein gültiges Ziel (from deck.claude import hooks)
                    if a.name in bound[node.module]:
                        continue
                    if f"{node.module}.{a.name}" in bound:
                        continue
                    bad.append(f"{rel}:{node.lineno}  from {node.module} import {a.name}")
    assert not bad, "Import zeigt auf einen Namen, den es nicht gibt:\n  " + "\n  ".join(bad)


def test_hooks_haengen_nicht_an_der_anzeige():
    """Die Hooks laufen als eigener Prozess bei JEDEM Tool-Aufruf.

    Wuerden sie tkinter oder Pillow nachziehen, kostete das bei jedem Hook-Start
    Ladezeit - und ein Importfehler in der Anzeige-Kette wuerde den Agenten blockieren,
    obwohl der Hook mit Anzeige nichts zu tun hat.
    """
    hooks = os.path.join(DECK, "claude", "hooks")
    bad = []
    for name in sorted(os.listdir(hooks)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(hooks, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            for mod in mods:
                head = mod.split(".")[0]
                if head in ("tkinter", "PIL"):
                    bad.append(f"claude/hooks/{name}:{node.lineno}  {mod}")
                if mod.startswith("deck.ui") or mod.startswith("deck.dock") \
                        or mod.startswith("deck.render"):
                    bad.append(f"claude/hooks/{name}:{node.lineno}  {mod}")
    assert not bad, "Hook zieht die Anzeige nach:\n  " + "\n  ".join(bad)
