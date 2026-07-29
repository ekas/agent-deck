"""Der Poll-Takt: Slot-Zustände lesen, Kacheln nachziehen, Bindungen pflegen.

Der „gesehen“-Merker muss über den Poll hinaus halten - in der State-Datei
steht weiterhin done, sonst leuchtet eine gelesene Antwort wieder auf.
"""
import time

from deck import i18n
from deck.claude import settings as cset
from deck.domain import config as cfg
from deck.domain import paths as dp
from deck.domain import slot_state as dc
from deck.domain import status_model as sm
from deck.domain.binding import is_placeholder_ws as _is_placeholder_ws
from deck.domain.binding import repo_from_title as _repo_from_title
from deck.ops import instance as si
from deck.platform import focus as wf
from deck.render.kit import CARD_BORDER
from deck.render.kit import CARD_FILL
from deck.render.kit import INK_3
from deck.render.kit import mix as _mix
from deck.render.kit import short_model as _short_model

from deck.ui.theme import AUTO_MAX_TRIES, AUTO_READY_GRACE, BLOOM_ON_CHANGE, GLOW_STYLE, LOST_FILL, LOST_GLOW, PENDING_AUTO_TTL, POLL_MS, SEL_BORDER, SLIDE_RETRY_MS, STALE_S, STALE_WINDOW_S, TICKET_AUTO_CARD, TICKET_AUTO_INK, TICKET_INK, TICKET_MAX_CHARS, WAIT_BORDER, WINDOWS, status_label


