"""Ticket an einen Agenten geben; der legt sich selbst einen git worktree an.

Der Prompt MUSS einzeilig sein. Beim Schließen des Agenten räumt das Deck
genau diesen worktree wieder ab - mit Guard gegen fremde Slots.
"""
import os
import threading
import tkinter as tk

from deck import i18n
from deck.domain import config as cfg
from deck.domain import paths as dp
from deck.domain import slot_state as dc
from deck.domain.binding import jira_key as _jira_key
from deck.domain.binding import ticket_branch as _ticket_branch
from deck.domain.binding import ticket_slug as _ticket_slug
from deck.ops import worktree as wtc
from deck.platform import dpi
from deck.platform import monitor
from deck.render import kit as ck
from deck.render.kit import BG
from deck.render.kit import INK
from deck.render.kit import INK_2

from deck.ui.theme import WINDOWS, WT_DISK_ORPHAN_GRACE_S, WT_DISK_SWEEP_INTERVAL_S, WT_ORPHAN_GRACE_S


class TicketMixin:
    """Wird in AgentDeck eingemischt (siehe panel.py)."""

    def assign_ticket(self, slot):
        """Einem laufenden Agenten ein Ticket umhaengen und ihn anweisen, in einem eigenen
        git worktree fuer den Ticket-Branch zu arbeiten -> er kommt den anderen Agenten am
        selben Repo nicht in die Quere. Der Agent legt den worktree selbst an (Variante B),
        darum reicht send_text – keine Extension-Aenderung. Zwei Wege (Dialog):
          • "Zuweisen": du tippst die ID -> sofort auf der Karte, Prompt mit fixem Branch.
          • "Im Chat suchen": der Agent findet die ID im bisherigen Chat und schreibt sie
            in die Marker-Datei -> die Karte zeigt sie, sobald er sie gefunden hat."""
        if not self.broker.connected(slot[0]):
            return
        res = self._ticket_dialog(slot)
        if not res:
            return                              # Abbruch
        mode, ticket, task = res
        self.active_slot = slot                 # Auswahl auf den Ticket-Agenten
        # Hing an diesem Slot schon ein worktree (vorheriges Ticket)? Erst DEN abraeumen –
        # solange altes Ticket/alter Marker den Pfad noch verraten. Sonst bleibt der alte
        # worktree verwaist, wenn der Agent gleich fuer das neue Ticket einen anderen anlegt.
        self._cleanup_worktrees(slot)
        if mode == "search":
            # ID noch unbekannt -> keine manuelle Merkung; alten Wert/Marker fuer den Slot
            # raeumen, damit nichts Altes stehen bleibt, bis der Agent die neue ID meldet.
            if self.tickets.pop(slot, None) is not None:
                self.store.save_tickets()
            self._clear_found_ticket(slot)
            self._clear_worktree_marker(slot)   # der Agent legt gleich einen neuen an
            self.cmds.send_text(slot, self._ticket_search_prompt(slot, task), submit=True)
        else:                                   # "manual"
            self.tickets[slot] = ticket
            self.store.save_tickets()
            self._clear_found_ticket(slot)      # manueller Wert gewinnt -> alten Marker weg
            self._clear_worktree_marker(slot)   # der Agent legt gleich einen neuen an
            self.cmds.send_text(slot, self._ticket_prompt(slot, ticket, task), submit=True)

    def clear_ticket(self, slot):
        """Zugewiesenes/gemeldetes Ticket dieses Slots vergessen (nur Anzeige/Merkung;
        Agent und sein worktree bleiben unangetastet)."""
        if self.tickets.pop(slot, None) is not None:
            self.store.save_tickets()
        self._clear_found_ticket(slot)
        self._found.pop(slot, None)             # sofort aus der Anzeige (bis zum naechsten Poll)

    def _clear_found_ticket(self, slot):
        """Die Marker-Datei (state/<slot>.ticket) des Slots loeschen, falls vorhanden."""
        try:
            os.remove(dp.found_ticket_path(slot))
        except OSError:
            pass

    def _clear_worktree_marker(self, slot):
        """Die worktree-Marker-Datei (state/<slot>.worktree) des Slots loeschen."""
        try:
            os.remove(dp.worktree_marker_path(slot))
        except OSError:
            pass

    def _slots_for_window(self, win):
        """Alle Slots dieses Fensters, an denen (moeglicherweise) ein worktree haengt –
        aus Ticket-Merkung, gemeldeter ID und worktree-Marker zusammengetragen."""
        slots = set(self.tickets) | set(self._found) | set(dc.read_found_worktrees())
        return sorted(s for s in slots if s and s[0] == win)

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

    def _ticket_prompt(self, slot, ticket, task):
        """Den EINZEILIGEN Worktree-Prompt aus der config-Vorlage bauen. Der Task wird
        auf eine Zeile geglaettet – ein \\n wuerde per sendText(execute=True) im pty
        sofort absenden und den Prompt zerreissen. {wt_marker} = wohin der Agent den
        worktree-Pfad schreibt (Vorwaerts-Slashes -> shell-/tool-unabhaengig)."""
        branch = _ticket_branch(ticket) or ("ticket/" + str(ticket).strip())
        slug = _ticket_slug(ticket) or "ticket"
        prefix = self.settings.get("jira_prefix", getattr(cfg, "JIRA_PROJECT_KEY", ""))
        jira = _jira_key(ticket, project=prefix) or str(ticket).strip()   # nur Nummer -> <prefix>-<nr>
        wt_marker = dp.worktree_marker_path(slot).replace("\\", "/")
        task = " ".join(str(task or "").split()) or getattr(
            cfg, "TICKET_TASK_FALLBACK", "Then wait for my next instruction.")
        tmpl = getattr(cfg, "TICKET_PROMPT", "")
        try:
            return tmpl.format(ticket=ticket, jira_key=jira, branch=branch, slug=slug,
                               wt_marker=wt_marker, task=task)
        except (KeyError, IndexError, ValueError):
            # Kaputte Vorlage (unbekannter Platzhalter) -> sinnvoller Fallback statt Crash.
            return (f"Work on Jira ticket {jira} in a dedicated git worktree for the "
                    f"branch {branch} (git worktree add), work exclusively there, do "
                    f"not touch the main checkout, and write the absolute worktree path "
                    f"into the file {wt_marker}. Then look up ticket {jira} in Jira "
                    f"(Atlassian/Jira MCP) and give me a short summary. Task: {task}")

    def _ticket_search_prompt(self, slot, task):
        """EINZEILIGER Prompt fuer die 'Im Chat suchen'-Zuweisung: der Agent findet die
        ID selbst und schreibt sie in die Marker-Datei (Vorwaerts-Slashes -> shell-/
        tool-unabhaengig zuverlaessig). Das Deck kennt die ID vorher nicht."""
        marker = dp.found_ticket_path(slot).replace("\\", "/")
        wt_marker = dp.worktree_marker_path(slot).replace("\\", "/")
        prefix = getattr(cfg, "TICKET_BRANCH_PREFIX", "ticket/")
        task = " ".join(str(task or "").split()) or getattr(
            cfg, "TICKET_TASK_FALLBACK", "Then wait for my next instruction.")
        tmpl = getattr(cfg, "TICKET_SEARCH_PROMPT", "")
        try:
            return tmpl.format(prefix=prefix, marker=marker, wt_marker=wt_marker, task=task)
        except (KeyError, IndexError, ValueError):
            return (f"Find the ticket number in our previous chat, work in a dedicated "
                    f"git worktree for branch {prefix}<id> (git worktree add), write the "
                    f"found ID into the file {marker} and the absolute worktree path "
                    f"into the file {wt_marker}. Task: {task}")

    def _place_dialog(self, dlg):
        """Einen fertig aufgebauten, noch withdrawn Dialog neben das Panel legen und
        zeigen. Anker ist die obere linke Panel-Ecke; passt der Dialog rechts/unten
        nicht mehr auf den Monitor, klappt screen_fit ihn auf die andere Seite des
        Ankers – beim rechts angedockten Deck erscheint er also LINKS daneben statt
        halb jenseits des Bildschirmrands. Erst hier platzieren (nicht direkt nach
        dem Toplevel): vorher steht die Dialoggroesse noch nicht fest.

        Zweimal platziert, und zwar mit Absicht: die Hoehe der Titelleiste ist erst am
        SICHTBAREN Fenster messbar (monitor._frame_pad). Der zweite Aufruf aendert
        darum meist nichts – und rueckt den Dialog genau dann noch zurecht, wenn er
        ohne die Leiste knapp unter den Bildschirmrand geraten waere."""
        anchor = (self.root.winfo_rootx(), self.root.winfo_rooty())
        monitor.place(dlg, *anchor, dx=dpi.px(30), dy=dpi.px(60))
        try:
            dlg.deiconify()
        except tk.TclError:
            return
        monitor.place(dlg, *anchor, dx=dpi.px(30), dy=dpi.px(60))

    def _ticket_dialog(self, slot):
        """Kleiner, modaler Dialog mit EINEM Feld: die Ticketnummer. Gibt zurueck:
          • ("manual", ticket, "") – Nummer getippt und "Zuweisen"/Enter,
          • None                   – Abbruch / Escape / leer bestaetigt.
        Stil + modal-Pause wie der Button-Dialog (sonst klaut ein neu erscheinender
        Agent den Tastaturfokus)."""
        dlg = tk.Toplevel(self.root)
        dlg.title(i18n.L(f"Ticket für {slot}", f"Ticket for {slot}"))
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        try:
            dlg.attributes("-topmost", True)
        except tk.TclError:
            pass
        dlg.withdraw()      # erst aufbauen+platzieren, dann zeigen (siehe _place_dialog)
        result = {"val": None}
        tk.Label(dlg, text=i18n.L("Ticketnummer", "Ticket number"), bg=BG,
                 fg=INK_2, font=("Segoe UI", 9)).grid(
                     row=0, column=0, sticky="w", padx=12, pady=(12, 2))
        id_var = tk.StringVar(value=self.tickets.get(slot, ""))
        id_entry = tk.Entry(dlg, textvariable=id_var, bg="#20202a", fg=INK,
                            insertbackground=INK, relief="flat", font=("Segoe UI", 10),
                            width=20)
        id_entry.grid(row=1, column=0, sticky="we", padx=12)

        def save(*_):
            tid = id_var.get().strip()
            if tid:                              # leere Nummer -> stillschweigend verwerfen
                result["val"] = ("manual", tid, "")
            dlg.destroy()

        def cancel(*_):
            dlg.destroy()

        btns = tk.Frame(dlg, bg=BG)
        btns.grid(row=2, column=0, sticky="e", padx=12, pady=12)
        ck.btn(btns, i18n.L("Abbrechen", "Cancel"), cancel)
        ck.btn(btns, i18n.L("Zuweisen", "Assign"), save)
        dlg.bind("<Return>", save)      # Enter im Einzelfeld -> zuweisen
        dlg.bind("<Escape>", cancel)
        self._place_dialog(dlg)
        id_entry.focus_set()            # Fokus erst am sichtbaren Fenster
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        self._set_modal(True)
        try:
            self.root.wait_window(dlg)
        finally:
            self._set_modal(False)
        return result["val"]
