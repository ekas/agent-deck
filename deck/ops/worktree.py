"""Einen an einen Agenten gehaengten git worktree wieder aufraeumen.

Hintergrund (siehe config.py / agent_deck.assign_ticket): einem laufenden Agenten
wird ein Ticket zugewiesen; er legt sich fuer den Branch `ticket/<slug>` SELBST
einen isolierten git worktree NEBEN dem Repo an (Variante B). Wird der Agent
geschlossen, soll dieser worktree verschwinden – sonst bleiben verwaiste Arbeits-
verzeichnisse liegen.

Woher das Panel den worktree kennt, sind ZWEI voneinander unabhaengige Signale
(beide werden beim Schliessen zusammengefuehrt):
  1) EXAKTER PFAD: der Agent schreibt den absoluten worktree-Pfad in die Marker-
     Datei state/<slot>.worktree (analog zur Ticket-Marker-Datei). Deckt manuellen
     UND Such-Weg exakt ab.
  2) BRANCH-FALLBACK: report.py merkt sich das cwd (= Repo-Root) des Agenten; damit
     findet `git worktree list` den worktree fuer den Ticket-Branch – greift auch,
     wenn der Agent (1) mal nicht geschrieben hat (nur zuverlaessig im manuellen
     Weg, weil dort der Branch aus der Ticket-ID feststeht).

SICHERHEIT (das Modul LOESCHT Verzeichnisse): entfernt wird nur, was zweifelsfrei
ein *verlinkter* worktree ist – dessen `.git` ist eine DATEI (`gitdir: …`), waehrend
ein Haupt-Checkout ein `.git`-VERZEICHNIS hat. Alles andere (Haupt-Repo, Nicht-Git-
Ordner) wird verweigert. Der Branch/die Commits bleiben erhalten – nur das
Arbeitsverzeichnis des worktree geht weg (`git worktree remove`).

Reine stdlib. Der eigentliche Lauf (subprocess/rmtree/sleep) gehoert NICHT auf den
Tk-Thread; agent_deck ruft remove_worktree() aus einem Daemon-Thread.
"""
import os
import shutil
import stat
import subprocess
import time

from deck.domain.paths import STATE_DIR

# Auf Windows (pythonw) kein kurz aufblitzendes Konsolenfenster fuer git.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# Wartezeiten (Sekunden) zwischen den Versuchen: das geschlossene Terminal (und
# etwaige Kindprozesse) muss erst sterben und seine Datei-Handles freigeben, sonst
# scheitert das Loeschen auf Windows an gesperrten Dateien.
_RETRY_DELAYS = (1, 2, 4, 8)


# ── pure Helfer (unit-getestet) ──────────────────────────────────────────
def parse_worktrees_porcelain(text):
    """`git worktree list --porcelain` -> [{'path': str, 'branch': str|None}, …].

    Bloecke sind durch Leerzeilen getrennt; je Block eine 'worktree <pfad>'-Zeile
    und optional 'branch refs/heads/<name>' (fehlt bei detached HEAD). 'refs/heads/'
    wird abgeschnitten, sodass 'branch' direkt mit ticket_branch() vergleichbar ist."""
    entries = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("worktree "):
            if cur is not None:
                entries.append(cur)
            cur = {"path": line[len("worktree "):].strip(), "branch": None}
        elif line.startswith("branch ") and cur is not None:
            ref = line[len("branch "):].strip()
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    if cur is not None:
        entries.append(cur)
    return entries


def main_path(entries):
    """Der Haupt-Checkout ist der erste Eintrag der porcelain-Liste."""
    return entries[0]["path"] if entries else None


def path_for_branch(entries, branch):
    """Pfad des worktree, der auf <branch> ausgecheckt ist (oder None)."""
    if not branch:
        return None
    for e in entries:
        if e.get("branch") == branch:
            return e["path"]
    return None


def wt_dir_for_repo(repo_root):
    """Verzeichnis, in dem die deck-beauftragten worktrees eines Repos liegen. Per
    Konvention (siehe config.TICKET_PROMPT: `<repo-root>/../<repo-name>.wt/<slug>`)
    ist das IMMER der Geschwisterordner mit Repo-Namen + '.wt', also schlicht
    '<repo-root>.wt'. Reine Pfad-Rechnung. None bei leerem Root."""
    if not repo_root:
        return None
    return os.path.normpath(repo_root) + ".wt"


def repo_root_from_wt_dir(wt_dir):
    """Umkehr von wt_dir_for_repo: '<x>.wt' -> '<x>'. None, wenn der Pfad nicht auf
    '.wt' endet (dann ist es kein deck-worktree-Sammelordner). Aus einem worktree-
    Marker gewinnt man so das Repo-Root: repo_root_from_wt_dir(dirname(markerpfad))."""
    if not wt_dir:
        return None
    n = os.path.normpath(wt_dir)
    return n[:-len(".wt")] if n.lower().endswith(".wt") else None


