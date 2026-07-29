"""Verwaiste git worktrees abraeumen - beim Schliessen einer Kachel und periodisch.

Zwei Wege, weil ein Marker verloren gehen kann: der direkte (Marker-Datei nennt den
worktree) und der Disk-Sweep, der die '<repo>.wt'-Ordner absucht.

Beide haben einen Guard gegen fremde Slots: ein worktree, der einem anderen Agenten
gehoert, wird nie angefasst - und geloescht wird nur, was git selbst als verlinkten
worktree bestaetigt.
"""
import os
import threading

from deck.domain import slot_state as dc
from deck.domain.binding import ticket_branch as _ticket_branch
from deck.domain.binding import ticket_slug as _ticket_slug
from deck.ops import worktree as wtc
from deck.ui.theme import WINDOWS
from deck.ui.theme import WT_DISK_ORPHAN_GRACE_S
from deck.ui.theme import WT_DISK_SWEEP_INTERVAL_S
from deck.ui.theme import WT_ORPHAN_GRACE_S


class WorktreeSweepMixin:
    """Wird in AgentDeck eingemischt (siehe panel.py)."""

    def _cleanup_worktrees(self, slot):
        """Beim Schliessen eines Agenten dessen git worktree(s) entfernen. Auf dem
        Tk-Thread werden hier NUR die guenstigen Signale eingesammelt (solange Ticket/
        Marker noch da sind): der exakte Marker-Pfad dieses Slots, der Ticket-Branch +
        Repo-cwd fuer den Fallback und – wichtig – die Pfade, die ANDERE Slots per Marker
        beanspruchen. Das eigentliche Entfernen samt der (blockierenden) `git worktree
        list`-Fallbacksuche laeuft im Daemon-Thread, damit die UI nicht haengt.

        Der Fremd-Slot-Schutz verhindert Datenverlust, wenn sich zwei Agenten denselben
        worktree teilen (gleiches Ticket -> gleicher Branch; git erlaubt den Branch nur in
        EINEM worktree, also arbeiten dann beide im selben Verzeichnis): den worktree, den
        noch ein anderer offener Slot beansprucht, NIE loeschen."""
        markers = dc.read_found_worktrees()
        exact = markers.get(slot)
        ticket = self.tickets.get(slot) or self._found.get(slot)
        branch = _ticket_branch(ticket) if ticket else ""
        repo = (dc.read_all().get(slot) or {}).get("cwd") if ticket else None
        # Pfade, die ein ANDERER Slot per exaktem Marker beansprucht -> tabu.
        others = {os.path.normpath(p) for s, p in markers.items() if s != slot and p}
        self._clear_worktree_marker(slot)
        if exact or (branch and repo):
            threading.Thread(target=self._remove_worktrees_bg,
                             args=(exact, branch, repo, others), daemon=True).start()

    @staticmethod
    def _remove_worktrees_bg(exact, branch, repo, others):
        """Hintergrund-Thread: worktree(s) dieses Slots entfernen (best effort). Ruft NICHT
        ins Tk zurueck -> nur Dateisystem/git, darum ungefaehrlich. Der Branch-Fallback
        (`git worktree list`) laeuft NUR hier (blockierender subprocess) und NUR, wenn der
        exakte Marker nichts lieferte – das spart die Abfrage und trifft nicht den worktree
        eines anderen Agenten am selben Branch. Von einem anderen Slot beanspruchte Pfade
        (`others`) werden uebersprungen, nicht geloescht."""
        paths = {}
        def _add(p):
            if p:
                paths.setdefault(os.path.normpath(p), p)
        _add(exact)
        if not exact and branch and repo:
            try:
                _add(wtc.worktree_for_branch(repo, branch))
            except Exception:
                pass
        for norm, p in paths.items():
            if norm in others:
                wtc.note("uebersprungen (noch von anderem Slot beansprucht): " + p)
                continue
            try:
                wtc.remove_worktree(p)
            except Exception:
                pass

    def _sweep_orphan_worktrees(self, now):
        """Verwaiste git worktrees im Hintergrund abraeumen: fuer jeden gemeldeten
        worktree-Marker (state/<slot>.worktree) pruefen, ob der zugehoerige Agent noch
        LEBT (Slot unter den Terminals seines VERBUNDENEN Fensters). Fehlt er, ist der
        worktree verwaist und wird – nach einer kurzen Grace (WT_ORPHAN_GRACE_S) gegen
        Terminal-Listen-Aussetzer – ueber dieselbe sichere Maschinerie wie beim Agenten-
        Schliessen entfernt (Marker/Ticket mit). Deckt die Faelle ab, in denen
        _cleanup_worktrees NIE lief: Agent extern geschlossen (Terminal gekillt), Deck
        war beim Schliessen aus, oder ein Marker aus einer frueheren Session liegt noch.

        Bewusst NICHT angetastet: ein gebundenes, aber gerade getrenntes Fenster (Reload/
        kurzer Abriss) – dort besitzt _cleanup_closed_windows das Aufraeumen (mit eigener
        Grace + echter Fenster-zu-Pruefung per Win32). Sonst raeumte ein Reload den
        worktree faelschlich weg, waehrend das native Fenster noch offen ist."""
        markers = self._worktrees                  # in refresh() frisch gelesen
        if not markers:
            self._wt_gone_since.clear()
            return
        # Lebende Slots = Terminals aller VERBUNDENEN Fenster.
        live, connected = set(), set()
        for w in WINDOWS:
            if self.broker.connected(w):
                connected.add(w)
                live.update(self.broker.terminals(w))
        orphans = []
        for slot in markers:
            if slot in live:
                self._wt_gone_since.pop(slot, None)    # Agent lebt -> Uhr aus
                continue
            win = slot[0] if slot else ""
            # Gebunden, aber getrennt (Reload/kurzer Abriss) -> nicht wir; das erledigt
            # _cleanup_closed_windows, sobald das VS-Code-Fenster wirklich zu ist.
            if win not in connected and self.bindings.get(win):
                self._wt_gone_since.pop(slot, None)
                continue
            t0 = self._wt_gone_since.get(slot)
            if t0 is None:
                self._wt_gone_since[slot] = now        # erstmals als verwaist gesehen -> Uhr starten
            elif now - t0 >= WT_ORPHAN_GRACE_S:
                orphans.append(slot)
        for slot in orphans:
            self._cleanup_worktrees(slot)              # entfernt worktree (bg-Thread) + loescht den Marker
            if self.tickets.pop(slot, None) is not None:
                self.store.save_tickets()
            self._clear_found_ticket(slot)
            self._found.pop(slot, None)
            self._worktrees.pop(slot, None)            # Snapshot dieses Polls angleichen (Marker ist weg)
            self._wt_gone_since.pop(slot, None)
        # Uhren fuer Slots ohne Marker aufraeumen (Dict sauber halten).
        for slot in list(self._wt_gone_since):
            if slot not in markers:
                self._wt_gone_since.pop(slot, None)

    def _sweep_disk_worktrees(self, now, states):
        """Zweiter, MARKER-UNABHAENGIGER Orphan-Sweep. Durchsucht ~minuetlich die
        '<repo>.wt/'-Ordner der bekannten Repos DIREKT auf der Platte nach worktrees,
        an denen kein lebender Agent mehr haengt, und raeumt sie ab. Faengt genau die
        Faelle, die der marker-getriebene _sweep_orphan_worktrees NICHT sieht, weil
        keine state/<slot>.worktree-Datei (mehr) auf sie zeigt:
          • der Agent hat den Pfad-Marker nie geschrieben (Absturz / Prompt ignoriert),
          • ein frueheres Aufraeumen hat den Marker geloescht, aber das Verzeichnis
            blieb liegen (Windows-Dateisperre -> `git worktree remove` scheiterte),
          • ein '<repo>.wt/<slug>'-Rest ganz ohne .git (nur halb abgeraeumt).

        'deck-beauftragt' = liegt per Konvention (config.TICKET_PROMPT) unter
        '<repo-root>.wt/'. 'zugehoeriger Agent lebt' = ein Slot eines VERBUNDENEN
        Fensters, dessen worktree-Marker auf den Ordner zeigt ODER dessen zugewiesenes/
        gefundenes Ticket denselben Slug (= Ordnername) hat. Der Branch-NAME taugt
        bewusst NICHT als Kriterium: Agenten benennen den Branch oft nach Repo-Konvention
        (z.B. 'bugfix/PROJ-2701-...'), legen den Ordner aber trotzdem als '<slug>' an.

        Nur der leichte Teil (Zustand einsammeln + throtteln) laeuft hier auf dem Tk-
        Thread; das blockierende `git worktree list` + os.listdir + das Loeschen macht
        _disk_sweep_bg in einem Daemon-Thread (nie zwei parallel: _disk_sweep_busy). Die
        Grace (WT_DISK_ORPHAN_GRACE_S) gegen frisch angelegte, noch nicht gemeldete
        worktrees traegt _wt_disk_gone_since – das fasst NUR der bg-Thread an, und da
        der Tk-Thread waehrenddessen keinen zweiten startet, ist der Zugriff race-frei."""
        if self._disk_sweep_busy or (now - self._last_disk_sweep) < WT_DISK_SWEEP_INTERVAL_S:
            return
        # Repo-Roots dieser Session sammeln: aus den cwds gemeldeter Agenten (report.py
        # schreibt das Repo-Root als cwd) und aus vorhandenen worktree-Markern
        # ('<repo>.wt/<slug>' -> Root). Einmal gesehen -> bleibt fuer die Session gefegt,
        # damit auch nach dem Schliessen des letzten Agenten eines Repos noch aufgeraeumt
        # wird (bis dahin liegt das Repo-Root ohnehin schon vor).
        for st in states.values():
            root = st.get("cwd")
            if root:
                self._known_repos.add(os.path.normpath(root))
        for path in self._worktrees.values():
            root = wtc.repo_root_from_wt_dir(os.path.dirname(os.path.normpath(path)))
            if root:
                self._known_repos.add(root)
        if not self._known_repos:
            return
        # Lebende Slots = Terminals aller VERBUNDENEN Fenster (wie im Marker-Sweep).
        connected = {w for w in WINDOWS if self.broker.connected(w)}
        live = set()
        for w in connected:
            live.update(self.broker.terminals(w))
        # Besitz-Signale NUR lebender Slots: (1) exakter worktree-Marker-Pfad,
        # (2) Slug des zugewiesenen/gefundenen Tickets (= Ordnername). Beide Signale
        # werden fuer den Vergleich case-gefaltet (os.path.normcase): auf Windows ist
        # das Dateisystem case-insensitiv, und der Ordnername auf der Platte kann von
        # der (stets kleingeschriebenen) Slug-Schreibweise abweichen.
        owned_paths = {os.path.normcase(os.path.normpath(p))
                       for s, p in self._worktrees.items() if s in live and p}
        owned_slugs = set()
        for slot in live:
            ticket = self.tickets.get(slot) or self._found.get(slot)
            slug = _ticket_slug(ticket) if ticket else ""
            if slug:
                owned_slugs.add(os.path.normcase(slug))
        # Reload / kurzer Abriss: Repos, deren GEBUNDENES Fenster gerade getrennt ist,
        # NICHT fegen – das ueberlaesst der Sweep dem Fenster-Weg (_cleanup_closed_windows
        # bzw. Reconnect), sonst faellt ein worktree, waehrend die Sitzung nur neu laedt.
        # Welches Repo zu einem getrennten Fenster gehoert, kommt aus dem Slot (state-cwd
        # bzw. worktree-Marker eines Slots -> dessen Fenster = slot[0]) und ist damit
        # unabhaengig vom evtl. abweichenden VS-Code-Workspace-Namen; die Repo-Namens-
        # Bindung (disc_names) dient nur als Fallback, falls ein Fenster (noch) keinen
        # Slot-Zustand gemeldet hat.
        bound_disc = {w for w in WINDOWS if self.bindings.get(w) and w not in connected}
        disc_names = {(self.bindings.get(w) or "").lower() for w in bound_disc}
        skip_roots = set()

        def _note_disc_root(slot, root):
            if root and slot and slot[0] in bound_disc:
                skip_roots.add(os.path.normcase(os.path.normpath(root)))

        for slot, st in states.items():
            _note_disc_root(slot, st.get("cwd"))
        for slot, path in self._worktrees.items():
            _note_disc_root(slot, wtc.repo_root_from_wt_dir(
                os.path.dirname(os.path.normpath(path))))
        self._last_disk_sweep = now
        self._disk_sweep_busy = True
        threading.Thread(
            target=self._disk_sweep_bg,
            args=(now, sorted(self._known_repos), disc_names, skip_roots,
                  owned_paths, owned_slugs),
            daemon=True).start()

    def _disk_sweep_bg(self, now, repos, disc_names, skip_roots, owned_paths, owned_slugs):
        """bg-Thread des Disk-Sweeps: das blockierende git/fs + das Loeschen. Bekommt
        einen unveraenderlichen Snapshot des Deck-Zustands uebergeben; von der Deck-Seite
        fasst er NUR _wt_disk_gone_since an (der Tk-Thread startet keinen zweiten Sweep,
        solange _disk_sweep_busy True ist -> alleiniger Zugriff, keine Locks noetig).
        `skip_roots`/`disc_names` = Repos, deren gebundenes Fenster gerade getrennt ist
        (Reload) -> diesen Lauf ueberspringen; siehe _sweep_disk_worktrees."""
        try:
            seen = set()       # diesen Lauf als Kandidat gesehene Pfade (normcased)
            examined = set()    # diesen Lauf tatsaechlich durchsuchte '<repo>.wt'-Ordner (normcased)
            for root in repos:
                # Gebundenes Fenster dieses Repos gerade getrennt (Reload/kurzer Abriss)?
                # -> nicht wir; das Aufraeumen macht der Fenster-Weg. WICHTIG: uebersprungene
                # Repos NICHT in `examined` -> ihre Grace-Uhren bleiben unten erhalten
                # (nur pausiert, nicht zurueckgesetzt), sonst koennte ein dauernd flackerndes
                # Fenster die Grace ewig neu starten und ein echter Orphan fiele nie.
                if os.path.normcase(os.path.normpath(root)) in skip_roots \
                        or os.path.basename(root).lower() in disc_names:
                    continue
                wt_dir = wtc.wt_dir_for_repo(root)
                examined.add(os.path.normcase(os.path.normpath(wt_dir)))
                for d in wtc.list_child_dirs(wt_dir):
                    nd = os.path.normcase(os.path.normpath(d))
                    slug = os.path.normcase(os.path.basename(os.path.normpath(d)))
                    if nd in owned_paths or slug in owned_slugs:
                        self._wt_disk_gone_since.pop(nd, None)   # Agent lebt -> Uhr aus
                        continue
                    seen.add(nd)
                    t0 = self._wt_disk_gone_since.get(nd)
                    if t0 is None:
                        self._wt_disk_gone_since[nd] = now       # erstmals verwaist -> Uhr an
                    elif now - t0 >= WT_DISK_ORPHAN_GRACE_S:
                        if self._remove_orphan_worktree(d, root):
                            self._wt_disk_gone_since.pop(nd, None)
            # Grace-Uhren nur fuer Pfade tilgen, deren '<repo>.wt' diesen Lauf DURCHSUCHT
            # wurde und die dabei nicht (mehr) als Kandidat auftauchten (weg / wieder
            # besetzt). Pfade unter uebersprungenen (Reload-)Repos bleiben unangetastet.
            for nd in list(self._wt_disk_gone_since):
                if os.path.dirname(nd) in examined and nd not in seen:
                    self._wt_disk_gone_since.pop(nd, None)
        finally:
            self._disk_sweep_busy = False

    @staticmethod
    def _remove_orphan_worktree(path, repo):
        """Einen als verwaist erkannten worktree-Ordner ueber die sichere wtc-Maschinerie
        entfernen; True, wenn er weg ist. Waehlt den Weg nach Form:
          • verlinkter worktree (registriert ODER mit haengender '.git -> …/worktrees/…'-
            Datei) -> remove_worktree (git raeumt seine Verwaltung mit auf);
          • ein Rest GANZ OHNE .git -> die eng begrenzte remove_orphan_dir.
        Alles andere bleibt tabu: ein '.git'-VERZEICHNIS (echter Checkout/Clone) UND
        eine '.git'-DATEI, die NICHT auf einen worktree zeigt (Submodul -> …/modules/…,
        `git init --separate-git-dir`). Beide sind fremd und keine deck-worktrees; die
        Unterscheidung leistet is_linked_worktree, darum darf remove_orphan_dir nur den
        Fall 'kein .git vorhanden' bekommen."""
        try:
            if wtc.is_linked_worktree(path):
                return wtc.remove_worktree(path)
            if not os.path.exists(os.path.join(path, ".git")):
                return wtc.remove_orphan_dir(path, repo=repo)
            wtc.note("uebersprungen (fremdes .git, kein verwaister worktree): " + path)
        except Exception:
            pass
        return False