class RefreshMixin:
    """Wird in AgentDeck eingemischt (siehe panel.py)."""

    def refresh(self):
        """Poll-Schleife (alle POLL_MS): Zweitstart-Wunsch bedienen, Verbindungen
        synchronisieren, Layout bei Bedarf neu zeichnen, neuen Chat auto-fokussieren,
        dann Zustaende einlesen und jede Kachel aktualisieren. In benannte Schritte
        zerlegt (jeweils unten)."""
        self._beat()        # VOR jedem vorzeitigen return: sonst gilt ein langes
                            # Kachel-Ziehen dem Waechter als haengendes Panel
        if self._dragging():
            # Waehrend eines laufenden Kachel-Drags NICHT neu zeichnen (c.delete("all")
            # wuerde das Ziehen zerreissen). Poll pausiert kurz; _tile_release zeichnet
            # danach sauber neu. Timer aber weiterlaufen lassen.
            self.root.after(POLL_MS, self.refresh)
            return
        if self.dock is not None and self.dock.sliding():
            # Das Deck gleitet gerade an den Rand oder heraus. Diese Bewegung laeuft im
            # SELBEN Thread wie dieser Poll – und ein Durchlauf kostet gemessen ~7 ms,
            # mit kalten Zustandsdateien ueber 40. Das sind ein bis vier ausgefallene
            # Bilder mitten in einer nur ~270 ms kurzen Bewegung, also genau die Art
            # Ruckler, die man dem Rechner zuschreibt. Der Kachel-Animator wird aus
            # demselben Grund fuer die Dauer des Slides angehalten
            # (edge_dock._anim_hold); dieser Poll war der letzte Mitbewerber.
            #
            # Kurz nachfassen statt POLL_MS abzuwarten: die Bewegung ist gleich vorbei,
            # und danach soll die Anzeige unverzueglich stimmen. Der Leerlauf-Durchlauf
            # kostet nichts, und haengen kann das nicht – ein Slide endet garantiert
            # (Notbremse + Watchdog in edge_dock).
            self.root.after(SLIDE_RETRY_MS, self.refresh)
            return
        self._serve_reveal_request()    # ein zweiter Programmstart will uns sehen
        # Auf einen anders skalierten Monitor geschoben? Dann Oberflaechenfaktor
        # nachziehen (zeichnet selbst neu und passt die Fenstergroesse an).
        self._sync_ui_scale()
        self._sync_bindings()
        self._cleanup_closed_windows()
        sig = self._layout_sig()
        if sig != self._last_sig:                 # nur bei Aenderung neu zeichnen (Flackern)
            self._render_agents()
            self._last_sig = sig
        self._autofocus_new()
        self._mark_seen_read()          # in VS Code angeklickte Panes: 'ungelesen' -> 'idle'
        states = dc.read_all()
        live = dc.read_live()
        self._found = dc.read_found_tickets()   # vom Agenten gemeldete Ticket-IDs (Such-Modus)
        self._worktrees = dc.read_found_worktrees()  # gemeldete worktree-Pfade (Ticket-Anzeige + Orphan-Sweep haengen daran)
        now = time.time()
        cycle = getattr(cfg, "MODE_CYCLE", ["manual", "accept", "plan", "auto"])
        self._sweep_orphan_worktrees(now)       # (marker-getrieben) worktrees ohne lebenden Agenten abraeumen
        self._sweep_disk_worktrees(now, states) # (fs-getrieben) verwaiste '<repo>.wt'-worktrees OHNE Marker abraeumen
        self._adopt_hook_modes(states, cycle)
        self._apply_pending_auto(states, now, cycle)
        self._update_tiles(states, live, now, cycle)
        self._prefetch_summaries(now)   # Ticket-ID + Zusammenfassung im Hintergrund vorwaermen
        self.root.after(POLL_MS, self.refresh)

    def _beat(self):
        """Lebenszeichen fuer den Waechter (watchdog.py), gedrosselt auf BEAT_EVERY_S.

        Bewusst hier in der Poll-Schleife und nicht in einem eigenen Timer: der
        Herzschlag soll genau das bezeugen, was zaehlt – dass refresh() noch laeuft.
        Ein Panel, dessen Prozess lebt, dessen Schleife aber steht, ist fuer den
        Nutzer genauso tot wie ein abgestuerztes."""
        now = time.time()
        if now - self._last_beat < si.BEAT_EVERY_S:
            return
        self._last_beat = now
        si.beat()

    def _serve_reveal_request(self):
        """Den Wunsch eines zurueckgetretenen Zweitstarts bedienen: »zeig dich«.

        Ein erneuter Programmstart oeffnet absichtlich KEIN zweites Panel
        (single_instance) und holt stattdessen dieses hier nach vorn. Am Rand
        angedockt war davon nichts zu sehen: sichtbar ist dann nur der 12 px
        schmale Griff, fokussiert wurde also genau der – fuer den Nutzer sah der
        zweite Start damit aus wie »das Deck laesst sich nicht mehr oeffnen«.
        Also hier aufklappen (mit Haltefrist, der Zeiger steht ja nicht auf dem
        Deck); schwebend genuegt nach vorn holen."""
        if not si.take_reveal_request():
            return
        if self.dock is not None and self.dock.current_edge() != "off":
            self.dock.reveal_for_request()
            return
        try:                            # schwebend: evtl. minimiert/verdeckt
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def _mark_seen_read(self):
        """Panes, die du direkt in VS Code angeklickt hast, als gelesen markieren.
        Die Extension meldet solche Fokuswechsel als 'seen' an den Broker; hier holen
        wir die Slots ab und schalten 'ungelesen' (done) -> 'idle' – dieselbe Geste
        wie ein Klick auf die Deck-Kachel (siehe focus_slot). Nur done wird angefasst;
        ein denkender/laufender Agent bleibt unberuehrt. Vor read_all(), damit dieselbe
        Poll-Runde die Kachel schon grau statt gruen zeichnet."""
        seen = self.broker.drain_seen()
        if not seen:
            return
        states = dc.read_all()
        for slot in seen:
            st = states.get(slot)
            if st and st.get("status") == "done":
                dc.write_state(slot, "idle")

    def _sync_bindings(self):
        """Auto-Verknuepfung + Auto-Bind: eine gemerkte Extension bekommt ihren
        Buchstaben; jede verbundene Extension OHNE Buchstaben das naechste freie
        Fenster -> alles verbindet sich selbst."""
        for g, repo in self.bindings.items():
            if repo and not self.broker.connected(g):
                self.broker.assign(repo, g)
        bound = set(self.bindings.values())
        for ws in self.broker.workspaces():
            if _is_placeholder_ws(ws) or ws in bound:
                continue
            # Bevorzugt den Buchstaben, den die vorhandenen Terminals dieses Clients
            # schon tragen (Slot-Namen wie 'C1') -> der Buchstabe bleibt ueber
            # forget/neu-verbinden stabil und die Kacheln bleiben ansprechbar.
            slots = self.broker.workspace_slots(ws)
            pref = slots[0][0].upper() if (slots and slots[0]) else None
            free = pref if (pref in WINDOWS and not self.bindings.get(pref)) else \
                next((w for w in WINDOWS if not self.bindings.get(w)), None)
            if not free:
                break
            self.bindings[free] = ws
            bound.add(ws)
            self.store.save_bindings()
            self.broker.assign(ws, free)
            self._last_sig = None

    def _open_vscode_repos(self):
        """Repo-/Ordnernamen (lowercased) ALLER aktuell offenen VS-Code-Fenster, aus den
        Fenstertiteln gezogen. None, wenn die Win32-Enumeration fehlschlaegt -> der Aufrufer
        raeumt dann NICHT ab (lieber eine tote Kachel stehen lassen als eine lebende
        faelschlich abraeumen)."""
        try:
            titles = wf.list_titles(cfg.VSCODE_MARKER)
        except Exception:
            return None
        repos = set()
        for title in titles:
            repo = _repo_from_title(title)
            if repo and not _is_placeholder_ws(repo) and repo != cfg.VSCODE_MARKER:
                repos.add(repo.lower())
        return repos

    def _cleanup_closed_windows(self):
        """Ein gebundenes Fenster automatisch abraeumen (Bindung vergessen -> Kachel weg),
        sobald sein VS-Code-Fenster WIRKLICH geschlossen wurde. Abgrenzung zum blossen
        Reload/kurzen Verbindungsabriss: bei einem Reload bleibt das native VS-Code-Fenster
        offen, sein Titel (mit dem Repo-Namen) also sichtbar -> wir raeumen NUR ab, wenn KEIN
        offenes VS-Code-Fenster mehr zu diesem Repo existiert. Ein kurzer Grace
        (STALE_WINDOW_S) faengt den Socket-zu/HWND-noch-da-Moment und Titel-Aussetzer ab. Ein
        noch lebendes Fenster bindet sich wie gehabt automatisch neu (_sync_bindings)."""
        pending = [w for w in WINDOWS
                   if self.bindings.get(w) and not self.broker.connected(w)]
        if not pending:
            self._gone_since.clear()
            return
        open_repos = self._open_vscode_repos()
        if open_repos is None:
            return                                  # Enumeration nicht verfuegbar -> nicht abraeumen
        now = time.time()
        changed = False
        for w in pending:
            repo = self.bindings.get(w)
            if repo and repo.lower() in open_repos:
                self._gone_since.pop(w, None)       # Fenster lebt (z.B. Reload) -> Uhr ruecksetzen
                continue
            t0 = self._gone_since.get(w)
            if t0 is None:
                self._gone_since[w] = now           # erstmals als "weg" gesehen -> Uhr starten
            elif now - t0 >= STALE_WINDOW_S:
                # Fenster ist wirklich zu. Wurde es NICHT ueber die Deck-Knoepfe geschlossen
                # (Alt+F4/OS-X/Absturz), lief close_window nie -> hier dieselbe Slot-Aufraeumung
                # nachholen wie dort, sonst bleiben worktree + Marker/Ticket verwaist (und ein
                # spaeter denselben Slot-Namen erbender Agent koennte am alten Marker haengen).
                for slot in self._slots_for_window(w):
                    self._cleanup_worktrees(slot)
                    if self.tickets.pop(slot, None) is not None:
                        self.store.save_tickets()
                    self._clear_found_ticket(slot)
                    self._forget_slot(slot)
                del self.bindings[w]                # Bindung vergessen -> Kachel abraeumen
                self._gone_since.pop(w, None)
                if self.active_slot and self.active_slot[0] == w:
                    self.active_slot = None
                changed = True
        # Wieder verbundene Fenster aus der Uhr nehmen (Dict sauber halten).
        for w in list(self._gone_since):
            if w not in pending:
                self._gone_since.pop(w, None)
        if changed:
            self.store.save_bindings()
            self._last_sig = None                   # Layout sofort neu zeichnen

    def _autofocus_new(self):
        """Neuen "＋"-Chat automatisch auswaehlen + fokussieren, sobald sein Slot da ist.
        Das Vormerken fuer den Auto-Startmodus (_register_pending_auto: nur Dict-Eintrag +
        Datei-Lesen, KEIN Fokus) geschieht sofort bei Erst-Erkennung — moeglichst FRUEH, vor
        dem SessionStart-Report des Agenten, damit dessen baseline-ts stimmt. Das FOKUS-Holen
        dagegen NICHT waehrend eines modalen Dialogs: focus_slot holt per Win32 das VS-Code-
        Fenster nach vorn (SetForegroundWindow) und wuerde dem Button-Dialog mitten im Tippen
        den OS-Fokus klauen -> bei offenem Dialog Auto-Fokus auslassen (Vormerkung steht)."""
        if not self._await_new:
            return
        win, before, ts = self._await_new
        fresh_slots = [s for s in self.broker.terminals(win) if s not in before]
        if fresh_slots:
            self._register_pending_auto(fresh_slots)   # neue Agenten -> Auto-Startmodus (fokusfrei)
            self._await_new = None
            if not self._modal:
                self.focus_slot(fresh_slots[-1])   # der zuletzt angelegte
        elif not self._modal and time.time() - ts > 8:
            self._await_new = None             # nichts erschienen -> aufgeben

    def _register_pending_auto(self, slots):
        """Frisch per ＋ angelegte Slots fuer den Auto-Startmodus (config.NEW_AGENT_MODE)
        vormerken. Je Slot ein Fortschritts-Dict:
          • base_ts  = ts einer evtl. noch herumliegenden ALTEN Zustands-Datei (0, wenn
            keine da). _apply_pending_auto treibt erst bei einem NEUEREN Hook -> die alte
            Restdatei (Slot-Reuse) loest NICHT faelschlich aus, und der Wechsel greift auch,
            wenn die Vormerkung (z.B. bei offenem Dialog) erst NACH dem SessionStart-Report
            passiert (base ist die ALTE ts, nicht 'jetzt').
          • reg_ts   = jetzt, nur als Anker fuer PENDING_AUTO_TTL (Geduld ab Vormerkung).
          • ready_ts = 0; wird auf 'jetzt' gesetzt, sobald der erste frische Hook da ist
            (Anker fuer AUTO_READY_GRACE, damit die TUI-Eingabe erst warmlaeuft).
          • sent_ts  = 0; Zeitpunkt, zu dem wir zuletzt Shift+Tab geschickt haben (0 = noch
            nie). Trennt 'erst-antreiben' von 'bestaetigen/nachfassen'.
          • tries    = Anzahl bisheriger (Nach-)Antriebe (gedeckelt per AUTO_MAX_TRIES).
        Ohne gesetzten NEW_AGENT_MODE passiert nichts (Automatik aus)."""
        if not getattr(cfg, "NEW_AGENT_MODE", None):
            return
        # Sobald die globale settings.json einen Start-Permission-Modus vorgibt
        # (permissions.defaultMode – vom Einstellungs-Fenster gesetzt), startet jeder
        # frische claude nativ in diesem Modus. Die Shift+Tab-Automatik wuerde von
        # MODE_START aus NOCHMAL weiterschalten und ueberschiessen -> hier aus.
        try:
            if cset.read_values().get("mode"):
                return
        except Exception:
            pass
        now = time.time()
        for slot in slots:
            prev = dp.load_json(dp.state_path(slot), {}) or {}
            self._pending_auto[slot] = {
                "base_ts": prev.get("ts", 0), "reg_ts": now,
                "ready_ts": 0.0, "sent_ts": 0.0, "tries": 0,
            }

    def _apply_pending_auto(self, states, now, cycle):
        """Neu per ＋ erzeugte Agenten in den Wunsch-Startmodus (config.NEW_AGENT_MODE,
        z.B. 'auto') treiben, sobald ihr erster Hook feuert (mit SessionStart-Hook beim
        Oeffnen, sonst beim ersten Prompt) – NICHT feuern-und-vergessen, sondern:

          1) Readiness-Gate: nach dem ersten frischen Hook erst AUTO_READY_GRACE warten,
             DANN blind ab MODE_START die noetigen Shift+Tab schicken. Der SessionStart-Hook
             feuert sehr frueh, oft bevor die Claude-TUI die Back-Tab-Sequenz verarbeitet ->
             ohne die kurze Wartezeit gehen einzelne Taps verloren und der Agent 'bleibt auf
             dem Weg haengen' (accept/plan statt auto).
          2) Bestaetigen/Nachfassen: der Slot bleibt NACH dem Senden vorgemerkt. Meldet ein
             Hook NACH sent_ts einen echten Ist-Modus (rep_ts > sent_ts – so faellt der
             leere/vererbte SessionStart-Modus bewusst raus), gilt: im Ziel -> fertig; kurz
             gelandet -> vom gemeldeten Ist-Modus die Rest-Taps nachschicken (bis
             AUTO_MAX_TRIES). Ohne echtes Signal (Agent im Leerlauf) bleibt der Blind-Antrieb
             stehen, bis PENDING_AUTO_TTL abgelaufen ist.

        Nur fuer im Deck angelegte Slots; sobald das Ziel EINMAL bestaetigt (oder das
        Zeitfenster zu) ist, wird der Slot vergessen -> ein manueller Moduswechsel danach
        bleibt unangetastet."""
        if not self._pending_auto:
            return
        target = getattr(cfg, "NEW_AGENT_MODE", None)
        if not (target and target in cycle):
            self._pending_auto.clear()             # nichts Sinnvolles zu tun (Automatik faktisch aus)
            return
        tgt_idx = cycle.index(target)
        start = getattr(cfg, "MODE_START", "manual")
        start_idx = cycle.index(start) if start in cycle else 0
        for slot, p in list(self._pending_auto.items()):
            if now - p["reg_ts"] > PENDING_AUTO_TTL:
                del self._pending_auto[slot]       # Zeitfenster abgelaufen -> Automatik aufgeben
                continue
            st = states.get(slot)
            # Erst arbeiten, wenn ein FRISCHER Zustand NACH dem Anlegen kam (Claude lebt).
            if not (st and st.get("ts", 0) > p["base_ts"]):
                continue                            # noch kein neuer Hook -> weiter warten
            if not p["ready_ts"]:
                p["ready_ts"] = now                 # ersten frischen Hook gesehen -> Readiness-Uhr starten

            if not p["sent_ts"]:
                # ── Erst-Antrieb: TUI warmlaufen lassen, dann bewusst ab MODE_START rechnen.
                # (SessionStart meldet KEINEN Modus; ein doch vorhandener koennte von einem
                # gleichnamigen Vorgaenger vererbt sein -> nicht darauf verlassen.) Erst bei
                # ERFOLGREICHEM Senden sent_ts setzen; scheitert es (Verbindungsabriss), bleibt
                # sent_ts=0 -> der naechste Poll versucht es erneut.
                if now - p["ready_ts"] < AUTO_READY_GRACE:
                    continue                        # noch in der Warmlaufzeit
                if self._set_slot_mode(slot, target, cycle, current=start_idx):
                    p["sent_ts"] = now
                    p["tries"] += 1
                continue

            # ── Bestaetigen/Nachfassen: nur ein echtes Ist-Signal NACH unserem Senden zaehlt.
            rmode = st.get("mode")
            if not (rmode in cycle and st.get("ts", 0) > p["sent_ts"]):
                continue                            # (noch) keine neue Ist-Meldung -> geduldig warten
            if cycle.index(rmode) == tgt_idx:
                del self._pending_auto[slot]        # im Ziel angekommen -> fertig
            elif p["tries"] >= AUTO_MAX_TRIES:
                del self._pending_auto[slot]        # gibt auf (falscher MODE_CYCLE/Account?)
            elif self._set_slot_mode(slot, target, cycle, current=cycle.index(rmode)):
                p["sent_ts"] = now                  # kurz gelandet -> vom Ist-Modus nachtreiben
                p["tries"] += 1

    def _adopt_hook_modes(self, states, cycle):
        """Ist-Permission-Mode aus den Hooks uebernehmen (self-correcting): jeder
        neue Hook-Event (neuere ts) mit gueltigem Modus setzt die Deck-Annahme."""
        for slot, st in states.items():
            got = sm.adopt_hook_mode(self._mode_ts.get(slot, 0), st, cycle)
            if got:
                self.slot_mode[slot], self._mode_ts[slot] = got

    def _update_tiles(self, states, live, now, cycle):
        """Pro Kachel den Status interpretieren (status_model) und die Optik setzen:
        Glow-Ziele + Farbton-Ziel, betonte Kante, Modell/Effort/Status/Modus-Text.
        Das Faden/Atmen selbst macht der GlowAnimator (wir setzen nur die Ziele)."""
        skeys = []
        for slot, ids in self.tiles.items():
            st = states.get(slot)
            lv = live.get(slot) or {}
            status = st.get("status") if st else "idle"   # gerenderte Kachel = verbundener Agent -> hell
            fresh = sm.is_fresh(st, now, STALE_S)
            status = sm.normalize_status(status, fresh, GLOW_STYLE)
            lost = sm.is_lost(status, fresh, self.broker.connected(slot[0]))
            label = i18n.L("getrennt", "disconnected") if lost else status_label(status)
            # Status-Glow + Farbton der Karte (rot = im Panel berechneter Verlust).
            if lost:
                gcolor, gintensity, gpulse, gfill = LOST_GLOW, 1.0, False, LOST_FILL
            else:
                gcolor, gintensity, gpulse, gfill = GLOW_STYLE[status]
            ids["glow_color"] = gcolor
            ids["glow_intensity"] = gintensity
            ids["glow_pulse"] = gpulse
            ids["fill_target"] = _mix(CARD_FILL, gcolor, gfill)   # Ziel-Tönung der Fläche
            # Statuswechsel -> kurzes Aufleuchten (bloom); der Farbton fadet im Animator.
            skey = "lost" if lost else status
            skeys.append(skey)
            prev_skey = ids.get("status_key")
            if prev_skey != skey:
                ids["status_key"] = skey
                # KEIN Bloom, wenn eine "ungelesene" Antwort nur als gelesen quittiert
                # wird (done -> idle): das ist deine eigene Geste (Klick aufs Deck bzw.
                # Pane-Fokus in VS Code), kein neuer Agent-Zustand. Der Bloom (~1,25 s
                # Abkling) legt sich sonst additiv auf den Klick-Surge und laesst den Halo
                # ~1 s "ausrasten", obwohl die Kachel gerade RUHIGER wird. Das Klick-
                # Feedback traegt bereits surge() (0,4 s); nur echte Zustandswechsel leuchten.
                if not (prev_skey == "done" and skey == "idle"):
                    ids["bloom"] = BLOOM_ON_CHANGE
            # Kartenkante: Auswahl / Rückfrage / Verlust betont, sonst dezent getönt.
            if slot == self.active_slot:
                border, bw = SEL_BORDER, 2                # ausgewählte Kachel
            elif lost:
                border, bw = LOST_GLOW, 2                 # Verbindung weg fällt auf
            elif status == "waiting":
                border, bw = WAIT_BORDER, 2               # Rückfrage fällt auf
            else:
                border, bw = _mix(gcolor, CARD_BORDER, 0.5), 1
            # Karteninhalt: Modell (statusLine) + Effort (Hooks) + Status unten links.
            # Textfarben sind fest (INK/INK_2) – der Status läuft über den Glow.
            model = _short_model(lv.get("model"))
            live_eff = lv.get("effort") or (st or {}).get("effort") or ""
            effort = sm.resolve_effort(live_eff, self.slot_effort.get(slot))
            mi = self.slot_mode.get(slot)
            mode = cycle[mi] if (mi is not None and mi < len(cycle)) else ((st or {}).get("mode") or "")
            # Hover-Tooltip: letzte Frage (Fallback) + Transcript-Adresse der Session.
            ids["prompt"] = (st.get("prompt") if st else "") or ""
            ids["session_id"] = (st.get("session_id") if st else "") or ""
            ids["cwd"] = (st.get("cwd") if st else "") or ""

            # Bezugs-Zeile auf der Karte, zwei Quellen mit unterschiedlicher Verbindlichkeit:
            #  1) ZUGEWIESEN (manuell self.tickets, im Such-Modus gemeldet self._found) –
            #     nur mit worktree-Marker (state/<slot>.worktree), denn erst dann ist das
            #     Ticket wirklich an den Agenten gebunden. Volles Violett.
            #  2) ERKANNT: Ticket und/oder PR, die chat_summary aus dem Transcript gelesen
            #     hat (_auto_refs, vom Hintergrund-Job gefuellt). Kein worktree dahinter ->
            #     gedimmt, und nur solange keine zugewiesene ID die Zeile belegt.
            # Platz ist knapp (rechts steht das Effort): beides zusammen nur, wenn es in
            # TICKET_MAX_CHARS passt, sonst gewinnt das Ticket; ein zu langer Rest wird
            # abgeschnitten.
            tink = TICKET_INK
            if slot in self._worktrees:
                tid = self.tickets.get(slot) or self._found.get(slot, "")
            else:
                tid = ""
            if not tid and TICKET_AUTO_CARD:
                tid = self._refs_card_label(self._auto_refs.get(ids["session_id"]))
                tink = TICKET_AUTO_INK
            if len(tid) > TICKET_MAX_CHARS:
                tid = tid[:TICKET_MAX_CHARS - 1] + "…"
            # fill NICHT hier setzen – die Fläche fadet im Animator zur fill_target.
            # Die Kante geht durch den Animator: im Bildmodus wird sie mitgerendert
            # (weiche Rundung), im Fallback bleibt es das Polygon-Outline.
            self.anim.set_border(ids, border, bw)
            # Erst jetzt malen: Glow-Ziele UND Kante stehen: im Bildmodus wird die
            # Kachel damit in EINEM Durchgang fertig (statt zweimal je Poll).
            self.anim.apply_glow(slot, self.anim.pulse_factor())
            self.deck.itemconfig(ids["model"], text=model)
            self.deck.itemconfig(ids["effort"], text=(effort if effort else ""))
            # Mit Ticket -> die ID im Violett (zugewiesen) bzw. gedimmt (nur erkannt);
            # ohne Ticket bleibt die Zeile leer (Zuweisung laeuft ueber das Rechtsklick-
            # Menue -> kein Platzhalter).
            if tid:
                self.deck.itemconfig(ids["ticket"], text=tid, fill=tink)
            else:
                self.deck.itemconfig(ids["ticket"], text="", fill=INK_3)
            self.deck.itemconfig(ids["act"], text=label)   # unten links: nur der Status
            self.deck.itemconfig(ids["mode"], text=mode)
        self._update_dock_glow(skeys)

    def _update_dock_glow(self, skeys):
        """Den Griff-Balken des angedockten Decks in der Farbe des DRINGLICHSTEN
        Kachel-Status leuchten lassen (Rueckfrage > ungelesen > getrennt > denkt >
        idle): eingeklappt sieht man so am Rand, ob einer etwas von dir will.

        Die Farben bleiben hier (GLOW_STYLE/LOST_GLOW = eine Quelle fuer Kacheln UND
        Griff); das Dock bekommt nur Farbe/Intensitaet/Puls und kennt keine Status.
        Der Blitz beim Wechsel entscheidet sich ebenfalls hier, weil nur das Panel
        weiss, ob der neue Zustand dringlicher ist (sm.escalated) – 'ungelesen ->
        idle' ist deine Lese-Quittung und blitzt darum bewusst nicht."""
        if self.dock is None:
            return
        key = sm.dominant_status(skeys)
        prev = self._dock_key
        self._dock_key = key
        if key == "lost":
            color, intensity, pulse = LOST_GLOW, 1.0, False
        else:
            color, intensity, pulse, _fill = GLOW_STYLE[key]
        self.dock.set_glow(color, intensity, pulse,
                           flash=(prev is not None and sm.escalated(prev, key)))