# ── git / fs (unrein) ────────────────────────────────────────────────────
def _run_git(args, cwd):
    """git <args> in cwd; (returncode, stdout+stderr). Nie eine Exception (git fehlt/
    Timeout/kein Repo -> (1, meldung)).

    WICHTIG: git gibt Pfade als UTF-8 aus. Ohne explizites encoding wuerde Python auf
    (deutschem) Windows die ANSI-Codepage (cp1252) nehmen und Umlaute im Pfad zu
    Mojibake machen (ß -> ÃŸ, ü -> Ã¼) – der aus `worktree list` zurueckgeparste Repo-
    Root waere dann ein nicht existierender Pfad, `git -C <root> …` schluege fehl und
    das Aufraeumen (remove/prune) liefe ins Leere. Das Home des Nutzers enthaelt genau
    solche Zeichen. Darum hart UTF-8. `core.quotepath=false` haelt Nicht-ASCII zudem
    unescaped. (Gleiche Lektion wie beim Hook-stdin, report.py.)"""
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false", *list(args)], cwd=cwd,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, creationflags=_NO_WINDOW)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # git nicht gefunden, Timeout, …
        return 1, str(e)


def _worktrees(cwd):
    """Geparste worktree-Liste des Repos, in dem cwd liegt (leer bei Fehler)."""
    rc, out = _run_git(["worktree", "list", "--porcelain"], cwd=cwd)
    return parse_worktrees_porcelain(out) if rc == 0 else []


def worktree_for_branch(repo, branch):
    """Im Repo unter <repo> den worktree finden, der auf <branch> ausgecheckt ist."""
    if not repo or not branch or not os.path.isdir(repo):
        return None
    return path_for_branch(_worktrees(repo), branch)


def is_linked_worktree(path):
    """True NUR fuer einen verlinkten worktree – die zentrale Sicherung dieses Moduls
    (nur so etwas wird je geloescht). NICHT ausreichend ist der blosse Test '.git ist
    eine Datei': ein Submodul (`.git` -> `…/.git/modules/<name>`) und ein Haupt-Checkout
    mit ausgelagertem git-dir (`git init --separate-git-dir`) haben EBENFALLS eine
    `.git`-Datei und wuerden sonst faelschlich als wegwerfbar gelten. Ein verlinkter
    worktree ist eindeutig daran erkennbar, dass sein `gitdir:` unter `…/.git/worktrees/
    <name>` liegt. Bewusst git-UNABHAENGIG geprueft: die Selbst-Verweigerung ueber
    `git worktree list` (main==norm) faellt weg, wenn git fehlt/haengt – dann ist dies
    die einzige Schranke vor dem rmtree-Fallback."""
    try:
        gitfile = os.path.join(path, ".git")
        if not os.path.isfile(gitfile):
            return False
        with open(gitfile, encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
    except OSError:
        return False
    if not first.startswith("gitdir:"):
        return False
    gitdir = first[len("gitdir:"):].strip()
    # verlinkter worktree -> parent des gitdir heisst 'worktrees'; Submodul -> 'modules';
    # separate-git-dir Haupt-Checkout -> irgendetwas anderes. Nur 'worktrees' ist tabu-frei.
    return os.path.basename(os.path.dirname(os.path.normpath(gitdir))) == "worktrees"


def list_child_dirs(parent):
    """Direkte Unterordner von <parent> als absolute Pfade (leer, wenn <parent> kein
    Verzeichnis ist / nicht lesbar). Gedacht fuers Absuchen eines '<repo>.wt'-Ordners
    nach den darin liegenden worktrees – Dateien/Symlinks werden uebersprungen."""
    try:
        return [os.path.join(parent, n) for n in os.listdir(parent)
                if os.path.isdir(os.path.join(parent, n))]
    except OSError:
        return []


def _force_writable(func, path, _exc):
    """rmtree-onerror: Schreibschutz weg (git/objects & Windows) und einmal erneut."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_worktree(path):
    """Einen verlinkten git worktree entfernen. Gibt True zurueck, wenn er weg ist
    (oder schon war). Verweigert alles, was kein verlinkter worktree ist.

    Vorgehen je Versuch: bevorzugt `git worktree remove --force` vom Haupt-Checkout
    aus (git raeumt seine Verwaltung mit auf); klappt das nicht, das Verzeichnis
    direkt loeschen und die worktree-Verwaltung per `git worktree prune` bereinigen.
    Mehrere Versuche mit Pause, weil frisch geschlossene Prozesse auf Windows die
    Dateien noch kurz sperren. Der Branch bleibt erhalten."""
    if not path:
        return False
    norm = os.path.normpath(path)

    if not os.path.isdir(norm):
        return True  # schon weg

    if not is_linked_worktree(norm):
        _log("REFUSE (kein verlinkter worktree, .git ist kein File): " + norm)
        return False

    # Haupt-Checkout dieses worktree (fuer 'git worktree remove/prune'). Das Listing
    # laeuft VON INNEN (nur lesend – unkritisch); geloescht wird spaeter vom Haupt-
    # Checkout aus, dessen cwd NICHT im zu loeschenden Ordner liegt.
    main = main_path(_worktrees(norm))
    if main and os.path.normpath(main) == norm:
        _log("REFUSE (Pfad ist selbst der Haupt-Checkout): " + norm)
        return False

    last = ""
    for delay in _RETRY_DELAYS:
        time.sleep(delay)
        if main:
            rc, out = _run_git(["worktree", "remove", "--force", norm], cwd=main)
            if rc == 0 and not os.path.isdir(norm):
                _log("entfernt (git worktree remove): " + norm)
                return True
            last = out
        # Fallback: Verzeichnis selbst loeschen (rmtree nutzt den absoluten Pfad,
        # unabhaengig vom cwd -> kein "cwd im geloeschten Ordner"-Problem).
        try:
            shutil.rmtree(norm, onerror=_force_writable)
        except Exception as e:
            last = str(e)
        if not os.path.isdir(norm):
            if main:
                # --expire=now: sofort abraeumen (ohne Ablauf laesst prune den
                # Verwaltungseintrag .git/worktrees/<name> per Default 3 Monate stehen).
                _run_git(["worktree", "prune", "--expire=now"], cwd=main)
            _log("entfernt (rmtree" + (" + prune" if main else "") + "): " + norm)
            return True
    _log("FEHLGESCHLAGEN nach Versuchen: " + norm + " (" + str(last)[:200] + ")")
    return False


def remove_orphan_dir(path, repo=None):
    """Einen ABGERAEUMTEN worktree-Rest entfernen: ein Verzeichnis, das git nicht (mehr)
    als worktree fuehrt und KEINE .git-Datei mehr hat. Das entsteht auf Windows, wenn
    `git worktree remove` nur bis zur .git-Datei kam und dann eine gesperrte Datei das
    Loeschen des Verzeichnisses stoppte – uebrig bleibt ein Ordner ohne git-Verknuepfung.
    remove_worktree() VERWEIGERT so etwas (is_linked_worktree ist False), darum dieser
    eng begrenzte Zweitweg. Nur vom Disk-Orphan-Sweep genutzt, der zusaetzlich
    sicherstellt, dass <path> direkt unter einem erkannten '<repo>.wt/' liegt.

    SICHERHEIT: entfernt NUR ein Verzeichnis, das GAR KEIN '.git' (mehr) enthaelt – der
    Kern des 'halb abgeraeumten Rests'. Sobald ein '.git' vorhanden ist (egal ob DATEI
    oder VERZEICHNIS), wird verweigert: ein '.git'-Verzeichnis ist ein echter Checkout/
    Clone, eine '.git'-Datei ein verlinkter worktree (den nimmt remove_worktree), ein
    Submodul (…/modules/…) oder ein separate-git-dir-Checkout – alles fremd und tabu.
    Branch und Commits leben ohnehin im Repo weiter; hier faellt nur das tote Arbeits-
    verzeichnis. Mehrere Versuche mit Pause (frisch geschlossene Prozesse sperren die
    Dateien auf Windows noch kurz). Mit <repo> wird die worktree-Verwaltung zusaetzlich
    per `git worktree prune` bereinigt (haengender Verwaltungseintrag)."""
    if not path:
        return False
    norm = os.path.normpath(path)
    if not os.path.isdir(norm):
        return True  # schon weg
    if os.path.exists(os.path.join(norm, ".git")):
        _log("REFUSE orphan-dir (hat noch ein .git -> kein abgeraeumter Rest; "
             "Checkout/Submodul/separate-git-dir/worktree): " + norm)
        return False
    last = ""
    for delay in _RETRY_DELAYS:
        time.sleep(delay)
        try:
            shutil.rmtree(norm, onerror=_force_writable)
        except Exception as e:
            last = str(e)
        if not os.path.isdir(norm):
            if repo and os.path.isdir(repo):
                _run_git(["worktree", "prune", "--expire=now"], cwd=repo)
            _log("entfernt (orphan-dir rmtree" + (" + prune" if repo else "") + "): " + norm)
            return True
    _log("FEHLGESCHLAGEN orphan-dir nach Versuchen: " + norm + " (" + str(last)[:200] + ")")
    return False


def note(msg):
    """Oeffentlicher Protokoll-Eintrag fuer Aufrufer (z.B. das Deck, wenn es einen
    worktree bewusst NICHT loescht). Geht in dasselbe Log wie die Loeschungen."""
    _log(msg)


def _log(msg):
    """Best-effort-Protokoll nach STATE_DIR/worktree-cleanup.log (die Loeschung laeuft
    unsichtbar im Hintergrund; so ist nachvollziehbar, was passiert ist). Rotiert
    grob bei ~200 KB. Darf nie stoeren."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = os.path.join(STATE_DIR, "worktree-cleanup.log")
        try:
            mode = "w" if os.path.getsize(p) > 200_000 else "a"
        except OSError:
            mode = "a"
        with open(p, mode, encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass
