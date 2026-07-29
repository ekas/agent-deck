"""Agent Deck - Dashboard fuer Claude-Agents in VS-Code-Fenstern.

- Startet OHNE Agent-Kacheln. Pro verbundenem Fenster erscheint dynamisch eine
  Kachel je offenem Claude-Terminal + eine "＋"-Kachel fuer einen neuen Chat.
- Kachelfarbe = Live-Status (aus den Hook-Meldungen, state/<slot>.json)
- Klick auf "Fenster A/B" -> danach das VS-Code-Fenster anklicken = verbinden
  (Repo-Name wird gemerkt/angezeigt; nochmal klicken = neu verbinden)
- Klick auf Kachel -> Fenster nach vorn (Win32) + Pane fokussiert (Extension)
- Klick auf "＋" -> die Extension oeffnet ein weiteres Claude-Terminal
- Aktions-Buttons -> Kommandos an die Extension (kein Fokus-Klau)

Architektur: STATUS = Hooks -> State-Files (dieses Panel liest sie).
             ACTIONS/FOCUS = ueber den Broker an die VS-Code-Extension.
             Win32 nur noch, um das richtige der 2 Fenster nach vorn zu holen.

Start:  python agent_deck.py

Abhaengigkeiten: Stdlib. Optional Pillow – damit werden Kachelflaeche und Halo
gerendert statt als Canvas-Polygon gezeichnet (weiche Rundungen; Tk-Canvas kann
kein Antialiasing, siehe card_render.py). Fehlt Pillow, faellt das Deck
automatisch auf den bisherigen Polygon-Weg zurueck und laeuft normal weiter.
"""
import os
import sys
import time
import queue
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont

from deck.domain import slot_state as dc
from deck.ops import log
from deck.domain import paths as dp
from deck.ops import worktree as wtc
from deck.claude import summarize as cs
from deck.platform import focus as wf
from deck.ops import instance as si
from deck.domain import config as cfg
from deck.domain import status_model as sm
from deck.claude import settings as cset
from deck import i18n
from deck.ops import vscode_glow as rg
from deck.platform import dpi
from deck.platform import monitor
from deck.render import card as cr
from deck.render import kit as ck
from deck.render.kit import (BG, CARD_FILL, CARD_BORDER, INK, INK_2, INK_3,
                        hex_to_rgb as _hex_to_rgb, mix as _mix,
                        short_model as _short_model)
from deck.domain.binding import (BindStore, is_placeholder_ws as _is_placeholder_ws,
                       repo_from_title as _repo_from_title,
                       ticket_branch as _ticket_branch, ticket_slug as _ticket_slug,
                       jira_key as _jira_key)
from deck.net.broker import Broker
from deck.net.commands import BrokerCommands
from deck.render.glow import GlowAnimator, GLOW_RINGS
from deck.dock.controller import EdgeDock

# ── FROSTPANE-Palette (BG/CARD_FILL/INK…) lebt jetzt in canvas_kit ───────
# Aus der schwarzen Kiste wird eine dunkel getönte Scheibe; jede Karte glüht ruhig
# in ihrer Statusfarbe. Bewusst WEGGELASSEN (nach Vorgabe): der Leucht-Streifen an
# der linken Kante und der Status-Punkt in der Ecke. Die Grundfarben sind oben aus
# canvas_kit importiert; die Status-/Glow-Farben bleiben unten deck-spezifisch.

# Status -> Anzeigetext, je (deutsch, englisch) – i18n.L() waehlt beim Zeichnen die
# aktuelle Sprache (interne Namen aus Hooks/report.py bleiben unveraendert;
# running+thinking teilen sich "denkt"/"thinking", done = ungelesene Antwort).
STATUS_LABEL = {
    "idle": ("idle", "idle"), "done": ("ungelesen", "unread"),
    "thinking": ("denkt", "thinking"), "running": ("denkt", "thinking"),
    "waiting": ("Rückfrage", "needs input"), "none": ("—", "—"),
}


def status_label(status):
    """Lokalisierter Status-Anzeigetext (schaltet mit der Deck-Sprache)."""
    return i18n.L(*STATUS_LABEL.get(status, ("idle", "idle")))
# Status -> (Glow-Farbe, Glow-Intensität 0..1, atmet?, Füll-Tönung 0..1). Die Karte
# wird um die Füll-Tönung in ihre Statusfarbe eingefärbt (dunkles Glas), der Halo
# trägt den Status zusätzlich; "denkt"/"Rückfrage" pulsieren langsam, der Rest ruhig.
GLOW_STYLE = {
    "idle":     ("#8b8b99", 0.22, False, 0.06),  # grau  – fast neutral, "nichts zu tun"
    "done":     ("#6ee7a8", 0.85, False, 0.28),  # grün  – ungelesene Antwort
    "thinking": ("#7ecbff", 1.00, True,  0.28),  # cyan  – denkt (atmet)
    "running":  ("#7ecbff", 1.00, True,  0.28),  # cyan  – arbeitet (atmet)
    "waiting":  ("#ffc48a", 1.00, True,  0.30),  # amber – braucht dich (atmet)
    "none":     ("#8b8b99", 0.00, False, 0.00),  # kein Glow, kein Farbton
}
LOST_FILL = 0.30   # Füll-Tönung für "getrennt" (rot)
# Rot ist KEIN gemeldeter Agent-Status, sondern wird im Panel berechnet: die
# Extension des Fensters haengt nicht (mehr) am Broker -> Verbindung verloren.
# Anzeigetext lokalisiert (i18n.L) direkt am Zeichen-Aufruf, damit er mit der
# Deck-Sprache umschaltet.
LOST_GLOW  = "#ff6b6b"

# Border-Akzente: heben Auswahl / Rückfrage / Verlust zusätzlich zum Glow hervor.
SEL_BORDER  = "#ffffff"
WAIT_BORDER = "#ffc48a"
# Ticket-ID auf der Karte (Zeile zwischen Modell und Status): dezentes Violett –
# klar von den Statusfarben (cyan/grün/amber/rot) unterscheidbar.
TICKET_INK  = "#b7a6ff"
# Selbst aus dem Chat GELESENE Ticket-ID (nicht zugewiesen -> kein worktree dahinter):
# dasselbe Violett, aber gedimmt – so ist auf einen Blick klar, ob die ID nur erkannt
# oder wirklich an den Agenten gebunden ist.
TICKET_AUTO_INK = _mix(TICKET_INK, CARD_FILL, 0.45)
# So viele Zeichen passen in die Zeile, bevor sie dem Effort rechts ins Gehege kommt
# ("PROJ-2691 #62" = 13 – Ticket UND PR gehen sich damit gerade noch aus).
TICKET_MAX_CHARS = 14
# (Frueher stand hier ein "Ticket"-Platzhalter als Klick-Aufforderung auf der Karte.
# Ticket zuweisen laeuft jetzt ausschliesslich ueber das Rechtsklick-Menue -> ohne
# Ticket bleibt die Zeile leer; ein Platzhalter waere eine Button-Attrappe ohne Funktion.)

# Schiene links neben einem Repo-Block: der sichtbare Behaelter, der Kopf (Repo-Name)
# und Kachelreihe zu EINER Gruppe zusammenbindet. Vorher trug die Zuordnung allein der
# Abstand – und der war nach oben (fremde Reihe) genauso gross wie nach unten (eigene
# Reihe), die Gruppierung also reine Auslegungssache.
# Warum eine Schiene und KEINE zweite Farbdimension je Repo: Farbe ist im Deck bereits
# der Status (Halo aussen, leuchtend, atmend). Ein zweiter Farbcode daneben wuerde genau
# den Kanal verwaessern, der die eigentliche Arbeit macht.
# Ruhezustand: leise, aber sichtbar. CARD_BORDER allein (#33333d auf BG #121218) war im
# Render-Vergleich nicht mehr wahrnehmbar – eine Schiene, die man nicht sieht, bindet
# nichts. Ein Schritt in Richtung INK_3 reicht; lauter darf sie nicht werden, sonst
# konkurriert der Behaelter mit seinem Inhalt.
RAIL_IDLE = _mix(CARD_BORDER, INK_3, 0.3)
RAIL_HOT  = "#7ecbff"            # gehoverte Gruppe (dieselbe Aufmerksamkeitsfarbe wie
                                 # angehobene Kachel beim Ziehen und Tooltip-Saum)
RAIL_DIM  = _mix(CARD_BORDER, BG, 0.55)   # fremde Gruppe, waehrend woanders gehovert wird
# GLOW_RINGS (Ring-Fade) lebt jetzt im GlowAnimator; oben importiert, weil _draw_tile
# die Ringe anlegt.

# Statuswechsel-Effekt: refresh() setzt bloom = BLOOM_ON_CHANGE; das eigentliche
# Faden/Atmen/Abklingen (FILL_EASE/BLOOM_DECAY im ANIM_MS-Timer) macht jetzt der
# GlowAnimator. Poll-Takt + Stale-Grenze bleiben hier (refresh gehoert dem Panel);
# die Fallback-Werte greifen nur, falls ein config-Eintrag fehlt.
BLOOM_ON_CHANGE = getattr(cfg, "BLOOM_ON_CHANGE", 0.90)
POLL_MS         = getattr(cfg, "POLL_MS", 400)
# So kurz wird nachgefasst, wenn der Poll wegen einer laufenden Ein-/Ausklapp-Bewegung
# aussetzt (siehe refresh). Klein genug, dass die Anzeige direkt nach dem Aufklappen
# frisch ist, gross genug, dass das Nachfragen selbst keine Frames kostet.
SLIDE_RETRY_MS  = 50
STALE_S         = getattr(cfg, "STALE_S", 900)  # so lange ohne Update -> als "idle" zeigen
STALE_WINDOW_S  = getattr(cfg, "STALE_WINDOW_S", 3.0)  # getrennt+Fenster-zu so lange -> Bindung abraeumen
WT_ORPHAN_GRACE_S = getattr(cfg, "WT_ORPHAN_GRACE_S", 20.0)  # worktree-Marker ohne lebenden Agenten so lange -> abraeumen
WT_DISK_SWEEP_INTERVAL_S = getattr(cfg, "WT_DISK_SWEEP_INTERVAL_S", 60.0)  # so oft (s) die '<repo>.wt'-Ordner direkt auf verwaiste worktrees absuchen
WT_DISK_ORPHAN_GRACE_S = getattr(cfg, "WT_DISK_ORPHAN_GRACE_S", 90.0)  # so lange (s) muss ein '.wt'-worktree ohne zugehoerigen Agenten bestehen, bevor der Disk-Sweep ihn faellt
UI_PUMP_MS      = getattr(cfg, "UI_PUMP_MS", 80)     # Takt, in dem Thread-Ergebnisse abgeholt werden (siehe _post)
HOVER_TIP_MS    = getattr(cfg, "HOVER_TIP_MS", 250)  # Hover-Verzoegerung fuer den Tooltip
TIP_LEAVE_MS    = getattr(cfg, "TIP_LEAVE_MS", 80)   # verzoegertes Ausblenden (ueberbrueckt Tk-Leave+Enter zwischen Kachel-Items)
SUMMARY_ON      = getattr(cfg, "HOVER_SUMMARY", True)      # Hover -> KI-Kurzzusammenfassung statt letzter Frage
SUMMARY_MODEL   = getattr(cfg, "HOVER_SUMMARY_MODEL", "haiku")  # Modell fuer die Zusammenfassung
SUMMARY_PREFETCH = getattr(cfg, "HOVER_SUMMARY_PREFETCH", True)  # Zusammenfassungen vorab erzeugen (Hover sofort)
TICKET_AUTO     = getattr(cfg, "TICKET_AUTODETECT", True)        # Ticket-ID selbst aus dem Chat lesen
TICKET_AUTO_CARD = getattr(cfg, "TICKET_AUTODETECT_ON_CARD", True)  # … und auf der Karte zeigen
TICKET_PROJECT  = getattr(cfg, "JIRA_PROJECT_KEY", "")           # bevorzugtes Jira-Projekt bei der Erkennung
PREFETCH_EVERY_S = 5.0    # so oft (s) den Prefetch-Scan laufen lassen (nicht jeden Poll)
PENDING_AUTO_TTL = getattr(cfg, "PENDING_AUTO_TTL", 300)  # s: so lange auf den 1. Hook eines neuen Agenten warten, dann Auto-Startmodus aufgeben
AUTO_READY_GRACE = getattr(cfg, "AUTO_READY_GRACE", 1.5)  # s: nach dem 1. Hook warten, bevor der Auto-Startmodus getippt wird (TUI-Eingabe warmlaufen lassen)
AUTO_MAX_TRIES  = getattr(cfg, "AUTO_MAX_TRIES", 3)       # so oft den Auto-Startmodus (nach)treiben, dann aufgeben
WINDOWS = getattr(cfg, "WINDOWS", ["A", "B", "C", "D"])  # unterstuetzte Fenster


class AgentDeck:
    def __init__(self):
        self.active_slot = None
        self._await_new = None         # (win, slots-vorher, ts) – neuen "＋"-Chat auto-fokussieren
        self.slot_mode = {}            # Slot -> Permission-Mode-Index (Ist aus Hooks, sonst Annahme)
        self._mode_ts = {}             # Slot -> ts des zuletzt uebernommenen Hook-Modus
        self._pending_auto = {}        # Slot -> Fortschritts-Dict: neu per ＋ angelegt, wartet/treibt auf Auto-Startmodus (siehe _register_pending_auto)
        # Persistenz (bindings.json + slot_effort.json) liegt in BindStore; wir
        # halten die Dicts direkt und mutieren sie in place, danach store.save_*().
        self.store = BindStore()
        self.bindings = self.store.bindings       # {"A": repo, "B": repo}
        self.slot_effort = self.store.effort      # Slot -> gemerktes Effort ("xhigh"/"ultracode")
        self.tickets = self.store.tickets         # Slot -> manuell zugewiesene Ticket-ID
        self.order = self.store.order             # {win: [slot, …]} vom Nutzer gezogene Reihenfolge
        self._tile_drag = None                    # laufendes Kachel-Drag&Drop (sonst None)
        self._found = {}                          # Slot -> vom Agenten gemeldete ID (state/<slot>.ticket)
        self._worktrees = {}                      # Slot -> gemeldeter worktree-Pfad (state/<slot>.worktree); Ticket-Anzeige haengt daran
        self._wt_gone_since = {}                  # Slot -> ts, seit wann worktree-Marker ohne lebenden Agenten (Orphan-Grace)
        self._wt_disk_gone_since = {}             # worktree-Pfad(normcase) -> ts, seit wann als verwaister '.wt'-Ordner gesehen (Disk-Sweep-Grace)
        self._known_repos = set()                 # je in dieser Session gesehene Repo-Roots (aus cwd/Marker) -> deren '<repo>.wt' wird gefegt
        self._last_disk_sweep = 0.0               # ts des letzten Disk-Sweeps (Throttle auf WT_DISK_SWEEP_INTERVAL_S)
        self._disk_sweep_busy = False             # laeuft gerade ein Disk-Sweep-Thread? -> keinen zweiten parallel starten
        self.settings = self.store.settings       # Panel-Einstellungen (persistent)
        i18n.refresh()                             # Deck-Sprache aus settings.json (english/german) lesen
        self._modal = False            # True, solange der Ticket-/Einstellungs-Dialog offen ist (pausiert Auto-Fokus)
        self.binding_group = None      # "A"/"B" waehrend "klick-zum-Verbinden"
        self._bind_deadline = 0
        self._gone_since = {}          # Fenster -> ts, seit wann getrennt UND VS-Code-Fenster zu (Auto-Abraeumen)

        self.broker = Broker(cfg.BROKER_HOST, cfg.BROKER_PORT)
        self.broker.start()
        self.cmds = BrokerCommands(self.broker)   # typisierte Fassade fuers Senden

        # HiDPI: MUSS vor dem ersten Tk-Aufruf stehen. Ohne das zeichnet Tk in
        # logischen Pixeln und Windows streckt das fertige Fensterbild auf die
        # echte Aufloesung – dann ist alles weich und die runden Ecken treppen
        # (siehe dpi.py). Der Oberflaechenfaktor holt die Groesse zurueck:
        # gezeichnet wird ab jetzt in Geraetepixeln, aber um 1.5 groesser.
        # Vorlaeufig die System-Skalierung; sobald das Fenster existiert, zaehlt
        # die seines Monitors (_sync_ui_scale).
        dpi.enable()
        dpi.set_ui(dpi.system_factor())

        self.root = tk.Tk()
        # Punkt->Pixel-Umrechnung an den Faktor koppeln: daran haengen Dialoge,
        # Menues und alle Widgets mit Punkt-Schrift (die wachsen damit von selbst
        # mit). Der Canvas geht bewusst einen anderen Weg – Pixelschriften ueber
        # dpi.fontpx(), damit die Kachelschrift exakt dem Kachelraster folgt.
        dpi.sync_tk_scaling(self.root)
        self.root.title("Agent Deck")
        self._apply_icon()
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self._apply_transparency()
        self.tiles = {}          # {slot: record}; wird ge-clear()-t, NIE ersetzt
        # Je Repo-Block die beiden Items, die seine ZUGEHOERIGKEIT tragen: der Kopf
        # (Repo-Name) und die Schiene links daneben. {win: {"name","rail","connected"}}.
        # Wird beim Neuzeichnen geleert (die Item-IDs sterben mit dem delete('all')).
        self.win_items = {}
        self._hot_win = None     # Repo-Block, der gerade hervorgehoben ist (None = keiner)
        self.prompt_tip = ck.Tooltip(self.root)  # Hover-Kachel -> KI-Chat-Zusammenfassung
        # Hover-Zustand fuer den Tooltip (Shared-Tag-sicher, siehe _hover_enter):
        self._hover_slot = None   # Kachel, ueber der der Zeiger gerade ist (None = keine)
        self._tip_show_job = None # geplanter Show-Timer (Hover-Verzoegerung)
        self._tip_hide_job = None # geplanter Hide-Timer (verzoegertes Ausblenden)
        self._tip_visible = False # ist der Tooltip GERADE sichtbar? (fuer die async
                                  # Zusammenfassung: nach einem Klick NICHT wieder aufpoppen)
        # Rueckweg der Daemon-Threads auf den Tk-Thread (siehe _post/_ui_pump).
        # Absichtlich eine Queue und NICHT root.after(0, …) aus dem Thread.
        self._ui_q = queue.Queue()
        log.hook_tk(self.root)  # Tk-Callback-Fehler ins Log statt ins Leere
        self._summary_jobs = set()  # Sessions, deren Chat-Info gerade geholt wird
        self._last_prefetch = 0.0   # letzter Prefetch-Scan (gedrosselt, siehe _prefetch_summaries)
        self._last_beat = 0.0       # letztes Lebenszeichen fuer den Waechter (siehe _beat)
        # session_id -> im Chat erkannte Bezuege {"ticket": …, "pr": …} (leer = keine).
        # Vom Hintergrund-Job gefuellt, damit Tooltip UND Karte sie ohne Datei-I/O im
        # 400-ms-Poll haben.
        self._auto_refs = {}
        if SUMMARY_ON or TICKET_AUTO:
            cs.prune()            # alte Cache-Dateien laengst geschlossener Sessions weg
        # Alt+Tab ohne Mausbewegung feuert kein <Leave> -> beim App-Fokusverlust ausblenden.
        self.root.bind("<FocusOut>", self._on_focus_out)
        self._last_sig = None    # letztes gezeichnetes Agent-Layout (gegen Flackern)
        # Slim-Modus skaliert statt abzuschneiden: natuerliche (ungescalte) Inhaltsgroesse,
        # aktueller Fit-Faktor und ein Guard gegen Re-Entrancy beim Resize-Neuzeichnen.
        # nat = (0, 0) = "noch kein echter Render" -> die <=0-Guards lassen einen sehr
        # fruehen <Configure> (vor dem ersten Zeichnen) NICHT mit Riesenfaktor loslaufen.
        self._slim_nat = (0.0, 0.0)
        # Ruhefaktor = die Skalierung dieses Monitors: bei 150 % zeichnet das Deck
        # jede Design-Einheit 1.5 Pixel gross. Zieht der Nutzer das Fenster, weicht
        # der Faktor davon ab (Zoom) – der Bezug bleibt aber dpi.ui().
        self._slim_scale = dpi.ui()
        self._slim_relayout = False
        self.dock = None            # EdgeDock (Andocken/Auto-Hide) – erst nach dem Build
        self._dock_key = None       # zuletzt an den Griff-Balken gemeldeter Gesamtstatus
        self._build()
        self.root.update_idletasks()
        self.my_hwnd = wf.toplevel_hwnd(self.root.winfo_id())
        # Jetzt gibt es ein Fenster -> die Skalierung SEINES Monitors gilt (die drei
        # Schirme hier laufen auf 150/100/125 %). Noch ohne Neuzeichnen: das
        # _seed_slim_size weiter unten rendert ohnehin gleich frisch.
        self._sync_ui_scale(redraw=False)
        # Frostpane: die native Titelleiste BLEIBT, wird aber per Win11-DWM dunkel
        # + Cyan-Rand + runde Ecken (kein grauer Standard-Balken mehr).
        wf.style_titlebar(self.my_hwnd, dark=True, border="#7ecbff",
                          caption="#15151c", text="#cfd3dc", round_corners=True)
        # Groesse nur noch an der Ecke unten-rechts ziehbar (Seiten/obere Ecken tot);
        # das Bewegen an der Titelleiste und das programmatische _fit_slim_window
        # bleiben unberuehrt.
        wf.restrict_resize_to_corner(self.my_hwnd)
        # Animator teilt sich self.tiles und den Deck-Canvas mit dem Panel.
        self.anim = GlowAnimator(self.root, self.deck, self.tiles)
        # Es gibt nur noch die schlanke Ansicht: Fenster gleich auf die natuerliche
        # Inhaltsgroesse (Faktor 1.0) bringen; ab da skaliert _on_deck_configure bei
        # jedem Resize.
        self._seed_slim_size()
        self.root.update_idletasks()
        self.refresh()
        self._ui_pump()          # Ergebnisse der Hintergrund-Threads abholen (Queue)
        self.anim.start()        # schneller Timer: Farbton-Crossfade + Glow-Atmen
        self._glow_self_heal()   # Ring nach VS-Code-Update ggf. still neu einspielen
        # Am-Rand-andocken (Auto-Hide): gespeicherten Rand anwenden; ist einer gesetzt,
        # klappt das Deck sofort auf den Griff-Balken ein.
        self.dock = EdgeDock(self)
        self.dock.apply_initial()

    def _glow_self_heal(self):
        """Ist der Ring aktiviert (deck_settings 'glow'), aber der Patch fehlt (z.B.
        nach einem VS-Code-Update, das die workbench.html ersetzt hat), ihn im
        Hintergrund still neu einspielen. Best effort: Fehler (VS Code offen / keine
        Schreibrechte) werden geschluckt – der Nutzer kann im ⚙-Dialog manuell nachlegen.
        Laeuft in einem Daemon-Thread (Datei-I/O + Glob ueber die VS-Code-Ordner) und
        ruehrt bewusst kein tk an."""
        if not self.settings.get("glow"):
            return

        def work():
            try:
                installed, n = rg.status()
                if n and not installed:
                    rg.set_glow(True)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _set_modal(self, v):
        # Der Ticket-/Einstellungs-Dialog ruft das, solange er offen ist -> refresh()
        # pausiert den Auto-Fokus (sonst klaut ein neu erscheinender Agent dem Dialog
        # den Tastaturfokus). In finally IMMER wieder False.
        self._modal = v

    def _apply_icon(self):
        """Roboterkopf als Fenster-/Taskbar-Icon (assets/robot.ico, gezeichnet im
        Frost-/Cyan-Look; siehe assets/make_robot.py zum Neu-Generieren).

        Drei Schichten, jede fuer sich defensiv – ein fehlendes Asset darf das Deck
        NIE am Start hindern:
          • AppUserModelID: sonst gruppiert Windows uns unter python.exe und die
            Taskbar zeigt das Python-Feder-Icon statt unseres. Muss VOR dem ersten
            Sichtbarwerden gesetzt werden (hier direkt nach Tk()).
          • iconbitmap(default=…): setzt Titelleisten- UND Taskbar-Icon, und dank
            default= auch alle spaeteren Dialoge (Ticket-/Confirm-Fenster).
          • iconphoto: Fallback, falls iconbitmap scheitert. Die PhotoImage MUSS als
            Attribut ueberleben, sonst raeumt der GC sie weg und das Icon verschwindet.
        """
        base = dp.REPO_ROOT
        ico = os.path.join(base, "assets", "robot.ico")
        png = os.path.join(base, "assets", "robot_64.png")
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "agentdeck.panel")
        except Exception:
            pass
        try:
            if os.path.exists(ico):
                self.root.iconbitmap(default=ico)
        except tk.TclError:
            pass
        try:
            if os.path.exists(png):
                self._icon_img = tk.PhotoImage(file=png)   # Referenz halten!
                self.root.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass

    def _apply_transparency(self):
        """Windows: Hintergrund durchsichtig (transparentcolor = BG) und/oder ganzes
        Fenster halbtransparent (alpha). Faellt bei fehlender Unterstuetzung leise
        auf ein normales Fenster zurueck."""
        if getattr(cfg, "TRANSPARENT_BG", False):
            try:
                self.root.attributes("-transparentcolor", BG)
            except tk.TclError:
                pass
        try:
            alpha = float(getattr(cfg, "WINDOW_ALPHA", 1.0))
            if alpha < 1.0:
                self.root.attributes("-alpha", alpha)
        except (tk.TclError, TypeError, ValueError):
            pass

    # ── UI aufbauen ─────────────────────────────────────
    def _build(self):
        # Deck: pro verbundenem Fenster ein Block (kleiner Repo-Name als Kopf, darunter
        # die Agenten-Kacheln). Fuellt das Fenster und skaliert beim Resize.
        self.agent_area = tk.Frame(self.root, bg=BG)
        self.deck = tk.Canvas(self.agent_area, bg=BG, highlightthickness=0,
                              height=dpi.px(44))
        # Configure = Fenster/Canvas neu vermessen -> Deck passend skalieren.
        self.deck.bind("<Configure>", self._on_deck_configure)
        # Kachel-Drag&Drop: Motion/Release EINMAL fest am Canvas (nicht je Kachel neu),
        # damit kein Handler-Stapel entsteht und die Events auch kommen, wenn der Zeiger
        # die gezogene Kachel kurz verlaesst. Beide sind untaetig, solange _tile_drag None
        # ist. Der Press liegt als tag_bind auf jeder Kachel (siehe _draw_tile).
        self.deck.bind("<B1-Motion>", self._tile_motion)
        self.deck.bind("<ButtonRelease-1>", self._tile_release)

        # Untere Leiste: EIN durchgehender Streifen ueber die volle Breite (kein
        # freistehendes Chip-Paar mehr). Links die Claude-Nutzung (Session-Auslastung,
        # Hover = Rest), rechts das Zahnrad zu den Einstellungen. Bleibt dauerhaft am
        # unteren Rand sichtbar. Die Leiste ist selbst defensiv gebaut: ein fehlendes/
        # kaputtes Usage-Modul oder ein nicht laufendes Claude Desktop laesst nur die
        # linke Nutzungsanzeige weg -> das Deck startet trotzdem, das Zahnrad bleibt da.
        from deck.render.bottombar import BottomBar
        self.bottombar = BottomBar(
            self.root, self.root,
            on_settings=self._open_settings,
            show_usage=getattr(cfg, "SHOW_USAGE", True),
            poll_seconds=getattr(cfg, "USAGE_POLL_SECONDS", 120))
        # Packbares Widget fuer _apply_slim_layout (das Canvas IST die Leiste).
        self.bottom_bar = self.bottombar.canvas

        # Schrift des kleinen Fensternamens – EIN wiederverwendetes Font-Objekt (nicht je
        # Render neu), dessen Metriken die Zeilenhoehe/Breite liefern.
        self._slim_name_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")

        # Rahmen in fester Reihenfolge packen (Deck fuellt, Leiste bleibt unten).
        self._apply_slim_layout()

    # ── Layout (nur noch die schlanke Ansicht) ──────────
    def _apply_slim_layout(self):
        """Die Rahmen in fester Reihenfolge packen: das Deck (Agenten + kleiner Name)
        fuellt das Fenster (fill both/expand), die untere Leiste (Usage + Einstellungen)
        bleibt mit fester Hoehe am unteren Rand. Erst loesen, dann neu packen -> die
        Reihenfolge bleibt korrekt, auch wenn die Methode erneut aufgerufen wird."""
        for f in (self.agent_area, self.bottom_bar):
            f.pack_forget()
        self.deck.pack_forget()
        # Untere Leiste ZUERST mit side="bottom" -> sie reserviert den unteren Streifen;
        # das Deck bekommt danach die restliche Flaeche (fill both/expand) und <Configure>
        # feuert beim Resize (_on_deck_configure skaliert das Deck, statt abzuschneiden).
        # Ohne Rand (padx/pady=0) reicht die Leiste bis an die Fensterkanten -> ein
        # durchgehender Streifen statt eines eingerueckten Kastens.
        self.bottom_bar.pack(side="bottom", fill="x")
        self.agent_area.pack(side="top", padx=dpi.px(10),
                             pady=(dpi.px(6), dpi.px(4)), fill="both", expand=True)
        self.deck.pack(fill="both", expand=True)

    def _paint_once(self):
        """Deck einmal komplett neu zeichnen UND fuellen, ohne den Poll-Timer erneut zu
        schedulen (der laeuft weiter). Fuer sofortiges Neuzeichnen nach dem Moduswechsel."""
        self._render_agents()
        self._last_sig = self._layout_sig()
        self._repaint_tiles()

    def _repaint_tiles(self):
        """Zustaende einlesen und die AKTUELL gezeichneten Kacheln einfaerben/beschriften,
        ohne den Poll-Timer anzufassen. Nach jedem Neuzeichnen ausserhalb von refresh()
        noetig (Moduswechsel/Resize), damit die frischen Kacheln nicht bis zum naechsten
        Poll blank bleiben."""
        states = dc.read_all()
        live = dc.read_live()
        self._found = dc.read_found_tickets()
        cycle = getattr(cfg, "MODE_CYCLE", ["manual", "accept", "plan", "auto"])
        self._update_tiles(states, live, time.time(), cycle)

    def _sync_ui_scale(self, redraw=True):
        """Skalierung des Monitors unter dem Deck holen und die Oberflaeche darauf
        umstellen. Laeuft beim Start und in der Poll-Schleife – Tk kennt kein
        WM_DPICHANGED, ein zwischen 150-%- und 100-%-Monitor geschobenes Fenster
        muesste sich sonst selbst nicht anpassen und waere dort zu gross.

        Der vom Nutzer gezogene Zoom (Verhaeltnis Ist-Faktor zu Monitorfaktor)
        bleibt dabei erhalten – umgestellt wird nur die Basis. Rueckgabe: True,
        wenn sich wirklich etwas geaendert hat."""
        f = dpi.factor_for_window(getattr(self, "my_hwnd", 0))
        old = dpi.ui()
        if abs(f - old) < 0.01:
            return False
        dpi.set_ui(f)
        dpi.sync_tk_scaling(self.root)
        self._slim_scale = self._slim_scale * f / old if old else f
        for part in (getattr(self, "bottombar", None), getattr(self, "dock", None)):
            apply = getattr(part, "apply_ui_scale", None)
            if apply:
                apply()
        if redraw:
            self._render_agents()      # zeichnet neu UND passt die Fenstergroesse an
            self._repaint_tiles()
        return True

    def _seed_slim_size(self):
        """Beim Start das Fenster auf die natuerliche Inhaltsgroesse bringen –
        natuerlich heisst hier: eine Design-Einheit = dpi.ui() Geraetepixel, bei
        150 % also 1.5. Danach setzt die Fenstergroesse nur noch _fit_slim_window
        (bei Inhaltsaenderung); das reine Resize-Rendering (_on_deck_configure)
        fasst die Fenstergroesse bewusst nie an (sonst Resize-Loop / Kampf gegen die
        vom Nutzer gewaehlte Groesse)."""
        self._render_agents_slim(scale=dpi.ui())
        self._repaint_tiles()
        self._fit_slim_window(dpi.ui())

    def _fit_slim_window(self, scale):
        """Fenster + Canvas exakt auf den Inhalt bei <scale> setzen: rueckt beim Schliessen
        eines Agents/Fensters rechts+unten nach (und waechst beim Hinzufuegen) – oben links
        bleibt verankert. Explizites root.geometry, weil Tk ein vom Nutzer schon einmal
        angefasstes Fenster NICHT von allein verkleinert. +2 px Rundungsluft, damit der
        daraus folgende <Configure> denselben Faktor zurueckrechnet (kein Zitter-Redraw).
        Manuelles Ziehen laeuft NICHT hier durch (das setzt nie eine Fenstergroesse)."""
        self._slim_scale = scale
        nat_w, nat_h = self._slim_nat
        if nat_w <= 0 or nat_h <= 0:
            return
        self._slim_relayout = True
        try:
            self.deck.configure(width=int(round(nat_w * scale)) + 2,
                                height=int(round(nat_h * scale)) + 2)
            self.root.update_idletasks()      # requested size neu berechnen (inkl. Padding)
            self.root.geometry(
                f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")
        finally:
            self._slim_relayout = False
        # Angedockt + aufgeklappt: nach der Groessenaenderung wieder buendig an den
        # Rand ruecken (sonst liefe das gewachsene Fenster ueber die Kante hinaus).
        if self.dock is not None:
            self.dock.on_resized()

    def _slim_fit_scale(self):
        """Fit-Faktor aus aktueller Canvas-Flaeche / natuerlicher Groesse: min(Breite,
        Hoehe), damit NICHTS verdeckt wird – in beide Richtungen (auch > 1, Zoom). 2px
        Sicherheitsluft gegen Rundung der Font-Groessen. Vor der ersten Realisierung
        (Flaeche 1x1) faellt er auf 1.0 zurueck; harte Untergrenze nur als Crash-Schutz."""
        nat_w, nat_h = self._slim_nat
        aw, ah = self.deck.winfo_width(), self.deck.winfo_height()
        if aw <= 1 or ah <= 1 or nat_w <= 0 or nat_h <= 0:
            return 1.0
        return max(0.05, min((aw - 2) / nat_w, (ah - 2) / nat_h))

    def _on_deck_configure(self, ev):
        """Fenster/Canvas resized – nur im Slim-Modus relevant: Fit-Faktor neu berechnen
        und das Deck skaliert neu zeichnen. Guard gegen Re-Entrancy + eine kleine Schwelle
        gegen Zitter-Redraws. Im Slim-Modus setzt das Rendern KEINE Canvas-Groesse -> kein
        Resize-Loop."""
        if self._slim_relayout or self._dragging():
            return
        nat_w, nat_h = self._slim_nat
        if nat_w <= 0 or nat_h <= 0 or ev.width <= 1 or ev.height <= 1:
            return
        new_scale = max(0.05, min((ev.width - 2) / nat_w, (ev.height - 2) / nat_h))
        # Schwelle > Rundungsluft der +2/-2 in _fit_slim_window -> das durch dessen
        # geometry ausgeloeste <Configure> rechnet ~denselben Faktor und triggert hier
        # keinen erneuten Redraw (kein Zittern/Loop). Zugleich fein genug fuers Ziehen.
        if abs(new_scale - self._slim_scale) < 0.005:
            return
        self._slim_relayout = True
        try:
            self._render_agents_slim(scale=new_scale)
            self._repaint_tiles()
        finally:
            self._slim_relayout = False

    # ── Agent-Kacheln dynamisch ─────────────────────────
    def _ordered_slots(self, w):
        """Die Slots dieses Fensters in der vom Nutzer gewaehlten Reihenfolge (Drag&Drop).
        Basis ist die von der Extension gemeldete Liste (broker.terminals); die
        gespeicherte Reihenfolge (self.order) wird darueber gelegt, neue/unbekannte
        Slots haengen hinten in Melde-Reihenfolge an. So bestimmt allein das Deck die
        Anordnung – VS Code gibt die visuelle Pane-Reihenfolge nicht preis, also kann
        sie nicht gespiegelt, wohl aber hier frei getauscht werden."""
        live = self.broker.terminals(w)
        live_set = set(live)
        saved = [s for s in self.order.get(w, []) if s in live_set]
        seen = set(saved)
        return saved + [s for s in live if s not in seen]

    def _layout_sig(self):
        """Signatur des gewuenschten Layouts – nur bei Aenderung neu zeichnen. Nutzt die
        vom Nutzer gewaehlte Reihenfolge, damit ein Umsortieren einen Redraw ausloest."""
        return tuple(
            (w, self.bindings.get(w), self.broker.connected(w),
             tuple(self._ordered_slots(w)) if self.broker.connected(w) else ())
            for w in WINDOWS
        )

    def _render_agents(self):
        """Pro verbundenem Fenster ein Block: kleiner Repo-Name als Kopf, darunter die
        Agenten-Kacheln (die schlanke, skalierende Ansicht in _render_agents_slim).
        Inhalt aendert sich (Agent/Fenster zu ODER auf): AKTUELLEN Zoom halten und das
        Fenster an den neuen Inhalt anpassen -> rechte/untere Kante schliessen auf (statt
        den Rest in ein fixes Fenster hochzuskalieren). Manuelles Ziehen laeuft nicht hier,
        sondern ueber _on_deck_configure (das skaliert in ein fixes Fenster)."""
        self._render_agents_slim(scale=self._slim_scale)
        self._fit_slim_window(self._slim_scale)

    # Slim-Layout in DESIGN-Einheiten (Faktor 1.0). Beim Zeichnen wird alles mit dem
    # Fit-Faktor multipliziert -> beim Verkleinern wird alles kleiner statt abgeschnitten.
    _SLIM_W, _SLIM_H, _SLIM_GAP, _SLIM_R, _SLIM_X0 = 148, 52, 10, 12, 12
    _SLIM_ADD_W = 34            # Breite der Geister-＋-Klickflaeche am Reihenende (Design-Einheiten)
    # Vertikale Gliederung der Repo-Bloecke. Diese vier Zahlen sind das, was
    # Zugehoerigkeit ueberhaupt erst lesbar macht, darum stehen sie beisammen:
    # der Glow-Halo ragt RING (= len(GLOW_RINGS)*2) ueber die Kachel hinaus, die
    # SICHTBARE Luft ist also immer der Abstand MINUS RING. Frueher war die Luft
    # unter dem Kopf 4 und ueber dem naechsten Kopf 6 – der Repo-Name stand damit
    # praktisch mittig zwischen der fremden Reihe darueber und seiner eigenen
    # darunter, und die Gruppierung war Auslegungssache. Jetzt 3 gegen 16.
    _SLIM_TOP, _SLIM_BOT = 6, 6      # Rand oben/unten
    _SLIM_HEAD_GAP  = 3              # sichtbare Luft Kopf -> EIGENE Kachelreihe
    _SLIM_BLOCK_GAP = 16             # sichtbare Luft ZWISCHEN zwei Repo-Bloecken
    _SLIM_RAIL_X, _SLIM_RAIL_W = 2, 2   # Schiene links: Abstand vom Canvasrand, Breite.
                                        # Bleibt links vom Halo (der beginnt bei X0-RING = 6).

    def _slim_extent(self):
        """Natuerliche (ungescalte) Ausdehnung des Slim-Layouts in Design-Einheiten –
        Basis fuer den Fit-Faktor. Spiegelt exakt die y-/x-Schritte von _render_agents_slim
        bei Faktor 1.0 (Name-Zeile, Kachelreihe inkl. Glow-Halo, Platzhalter). Misst den
        Fensternamen bei Design-Groesse 12 (Font kurz darauf gestellt) – in PIXELN, damit
        die Messung im selben Raum wie die uebrigen Design-Einheiten liegt und nicht mit
        `tk scaling` (also der Monitor-Skalierung) mitwandert.

        ACHTUNG: die y-Schritte hier und in _render_agents_slim MUESSEN gleich bleiben –
        laufen sie auseinander, skaliert das Deck gegen eine falsche natuerliche Groesse
        (Inhalt abgeschnitten oder Fenster zu gross)."""
        W, H, GAP, R, X0 = self._SLIM_W, self._SLIM_H, self._SLIM_GAP, self._SLIM_R, self._SLIM_X0
        nf = self._slim_name_font
        nf.configure(size=dpi.fontpx(12)[1])
        RING = len(GLOW_RINGS) * 2
        name_h = nf.metrics("linespace")
        y, maxx = self._SLIM_TOP, X0 + W
        shown = [w for w in WINDOWS if self.bindings.get(w) or self.broker.connected(w)]
        for i, w in enumerate(shown):
            if i:
                y += self._SLIM_BLOCK_GAP        # Luft zum vorigen Block
            repo = self.bindings.get(w) or f"{i18n.L('Fenster', 'Window')} {w}"
            maxx = max(maxx, X0 + nf.measure(repo))
            y += name_h + RING + self._SLIM_HEAD_GAP
            if self.broker.connected(w):
                x = X0
                for _slot in self.broker.terminals(w):
                    x += W + GAP
                    maxx = max(maxx, x - GAP)
                maxx = max(maxx, x + self._SLIM_ADD_W)   # Platz fuer das Geister-＋ am Reihenende
                y += H + RING                    # Blockende = Unterkante des Halos
            else:
                y += name_h
        y += self._SLIM_BOT
        if not shown:
            y += 26
        return maxx + X0, max(y, 40)

    def _render_agents_slim(self, scale=None):
        """Slim-Modus: pro verbundenem Fenster nur ein KLEINER Name (kein ⟳/✕/Punkt)
        und darunter die Agenten-Kacheln – kein Button-Raster, keine ＋-Kachel. Die
        Kacheln sind dieselben wie im Vollmodus (_draw_tile), Klick/Glow/Tooltip also
        unveraendert. So bleibt 'wirklich nur die Agenten' uebrig.

        Alles wird mit `scale` gezeichnet (Koordinaten, Offsets UND Font-Groessen), damit
        beim Verkleinern des Fensters alles kleiner wird statt abgeschnitten zu werden
        (tkinters canvas.scale wuerde Fonts NICHT mitnehmen -> darum echtes Neuzeichnen).
        scale=None -> aus aktueller Canvas-Flaeche und natuerlicher Groesse berechnen. Im
        Slim-Modus wird BEWUSST keine Canvas-Groesse gesetzt (das macht nur _seed_slim_size)."""
        c = self.deck
        self._hide_prompt_tip()
        # Anim-Zustand der aktuell gezeichneten Kacheln merken, BEVOR alles neu
        # aufgebaut wird: ueberlebende Slots (Fenster/Agent bleibt) sollen ihren
        # Farbton/Glow BEHALTEN, damit ein einzelnes Auf-/Zugehen nicht ALLE Kacheln
        # neu "aufleuchten" laesst (kein Farb-Refade, kein Bloom-Blitz -> kein Reload-Look).
        prev_tiles = dict(self.tiles)
        c.delete("all")
        self.tiles.clear()
        self.win_items.clear()      # Kopf-/Schienen-Items sterben mit dem delete('all')
        self._hot_win = None
        # 1) natuerliche Groesse ermitteln (Design-Einheiten) -> merken fuer den Fit-Handler.
        nat_w, nat_h = self._slim_extent()
        self._slim_nat = (nat_w, nat_h)
        if scale is None:
            scale = self._slim_fit_scale()
        self._slim_scale = scale
        s = scale
        # 2) skaliert zeichnen.
        W, H, GAP, R, X0 = (self._SLIM_W * s, self._SLIM_H * s, self._SLIM_GAP * s,
                            self._SLIM_R * s, self._SLIM_X0 * s)
        nf = self._slim_name_font
        # Pixelschrift (negative Groesse): folgt exakt dem Kachelraster, statt
        # zusaetzlich ueber `tk scaling` mit der Monitor-Skalierung zu wandern.
        nf.configure(size=dpi.fontpx(12, s)[1])
        RING = len(GLOW_RINGS) * 2 * s
        name_h = nf.metrics("linespace")
        small_font = dpi.fontpx(8, s)
        rail_x, rail_w = self._SLIM_RAIL_X * s, self._SLIM_RAIL_W * s
        y = self._SLIM_TOP * s
        shown = [w for w in WINDOWS if self.bindings.get(w) or self.broker.connected(w)]
        for i, w in enumerate(shown):
            if i:
                y += self._SLIM_BLOCK_GAP * s      # Luft zum vorigen Block
            y_top = y                              # Blockanfang – die Schiene beginnt hier
            repo = self.bindings.get(w) or f"{i18n.L('Fenster', 'Window')} {w}"
            connected = self.broker.connected(w)
            # EIN Text-Item je Name: verbunden hell, sonst gedimmt. (Frueher zeichenweise
            # fuer den Kopf-Schimmer – der ist raus, siehe glow_animator.)
            name = c.create_text(X0, y, anchor="nw", text=repo, font=nf,
                                 fill=INK if connected else INK_3)
            # Knapp gehalten: der Kopf soll an SEINER Reihe kleben. Der Halo braucht RING,
            # darueber bleiben _SLIM_HEAD_GAP sichtbare Luft (siehe Konstanten).
            y += name_h + RING + self._SLIM_HEAD_GAP * s
            if connected:
                x = X0
                for slot in self._ordered_slots(w):
                    self._draw_tile(c, slot, x, y, W, H, R, scale=s, step=W + GAP)
                    x += W + GAP
                # Geister-＋ am Reihenende: einziger Startweg im Slim-Modus (bewusst
                # klein/blass statt volle ＋-Kachel wie im Vollmodus).
                self._draw_slim_add(c, w, x, y, H, s)
                y += H + RING
            else:
                c.create_text(X0, y, anchor="nw",
                              text=i18n.L("— nicht verbunden —", "— not connected —"),
                              fill="#52525b", font=small_font)
                y += name_h
            # Schiene ZULETZT: erst jetzt steht die Unterkante des Blocks fest. Sie ist
            # der eigentliche Behaelter – Kopf und Kachelreihe haengen sichtbar an
            # derselben Linie, statt nur ungefaehr beieinander zu stehen.
            rail = c.create_rectangle(rail_x, y_top, rail_x + rail_w, y,
                                      fill=RAIL_IDLE, outline="")
            self.win_items[w] = {"name": name, "rail": rail, "connected": connected}
        if not shown:
            c.create_text(X0, y, anchor="nw", width=220 * s, fill="#52525b",
                          font=small_font,
                          text=i18n.L("Warte auf VS-Code-Fenster …", "Waiting for VS Code window …"))
        self._carry_tile_anim(prev_tiles)   # ueberlebende Kacheln erben ihren Zustand -> kein Reload-Blitz
        if self.active_slot and self.active_slot not in self.tiles:
            self.active_slot = None

    # Felder, die eine ueberlebende Kachel beim Neuaufbau erbt, damit sie optisch
    # RUHIG bleibt: die aktuell gefadete Fuellfarbe (fill_rgb/fill_hex), die Glow-
    # Ziele und – entscheidend – status_key. Ohne uebernommenen status_key haelte
    # _update_tiles jede Kachel faelschlich fuer "Statuswechsel" und zuendete bei
    # jedem Redraw einen bloom-Blitz. surge/press-Jobs werden NICHT geerbt: die
    # neue Kachel startet sauber im Ruhezustand (Defaults aus _draw_tile). Ihre
    # Timer laufen aber noch und tragen den ALTEN Record in der Closure – sie
    # duerfen die frischen Items nicht mehr anfassen; dafuer sorgt
    # GlowAnimator._stale (sonst stand der Kachel-Text danach schief).
    # border/border_w gehoeren dazu, seit die Kante im Bildmodus MITGERENDERT wird:
    # ohne sie faellt eine ausgewaehlte Kachel beim Neuaufbau fuer einen Frame auf
    # die Ruhekante zurueck (sichtbares Blinzeln der weissen Auswahl-Kante).
    _CARRY_FIELDS = ("fill_hex", "fill_target", "glow_color", "glow_intensity",
                     "glow_pulse", "status_key", "bloom", "border", "border_w")

    def _carry_tile_anim(self, prev):
        """Anim-Zustand ueberlebender Slots aus <prev> in die frisch gezeichneten
        Kacheln uebernehmen. Ein Slot, den es vorher NICHT gab (frisch per ＋ oder
        neu verbundenes Fenster), fehlt in <prev> -> er faedt bewusst normal ein.
        Die geerbte Fuellfarbe wird sofort auf die Flaeche gesetzt (sonst blitzt ein
        Frame CARD_FILL auf, bevor der Animator wieder eingreift)."""
        anim = getattr(self, "anim", None)
        for slot, ids in self.tiles.items():
            old = prev.get(slot)
            if not old:
                continue                      # frischer Agent -> normal einfaden
            for k in self._CARRY_FIELDS:
                if k in old:
                    ids[k] = old[k]
            ids["fill_rgb"] = list(old.get("fill_rgb") or ids["fill_rgb"])  # eigene Liste
            if ids.get("rect"):               # Polygon-Fallback: Flaeche direkt faerben
                try:
                    self.deck.itemconfig(ids["rect"], fill=ids["fill_hex"])
                except tk.TclError:
                    pass
            if anim:
                # Im Bildmodus malt das die geerbte Flaeche gleich mit.
                anim.apply_glow(slot, anim.pulse_factor())

    def _draw_slim_add(self, c, win, x, y, H, s):
        """Slim-Weg zum Starten eines Agenten (Mockup-Option #2 »Geister-＋ am Reihenende«):
        ein blasses, schmales ＋ hinter der letzten Kachel der Reihe – KEINE volle ＋-Kachel
        wie im Vollmodus, damit der ruhige Slim-Look bleibt. Die gefuellte (BG = im Ruhe-
        zustand unsichtbare) Box ist die Klickflaeche; Hover hellt das ＋ auf und blendet
        einen gestrichelten Rahmen ein. Klick -> create_agent(win): die Extension oeffnet
        EIN weiteres Claude-Terminal in genau diesem Fenster (Autofokus wie bei der Voll-
        Kachel). x kommt bereits um eine GAP hinter der letzten Kachel herein."""
        bw = self._SLIM_ADD_W * s
        bh = min(H, 34 * s)
        by = y + (H - bh) / 2
        box = ck.round_rect(c, x, by, x + bw, by + bh, 11 * s,
                            fill=BG, outline="", width=max(1, int(round(s))), dash=(3, 3))
        # Zwei Striche statt des Zeichens "＋": als Text sass das Plus 3,5 px zu tief
        # im Kaestchen (tk zentriert die Zeilenbox, das Glyph sitzt auf der Mathe-
        # Achse – Begruendung in ck.plus). Die Masse sind dem alten Glyph abgemessen,
        # damit sich am Aussehen sonst nichts aendert: 16 pt bold ergab bei s=1.5
        # 16 px Spannweite. Der Strich dort war senkrecht 4 px, waagerecht 3 px
        # (Hinting); gewaehlt sind 3 px – mit 4 px in BEIDE Richtungen wirkt das
        # Kreuz fetter als das Zeichen, und das Geister-＋ soll blass bleiben.
        plus = ck.plus(c, x + bw / 2, by + bh / 2, 5.4 * s, 2.2 * s, fill=INK_3)
        tag = "slimadd_" + win
        ptag = tag + "_plus"          # trifft beide Striche mit einem itemconfig
        for it in (box, *plus):
            c.addtag_withtag(tag, it)
        for it in plus:
            c.addtag_withtag(ptag, it)
        c.tag_bind(tag, "<Button-1>", lambda e, g=win: self.create_agent(g))
        c.tag_bind(tag, "<Enter>", lambda e, b=box, p=ptag:
                   (c.itemconfig(p, fill=INK), c.itemconfig(b, outline=CARD_BORDER),
                    c.configure(cursor="hand2")))
        c.tag_bind(tag, "<Leave>", lambda e, b=box, p=ptag:
                   (c.itemconfig(p, fill=INK_3), c.itemconfig(b, outline=""),
                    c.configure(cursor="")))

    def _draw_tile(self, c, slot, x, y, W, H, R, scale=1.0, step=None):
        # Frostpane-Karte: dunkle Graphitfläche, heller Text, ruhiger Status-GLOW
        # (weicher Halo ringsum). BEWUSST WEGGELASSEN (nach Vorgabe): der Leucht-
        # Streifen an der linken Kante und der Status-Punkt in der Ecke – den Status
        # trägt allein der Glow (+ betonte Kante bei Auswahl/Rückfrage).
        # Layout: Modell (oben links) · ✕ (oben rechts) · Effort (Zeile 2 rechts)
        # · Status (unten links) · Modus (unten rechts).
        # scale != 1.0 nur im Slim-Modus: x/y/W/H/R kommen bereits skaliert herein, die
        # internen Text-Offsets, Font-Groessen und Halo-Masse werden hier mit demselben
        # Faktor mitskaliert -> die ganze Kachel wird kleiner statt abgeschnitten.
        s = scale
        # Kachelschrift in PIXELN (dpi.fontpx): sie folgt damit exakt demselben
        # Faktor wie die Koordinaten. Eine Punktangabe wuerde zusaetzlich ueber
        # `tk scaling` mit der Monitor-Skalierung wachsen -> doppelt, und der Text
        # liefe aus der Karte.
        fs = lambda b, w=None: dpi.fontpx(b, s, weight=w)
        # Flaeche + Halo + Kante: EIN gerendertes Bild (weiche Rundung, echter
        # Verlauf) – Tk-Canvas selbst kann kein Antialiasing, seine Rundungen
        # treppen. Ohne Pillow ODER bei durchsichtigem Fenster (dort wuerde der
        # Bild-Hintergrund mit ausgestanzt) bleibt es beim bisherigen Weg:
        # Polygon + drei Ring-Umrisse.
        rings, rect, img = [], None, None
        geom = None
        if cr.AVAILABLE and not getattr(cfg, "TRANSPARENT_BG", False):
            pad = cr.pad_for(s)
            geom = (max(1, int(round(W))), max(1, int(round(H))),
                    max(1, int(round(R))), pad)
            img = c.create_image(x - pad, y - pad, anchor="nw")
        else:
            for i in range(len(GLOW_RINGS)):
                d = (i + 1) * 2 * s
                rings.append(ck.round_rect(c, x - d, y - d, x + W + d, y + H + d,
                                           R + d, fill="", outline=BG, width=2 * s))
            rect = ck.round_rect(c, x, y, x + W, y + H, R,
                                 fill=CARD_FILL, outline=CARD_BORDER, width=1)
        model = c.create_text(x + 11 * s, y + 12 * s, anchor="w", text="—", fill=INK,
                              font=fs(10, "bold"))
        effort = c.create_text(x + W - 10 * s, y + 27 * s, anchor="e", text="", fill=INK_2,
                               font=fs(8))
        # Ticket-ID (Zeile 2 links, dem Effort gegenueber): zugewiesenes Ticket dieses
        # Slots – unter dem Modell, ueber dem Status. Farbe/Text setzt _update_tiles je
        # Poll; ohne Ticket bleibt die Zeile leer (der frueher hier stehende "Ticket"-
        # Platzhalter war eine Klick-Aufforderung – die Zuweisung laeuft jetzt nur noch
        # ueber das Rechtsklick-Menue, darum keine Button-Attrappe mehr auf der Karte).
        ticket = c.create_text(x + 11 * s, y + 27 * s, anchor="w",
                               text="", fill=INK_3,
                               font=fs(8, "bold"))
        act = c.create_text(x + 11 * s, y + H - 11 * s, anchor="w", text="idle", fill=INK_2,
                            font=fs(8))
        mode = c.create_text(x + W - 10 * s, y + H - 11 * s, anchor="e", text="", fill=INK_2,
                             font=fs(8))
        tag = "t_" + slot
        # Die Ticket-Zeile gehoert jetzt zur normalen Kachel (t_-Tag): Linksklick
        # fokussiert die Kachel wie ueberall, Rechtsklick oeffnet das Kachel-Menue.
        # Ticket zuweisen/aendern laeuft ausschliesslich ueber dieses Rechtsklick-
        # Menue (Untermenue "Ticket") – die frueher direkte Klickflaeche auf der
        # Zeile ist bewusst entfernt.
        # Klick-/Hover-Flaeche: im Bildmodus traegt das Bild den Tag (es IST die
        # Kachelflaeche), sonst das Polygon.
        for it in ((rect or img), model, effort, ticket, act, mode):
            c.addtag_withtag(tag, it)
        # Linksklick auf die Kachel: Klick ODER Ziehen. _tile_press merkt sich nur den
        # Start; ob es ein Klick (fokussieren) oder ein Drag (umsortieren) war, entscheidet
        # _tile_release anhand der Bewegung – die Motion/Release-Handler haengen EINMAL fest
        # am Canvas (siehe _build), damit die Events auch kommen, wenn der Zeiger die Kachel
        # beim Ziehen kurz verlaesst.
        c.tag_bind(tag, "<Button-1>", lambda e, s=slot: self._tile_press(s, e))
        # Rechtsklick irgendwo auf die Kachel -> Kachel-Menue (Ticket zuweisen/entfernen,
        # Agent schliessen).
        c.tag_bind(tag, "<Button-3>", lambda e, s=slot: self._card_menu(s, e))
        # Hover auf der Kachel -> nach kurzer Verzoegerung ein Tooltip mit einer KI-Kurz-
        # zusammenfassung des Chats (chat_summary; Session-Adresse in _update_tiles je Poll
        # aktualisiert, Erzeugung laeuft im Hintergrund). ACHTUNG:
        # der t_-Tag liegt auf mehreren gestapelten Items (rect + Textzeilen); Tk feuert
        # beim Wechsel zwischen ihnen Leave+Enter, OHNE dass die Kachel verlassen wird ->
        # _hover_enter/_hover_leave fangen das ab (Slot-Vergleich + verzoegertes Ausblenden),
        # sonst wuerde der Tooltip beim Bewegen ueber der Kachel flackern/nie erscheinen.
        c.tag_bind(tag, "<Enter>", lambda e, s=slot: self._hover_enter(s))
        c.tag_bind(tag, "<Leave>", lambda e: self._hover_leave())
        # Sichtbarer ✕-Button oben rechts: EIGENES Item/Tag, damit ein Klick darauf
        # NICHT zusaetzlich die Kachel fokussiert (das ✕ liegt ueber dem Rechteck und
        # traegt den t_-Tag NICHT). Klick -> Agent SOFORT schliessen (ohne Rueckfrage);
        # Hover faerbt rot. Feste Farbe (nicht im refresh()-Loop) -> Hover-Rot wird
        # nicht ueberschrieben. (Rechtsklick auf die Kachel zeigt weiter das Menue.)
        cls = c.create_text(x + W - 8 * s, y + 12 * s, anchor="e", text="✕", fill=INK_3,
                            font=fs(11, "bold"))
        xt = "x_" + slot
        c.addtag_withtag(xt, cls)
        c.tag_bind(xt, "<Button-1>", lambda e, s=slot: self.close_agent(s))
        c.tag_bind(xt, "<Enter>", lambda e, i=cls:
                   (c.itemconfig(i, fill=LOST_GLOW), c.configure(cursor="hand2")))
        c.tag_bind(xt, "<Leave>", lambda e, i=cls:
                   (c.itemconfig(i, fill=INK_3), c.configure(cursor="")))
        # Gruppen-Tag über ALLE Items der Kachel -> als Einheit skalierbar (Press & Pop).
        gtag = "g_" + slot
        for it in rings + [rect, img, model, effort, ticket, act, mode, cls]:
            if it is not None:
                c.addtag_withtag(gtag, it)
        # Anim-/Glow-State: Ziele setzt refresh(), gefadet wird im _anim_tick.
        # fill_rgb = aktuelle Füllfarbe (float, wird zur fill_target hin geeast);
        # bloom = kurzes Aufleuchten bei Statuswechsel; status_key erkennt den Wechsel.
        # surge/surge_job = laufender Glow-Surge (Klick-Feedback 02): Halo-Boost + Kanten-Blitz.
        # press_scale/-job = laufender Press&Pop-Zoom (Klick-Feedback), press_cx/cy = Zentrum.
        # img/geom/photo/img_key = der Bildweg (Flaeche+Halo+Kante als PhotoImage):
        # geom sind die Bildmasse in Pixeln, photo HAELT die Bildreferenz (der
        # Canvas tut das nicht – ohne sie verschwaende die Kachel, sobald der
        # Cache den Eintrag verdraengt), img_key merkt den zuletzt gemalten
        # Zustand, damit nicht jeder Frame ein identisches Bild neu setzt.
        # rect/rings sind im Bildmodus None bzw. leer (Polygon-Fallback).
        self.tiles[slot] = {"rect": rect, "model": model,
                            "effort": effort, "ticket": ticket,
                            "act": act, "mode": mode,
                            "img": img, "geom": geom, "photo": None,
                            "img_key": None,
                            "border": CARD_BORDER, "border_w": 1,
                            "rings": rings, "glow_color": INK_3,
                            "glow_intensity": 0.0, "glow_pulse": False,
                            "fill_rgb": list(_hex_to_rgb(CARD_FILL)),
                            "fill_hex": CARD_FILL, "fill_target": CARD_FILL,
                            "bloom": 0.0, "surge": 0.0, "surge_job": None,
                            "status_key": None,
                            "gtag": gtag, "press_scale": 1.0, "press_job": None,
                            "press_cx": 0.0, "press_cy": 0.0,
                            # Geometrie fuer das Drag&Drop-Umsortieren: linke obere Ecke,
                            # Breite/Hoehe und der horizontale Schritt (Kachel+Abstand) zur
                            # naechsten Kachel. win = Fensterbuchstabe (nur innerhalb des
                            # eigenen Fensters wird getauscht).
                            "x": x, "y": y, "w": W, "h": H,
                            "step": step if step else W, "win": slot[0],
                            # Hover-Tooltip-Daten (je Poll in _update_tiles aktualisiert):
                            # letzte Frage (Fallback bei HOVER_SUMMARY=False) + session_id/
                            # cwd, mit denen chat_summary das Transcript findet.
                            "prompt": "", "session_id": "", "cwd": ""}

    # ── Kachel-Umsortieren (Drag & Drop) ────────────────
    # VS Code gibt die visuelle Terminal-/Pane-Reihenfolge NICHT preis (kein Positions-/
    # Gruppen-API), also kann das Deck sie nicht spiegeln. Stattdessen ist das Deck die
    # Quelle der Wahrheit: die Kacheln lassen sich per Drag&Drop tauschen, die anderen
    # ruecken dabei zusammen und machen Platz (klassische Sortier-Animation). Die neue
    # Reihenfolge landet in self.order (persistiert via BindStore) und ueberlebt Neustarts.
    def _dragging(self):
        """True, sobald ein Kachel-Drag wirklich zieht (Bewegung ueber der Schwelle).
        Ein blosser Press ohne Bewegung zaehlt NICHT -> ein normaler Klick bleibt moeglich."""
        return bool(self._tile_drag and self._tile_drag.get("moved"))

    def _tile_press(self, slot, ev):
        """Maustaste auf einer Kachel gedrueckt: nur den Drag-Kandidaten merken (noch
        kein Drag). Bewegt sich der Zeiger nicht ueber die Schwelle, wertet _tile_release
        es als Klick -> focus_slot (Verhalten wie zuvor)."""
        self._tile_drag = {"slot": slot, "win": slot[0],
                           "sx": ev.x, "sy": ev.y, "moved": False}

    def _tile_motion(self, ev):
        d = self._tile_drag
        if not d:
            return
        if not d["moved"]:
            if abs(ev.x - d["sx"]) + abs(ev.y - d["sy"]) < 8:
                return                     # unter der Schwelle noch als Klick werten
            if not self._begin_tile_drag(d, ev):
                self._tile_drag = None     # nichts sinnvoll zu ziehen -> abbrechen
                return
        rec = self.tiles.get(d["slot"])
        if not rec:
            return
        # Gezogene Kachel folgt dem Zeiger – nur horizontal, damit sie in ihrer Reihe bleibt.
        self.deck.move(rec["gtag"], ev.x - d["lastx"], 0)
        d["lastx"] = ev.x
        self.deck.tag_raise(rec["gtag"])   # ueber den anderen Kacheln bleiben
        tgt = self._drag_target_index(d, ev.x)
        if tgt != d["target"]:
            d["target"] = tgt
            self._reflow_drag(d)           # Ziel-Positionen der anderen Kacheln neu setzen

    def _begin_tile_drag(self, d, ev):
        """Ersten echten Zug vorbereiten: Reihenfolge + Geometrie der Reihe erfassen,
        Kachel optisch anheben, den Sanft-Ease der Nachbarn starten. False, wenn es nichts
        zu ziehen gibt (Kachel inzwischen weg -> als Klick behandeln)."""
        win = d["win"]
        order = [s for s in self._ordered_slots(win) if s in self.tiles]
        if d["slot"] not in order:
            return False
        rec = self.tiles[d["slot"]]
        idx = order.index(d["slot"])
        d.update({
            "moved": True,
            "order": order,
            "from": idx,
            "target": idx,
            "x0": self.tiles[order[0]]["x"],
            "step": rec["step"] or rec["w"],
            "home_x": rec["x"],
            "begin_x": ev.x,
            "lastx": ev.x,
            "curx": {s: self.tiles[s]["x"] for s in order},
            "want": {},
            "job": None,
        })
        self._hide_prompt_tip()            # kein Tooltip waehrend des Ziehens
        try:
            self.deck.itemconfig(rec["rect"], outline="#7ecbff", width=2)  # angehoben
        except tk.TclError:
            pass
        self.deck.configure(cursor="hand2")
        self._drag_anim()
        return True

    def _drag_target_index(self, d, ev_x):
        """Aktuelle Zielposition (0..n-1) aus der Lage der gezogenen Kachel: ihre linke
        Kante relativ zum Reihenanfang, auf die naechste Spalte gerundet. Bezug auf die
        Kachel (nicht den blossen Zeiger) -> der Griff-Offset bleibt korrekt."""
        step = d["step"] or 1
        cur_left = d["home_x"] + (ev_x - d["begin_x"])
        raw = (cur_left - d["x0"]) / step
        return max(0, min(len(d["order"]) - 1, int(round(raw))))

    def _reflow_drag(self, d):
        """Ziel-x aller NICHT gezogenen Kacheln fuer die aktuelle Einfuege-Position
        berechnen: sie ruecken zusammen und lassen an d['target'] genau eine Luecke fuer
        die gezogene Kachel frei (die klassische 'Platz machen'-Anordnung)."""
        tgt = d["target"]
        want = {}
        p = 0
        for s in d["order"]:
            if s == d["slot"]:
                continue
            if p == tgt:
                p += 1                     # Luecke fuer die gezogene Kachel auslassen
            want[s] = d["x0"] + p * d["step"]
            p += 1
        d["want"] = want

    def _drag_anim(self):
        """Sanftes Nachziehen der Nachbarkacheln zu ihren Ziel-x (ease), im 16-ms-Takt,
        solange gezogen wird. Die gezogene Kachel selbst folgt in _tile_motion direkt dem
        Zeiger; hier bewegen sich nur die anderen, um Platz zu machen bzw. wieder zu
        schliessen, wenn man zuruueckzieht."""
        d = self._tile_drag
        if not d or not d.get("moved"):
            return
        c = self.deck
        try:
            for s, wx in d.get("want", {}).items():
                rec = self.tiles.get(s)
                if not rec:
                    continue
                cur = d["curx"].get(s, rec["x"])
                nx = wx if abs(wx - cur) < 0.5 else cur + (wx - cur) * 0.35
                if nx != cur:
                    c.move(rec["gtag"], nx - cur, 0)
                    d["curx"][s] = nx
        except tk.TclError:
            pass
        d["job"] = self.root.after(16, self._drag_anim)

    def _tile_release(self, ev):
        """Loslassen: war es ein Klick (keine Bewegung), fokussieren; war es ein Drag, die
        neue Reihenfolge festschreiben, speichern und die Reihe sauber einrasten (auch bei
        No-Op zurueck an den Start -> Kachel schnappt in ihr Raster)."""
        d = self._tile_drag
        self._tile_drag = None
        if not d:
            return                          # Release ohne Kachel-Press (z.B. auf ✕/Kopf)
        if d.get("job"):
            try:
                self.root.after_cancel(d["job"])
            except Exception:
                pass
        if not d.get("moved"):
            self.focus_slot(d["slot"])      # reiner Klick -> wie zuvor
            return
        self.deck.configure(cursor="")
        win = d["win"]
        order = [s for s in d["order"] if s != d["slot"]]
        tgt = max(0, min(d.get("target", d["from"]), len(order)))
        order.insert(tgt, d["slot"])
        if order != d["order"]:             # nur bei echter Aenderung speichern
            self.order[win] = order
            self.store.save_order()
        self._paint_once()                  # Kacheln in die (neue) Reihenfolge einrasten

    # ── Zugehoerigkeit beim Hover ───────────────────────
    def _highlight_group(self, win):
        """Den Repo-Block <win> hervorheben (None = alle in den Ruhezustand).

        Das ist die Antwort auf die eigentliche Frage beim Hovern: "zu welchem Repo
        gehoert die Karte unter dem Zeiger?". Statt sie im Tooltip zu BESCHREIBEN,
        antwortet die Gruppe selbst – ihre Schiene leuchtet auf, die fremden Bloecke
        treten zurueck. Angefasst werden nur Kopf und Schiene, NIE die Kacheln: deren
        Flaeche/Halo malt der GlowAnimator je Frame neu, ein Eingriff hier waere im
        naechsten Tick wieder ueberschrieben (und wuerde den Status-Kanal stoeren).

        Billig genug fuer jedes Enter (hoechstens len(WINDOWS) itemconfigs), und der
        _hot_win-Vergleich haelt den Wechsel zwischen Geschwisterkacheln kostenlos."""
        if win == self._hot_win:
            return
        self._hot_win = win
        for w, it in self.win_items.items():
            dim = win is not None and w != win
            hot = win is not None and w == win
            try:
                self.deck.itemconfig(
                    it["name"],
                    fill=INK_3 if (dim or not it["connected"]) else INK)
                self.deck.itemconfig(
                    it["rail"],
                    fill=RAIL_HOT if hot else (RAIL_DIM if dim else RAIL_IDLE))
            except tk.TclError:
                pass          # Items schon weg (Redraw dazwischen) -> nichts zu tun

    def _hover_enter(self, slot):
        """Zeiger betritt ein Item der Kachel. Wegen des geteilten t_-Tags feuert Tk das
        AUCH beim Wechsel zwischen den gestapelten Items DERSELBEN Kachel – dann ist
        slot == _hover_slot und wir tun nichts (kein Timer-Neustart, kein Flackern). Ein
        zuvor per Leave geplantes Ausblenden wird immer abgebrochen (wir sind ja noch
        drueber)."""
        if self._dragging():
            return                    # beim Umsortieren kein Frage-Tooltip aufpoppen
        self._cancel_tip_hide()
        if slot == self._hover_slot:
            return
        self._hover_slot = slot
        # SOFORT, nicht erst mit dem Tooltip nach HOVER_TIP_MS: die Zugehoerigkeit ist
        # die Frage, die man beim blossen Drueberfahren hat.
        self._highlight_group(slot[0])
        self._cancel_tip_show()
        self._tip_show_job = self.root.after(
            HOVER_TIP_MS, lambda s=slot: self._show_prompt_tip(s))

    def _hover_leave(self):
        """Zeiger verlaesst ein Item der Kachel. Feuert auch beim Wechsel auf ein Nachbar-
        Item DERSELBEN Kachel -> NICHT sofort ausblenden, sondern verzoegert: ein unmittel-
        bar folgendes _hover_enter derselben Kachel bricht das Ausblenden ab. Bleibt es aus
        (echtes Verlassen), verschwindet der Tooltip nach TIP_LEAVE_MS."""
        self._cancel_tip_hide()
        self._tip_hide_job = self.root.after(TIP_LEAVE_MS, self._do_hide_tip)

    def _do_hide_tip(self):
        """Verzoegertes Ausblenden faellig -> wir sind wirklich weg von der Kachel:
        geplanten Show abbrechen, Hover-Zustand loeschen, Tooltip verstecken."""
        self._tip_hide_job = None
        self._cancel_tip_show()
        self._hover_slot = None
        self._tip_visible = False
        self._highlight_group(None)
        self.prompt_tip.hide()

    def _cancel_tip_show(self):
        if self._tip_show_job is not None:
            try:
                self.root.after_cancel(self._tip_show_job)
            except Exception:
                pass
            self._tip_show_job = None

    def _cancel_tip_hide(self):
        if self._tip_hide_job is not None:
            try:
                self.root.after_cancel(self._tip_hide_job)
            except Exception:
                pass
            self._tip_hide_job = None

    def _hide_prompt_tip(self, *, keep_hover=False):
        """Tooltip hart ausblenden (Klick / Neu-Rendern / Fokusverlust): beide Timer weg,
        Tooltip versteckt. keep_hover=True behaelt _hover_slot -> ein durch die Klick-
        Animation (Skalieren der Kachel-Items) ausgeloestes erneutes Enter DERSELBEN Kachel
        wird von _hover_enter ignoriert, der Tooltip ploppt also NICHT ueber dem nach vorn
        geholten VS-Code-Fenster wieder auf. Sonst _hover_slot loeschen."""
        self._cancel_tip_show()
        self._cancel_tip_hide()
        if not keep_hover:
            self._hover_slot = None
        self._tip_visible = False
        # Auch bei keep_hover: die Hervorhebung ist ein Hinweis auf die Karte unter dem
        # Zeiger. Steht das Deck nicht mehr vorn (Klick auf eine Kachel holt VS Code
        # nach vorn), darf sie nicht als Rest stehenbleiben.
        self._highlight_group(None)
        self.prompt_tip.hide()

    def _on_focus_out(self, _e):
        """Ganze App hat den Fokus verloren (z.B. Alt+Tab OHNE Mausbewegung -> es feuert
        kein <Leave>): sonst bliebe ein sichtbarer Tooltip ueber dem neuen Fenster haengen.
        focus_displayof() ist None NUR, wenn der Fokus wirklich aus der App raus ist (nicht
        bei Fokuswechsel zwischen eigenen Widgets) -> kein Show/Hide-Flattern. keep_hover=
        True: der Zeiger steht bei Alt+Tab / beim Nach-vorn-Holen von VS Code (focus_slot)
        weiterhin PHYSISCH ueber der Kachel -> _hover_slot behalten, sonst wuerde die Klick-
        Animation den Tooltip ueber dem VS-Code-Fenster erneut aufpoppen lassen. Ein echtes
        <Leave> (Zeiger verlaesst die Kachel) raeumt _hover_slot ohnehin auf."""
        try:
            if self.root.focus_displayof() is None:
                self._hide_prompt_tip(keep_hover=True)
        except (tk.TclError, KeyError):
            pass

    def _tip_refs(self, sid):
        """Im Chat erkannte Bezuege dieser Session: {"ticket": …, "pr": …} (leer =
        keine/Erkennung aus). Erst aus dem In-Memory-Cache (vom Hintergrund-Job
        gefuellt), sonst EINMAL aus der Cache-Datei nachladen – die ueberlebt einen
        Deck-Neustart, der Hover zeigt die IDs also sofort und nicht erst nach dem
        naechsten Scan."""
        if not (TICKET_AUTO and sid):
            return {"ticket": "", "pr": ""}
        refs = self._auto_refs.get(sid)
        if refs is None:
            refs = cs.cached_refs(sid)
            self._auto_refs[sid] = refs
        return refs

    @staticmethod
    def _refs_label(refs):
        """Bezugs-Zeile fuer den Tooltip ("Ticket: ABC-1 · PR #62"); ohne beides ein
        leerer String."""
        parts = []
        if refs.get("ticket"):
            parts.append("Ticket: " + refs["ticket"])
        if refs.get("pr"):
            parts.append("PR #" + refs["pr"])
        return " · ".join(parts)

    @staticmethod
    def _refs_card_label(refs, max_chars=TICKET_MAX_CHARS):
        """Kompakte Fassung derselben Bezuege fuer die KARTE: "PROJ-2691 #62" – ohne das
        Wort 'Ticket' (die Zeile ist an ihrem Platz erkennbar) und nur, solange beides
        nebeneinander passt; sonst gewinnt das Ticket, weil es das Dauerhaftere ist."""
        refs = refs or {}
        tid, pr = refs.get("ticket") or "", refs.get("pr") or ""
        both = " ".join(p for p in (tid, ("#" + pr) if pr else "") if p)
        if len(both) <= max_chars:
            return both
        return tid or both[:max_chars - 1] + "…"

    def _origin_lines(self, slot):
        """Herkunft dieser Kachel als Tooltip-Kopf: "agent-deck · Fenster A · A2" und –
        wenn der Agent per Ticket in einem eigenen worktree sitzt – darunter "↳ wt/<slug>".

        Warum ueberhaupt: die Zusammenfassung sagt, WORUM es geht, aber nie WO. Bei
        mehreren offenen Repos ist genau das die Frage am Tooltip. Und der worktree-Fall
        ist ohne diese Zeile gar nicht sichtbar: der Blockkopf nennt das Repo, der Agent
        arbeitet aber in '<repo>.wt/<slug>' daneben.
        Ohne gebundenes Repo bleibt es beim Fensterbuchstaben (mehr wissen wir dann nicht)."""
        if not slot:
            return []
        win = slot[0]
        repo = self.bindings.get(win) or ""
        parts = [p for p in (repo, f"{i18n.L('Fenster', 'Window')} {win}", slot) if p]
        lines = [" · ".join(parts)]
        wt = self._worktrees.get(slot) or ""
        if wt:
            lines.append("↳ wt/" + os.path.basename(os.path.normpath(wt)))
        return lines

    def _tip_text(self, ids, sid, slot=""):
        """Text des Hover-Tooltips zusammenbauen: zuerst die Herkunft (Repo/Fenster/Slot,
        siehe _origin_lines), dann erkanntes Ticket / erkannter PR (was im
        Chat steht) und darunter die KI-Kurzzusammenfassung 'worum es geht' bzw. – solange
        die noch erzeugt wird – ein Platzhalter. Bei HOVER_SUMMARY=False bleibt es bei der
        bisherigen 'Letzten Frage', Herkunft und Bezugs-Zeile kommen trotzdem obendrueber.
        Leerer Rueckgabewert -> nichts zu zeigen."""
        lines = self._origin_lines(slot)
        head = self._refs_label(self._tip_refs(sid))
        if head:
            lines.append(head)
        if not SUMMARY_ON:
            text = ids.get("prompt") or ""
            if text:
                lines.append(i18n.L("Letzte Frage:\n", "Last question:\n") + text)
            return "\n".join(lines)
        summary = cs.cached_summary(sid) if sid else None
        if summary:
            lines.append(i18n.L("Worum es geht:\n", "What it's about:\n") + summary)
        elif sid:
            lines.append(i18n.L("Zusammenfassung wird erstellt …", "Generating summary …"))
        return "\n".join(lines)

    def _show_prompt_tip(self, slot):
        """Show-Timer abgelaufen -> Tooltip zeigen (Inhalt siehe _tip_text) und im
        Hintergrund Ticket/Zusammenfassung sicherstellen; was dabei neu dazukommt, zieht
        _chat_info_ready live nach. Nur, wenn die Kachel noch gehovert ist."""
        self._tip_show_job = None
        ids = self.tiles.get(slot)
        if not ids or self._hover_slot != slot:   # inzwischen weg / andere Kachel
            return
        sid = ids.get("session_id") or ""
        text = self._tip_text(ids, sid, slot)
        if text:
            self._tip_at_pointer(text)
        if sid:
            self._ensure_chat_info(sid, ids.get("cwd") or "")
        # Ohne sid (Agent verbunden, aber noch kein Hook) gibt es nichts zu HOLEN – die
        # Herkunftszeile steht trotzdem, die kennt das Deck aus sich selbst.

    def _tip_at_pointer(self, text):
        """Tooltip mit text leicht unter/rechts vom Mauszeiger zeigen. Zeiger-Koordinaten
        (winfo_pointer*) statt Canvas-Offset: gleiches Schirm-Koordinatensystem wie
        wm_geometry -> korrekt ueber mehrere Monitore und bei DPI-Skalierung.

        Der Versatz geht als dx/dy MIT (statt vorher aufaddiert): so kann der Tooltip am
        Bildschirmrand nach links/oben um den Zeiger klappen, statt teilweise jenseits
        des Monitors zu landen – und am rechts angedockten Deck ist genau das der
        Normalfall (siehe screen_fit)."""
        self.prompt_tip.show(self.root.winfo_pointerx(), self.root.winfo_pointery(),
                             text, dx=dpi.px(14), dy=dpi.px(18))
        self._tip_visible = True

    def _prefetch_summaries(self, now):
        """Chat-Infos offener Agenten schon VOR dem Hover holen -> der Hover ist dann
        sofort da (ein claude-Aufruf dauert ~8-13 s, fast nur CLI-Startup) und die
        erkannte Ticket-ID steht ohne Hover auf der Karte. Gedrosselt auf alle
        PREFETCH_EVERY_S, damit nicht jeder 400-ms-Poll Threads spawnt; die eigentliche
        Arbeit (und ob ueberhaupt neu erzeugt wird) entscheiden chat_summary.
        ensure_refs/generate (Cache- + Wachstums-/Cooldown-Gate, Concurrency-Cap).
        Der Ticket-Scan laeuft auch ohne HOVER_SUMMARY_PREFETCH (kostet nichts ausser
        dem Lesen des Transcripts)."""
        if not (TICKET_AUTO or (SUMMARY_ON and SUMMARY_PREFETCH)):
            return
        if now - self._last_prefetch < PREFETCH_EVERY_S:
            return
        self._last_prefetch = now
        for ids in self.tiles.values():
            sid = ids.get("session_id") or ""
            if sid and sid not in self._summary_jobs:
                self._ensure_chat_info(sid, ids.get("cwd") or "",
                                       summary=SUMMARY_ON and SUMMARY_PREFETCH)

    # ── Rueckweg vom Daemon-Thread auf den Tk-Thread ────
    def _post(self, fn):
        """Aus einem Hintergrund-Thread etwas auf dem Tk-Thread ausfuehren lassen.

        NICHT self.root.after(0, …) aus dem Thread benutzen, auch wenn es meistens
        gutgeht: after() ruft Tcl am Interpreter des Main-Threads auf, und tkinter
        haelt einen Fremdthread dabei nicht auf. Bei einem threaded Tcl-Build
        (tcl86t.dll) endet das irgendwann in einem Tcl_Panic – der Prozess ist dann
        SOFORT weg (abort(), kein Traceback, im Event-Log nur 0x80000003). Genau so
        ist das Panel am 2026-07-28 um 16:11 gestorben, zwei Sekunden nachdem ein
        Summary-Thread fertig wurde. Eine Queue ist der einzige gefahrlose Weg:
        put() ist thread-safe und faellt nicht ins Tcl."""
        self._ui_q.put(fn)

    def _ui_pump(self):
        """Tk-Thread: abarbeiten, was die Threads hinterlegt haben (eigener Takt,
        damit das nicht am refresh-Poll haengt, der beim Kachel-Drag pausiert).
        Ein Fehler in einem Callback darf die Pumpe nie anhalten."""
        while True:
            try:
                fn = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exc("_ui_pump")
        self.root.after(UI_PUMP_MS, self._ui_pump)

    def _ensure_chat_info(self, sid, cwd, summary=True):
        """Im Hintergrund Ticket-ID + Zusammenfassung dieser Session sicherstellen.
        Beides liest das Transcript (und generate() startet zusaetzlich claude) -> Daemon-
        Thread, NIE auf dem Tk-Thread. Pro Session laeuft hoechstens ein Job gleichzeitig.
        summary=False holt nur die Ticket-ID (Prefetch-Scan bei abgeschaltetem
        HOVER_SUMMARY_PREFETCH). Adressiert wird ueber die SESSION, nicht den Slot: bis
        das Ergebnis da ist, kann die Kachel laengst umsortiert oder weg sein."""
        if not sid or sid in self._summary_jobs:
            return
        self._summary_jobs.add(sid)
        threading.Thread(target=self._chat_info_worker,
                         args=(sid, cwd, summary), daemon=True).start()

    def _chat_info_worker(self, sid, cwd, summary=True):
        """Daemon-Thread: erst Ticket/PR (billig, reine Regex -> sofort nachziehen),
        dann die Zusammenfassung (teuer, claude). Fasst hier KEIN Tk an – der Rueckweg
        laeuft ueber _post (Queue), NICHT ueber root.after; siehe dort, warum."""
        if TICKET_AUTO:
            try:
                refs = cs.ensure_refs(sid, cwd, project=TICKET_PROJECT)
            except Exception:
                refs = None
            if refs is not None:                # Bezugs-Zeile sofort, ohne auf claude zu warten
                self._post(lambda: self._refs_ready(sid, refs))
        text = None
        if summary and SUMMARY_ON:
            try:
                text = cs.generate(sid, cwd, model=SUMMARY_MODEL, lang=i18n.current())
            except Exception:
                text = None
        self._post(lambda: self._chat_info_ready(sid, text))

    def _refs_ready(self, sid, refs):
        """Zurueck auf dem Tk-Thread: erkanntes Ticket/PR merken (die Karte liest sie im
        Poll von hier) und einen gerade sichtbaren Tooltip derselben Session nachziehen."""
        if self._auto_refs.get(sid) == refs:
            return
        self._auto_refs[sid] = refs
        self._refresh_tip_for(sid)

    def _chat_info_ready(self, sid, summary):
        """Zusammenfassung ist da -> Tooltip nachziehen (nur wenn er gerade sichtbar ist
        und noch dieselbe Session gehovert wird, siehe _refresh_tip_for)."""
        self._summary_jobs.discard(sid)
        if summary:
            self._refresh_tip_for(sid)

    def _refresh_tip_for(self, sid):
        """Sichtbaren Tooltip mit frischem Inhalt neu zeichnen, ABER nur, wenn er GERADE
        sichtbar ist und die gehoverte Kachel noch zu dieser Session gehoert. Der
        _tip_visible-Check ist wichtig: nach einem Klick haelt focus_slot _hover_slot
        (keep_hover), blendet den Tooltip aber aus -> ohne den Check poppte die spaet
        eintreffende Zusammenfassung ueber dem nach vorn geholten VS-Code-Fenster auf
        (dieselbe Falle wie beim Klick-Reentry, siehe _hover_enter)."""
        slot = self._hover_slot
        if not self._tip_visible or not slot:
            return
        ids = self.tiles.get(slot)
        if not ids or (ids.get("session_id") or "") != sid:
            return
        text = self._tip_text(ids, sid, slot)
        if text:
            self._tip_at_pointer(text)

    def _draw_add(self, c, win, x, y, W, H, R):
        rect = ck.round_rect(c, x, y, x + W, y + H, R,
                                fill="#191921", outline=CARD_BORDER, width=1)
        plus = c.create_text(x + W / 2, y + H / 2, text="＋", fill=INK_3,
                             font=("Segoe UI", 18, "bold"))
        tag = "add_" + win
        for it in (rect, plus):
            c.addtag_withtag(tag, it)
        c.tag_bind(tag, "<Button-1>", lambda e, g=win: self.create_agent(g))

    # ── Klick-zum-Verbinden ─────────────────────────────
    def start_bind(self, group):
        if self.binding_group == group:          # nochmal geklickt -> abbrechen
            self.binding_group = None
            return
        self.binding_group = group
        self._bind_deadline = time.time() + 20
        self.root.after(250, self._poll_bind)

    def _poll_bind(self):
        if not self.binding_group:
            return
        if time.time() > self._bind_deadline:
            self.binding_group = None
            return
        fg = wf.foreground_hwnd()
        title = wf.title_of(fg)
        if fg and int(fg) != int(self.my_hwnd) and cfg.VSCODE_MARKER in title:
            group = self.binding_group
            repo = _repo_from_title(title)
            # Fenster ohne Ordner hat als Titel nur den Marker ("Visual Studio Code")
            # -> kein Projektname; nicht binden (sonst Dauer-Phantom, das nie verbindet).
            if _is_placeholder_ws(repo) or repo == cfg.VSCODE_MARKER:
                self.binding_group = None
                return
            # Denselben Repo-Namen nicht an zwei Buchstaben haengen (sonst Doppel-
            # kachel): eine evtl. schon vorhandene Zuordnung dieses Repos loesen.
            if repo:
                for k in [k for k in list(self.bindings)
                          if k != group and (self.bindings[k] or "").lower() == repo.lower()]:
                    del self.bindings[k]
            self.bindings[group] = repo
            self.store.save_bindings()
            self.broker.assign(repo, group)
            self.binding_group = None
            self._last_sig = None    # Layout neu zeichnen -> neuer/geaenderter Block erscheint
            return
        self.root.after(250, self._poll_bind)

    def forget_window(self, win):
        """Bindung dieses Buchstabens vergessen (Kontextmenue per Rechtsklick auf den
        Namen). Entfernt Phantom-/Altkacheln – auch eine verbundene, aber bindungslose
        (der Extension wird gesagt, ihren Buchstaben zu vergessen; sonst taucht der
        Block ueber broker.connected() sofort wieder auf). Ein noch LEBENDES echtes
        Fenster bindet sich danach automatisch neu – so wird man es nicht dauerhaft
        los, was gewollt ist."""
        if self.binding_group == win:
            self.binding_group = None          # ein laufendes Verbinden mit abbrechen
        self.broker.forget(win)                # auch verbundene, bindungslose Kachel loesen
        if win in self.bindings:
            del self.bindings[win]
            self.store.save_bindings()
        self._last_sig = None                  # Layout sofort neu zeichnen
        # active_slot NICHT hart nullen: _render_agents raeumt es auf, sobald die
        # Kachel wirklich weg ist – bei einem lebenden Fenster bleibt die Auswahl.

    def _forget_menu(self, win, ev):
        """Rechtsklick-Kontextmenue am Fensternamen – macht die 'vergessen'-Geste
        auffindbar und dient zugleich als Bestaetigung (ein Klick zum Ausloesen)."""
        repo = self.bindings.get(win) or f"{i18n.L('Fenster', 'Window')} {win}"
        m = getattr(self, "_ctx_menu", None)
        if m is None:
            m = self._ctx_menu = tk.Menu(self.root, tearoff=0)  # EIN Menue, wiederverwendet
        m.delete(0, "end")
        m.add_command(label=i18n.L(f"„{repo}“ vergessen", f"Forget “{repo}”"),
                      command=lambda g=win: self.forget_window(g))
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _confirm_menu(self, header, action_label, action, *, x=None, y=None):
        """Ein wiederverwendbares Bestaetigungs-Kontextmenue fuer destruktive Aktionen.
        Erster Eintrag ist eine DEAKTIVIERTE Kopfzeile (liegt direkt unter dem Zeiger):
        so trifft der zweite Klick eines gewohnheitsmaessigen Doppelklicks auf den ✕-
        Buttons genau diese harmlose Zeile – die eigentliche Aktion steht darunter und
        wird nur durch einen bewussten zweiten Klick ausgeloest. Popup an der aktuellen
        Mausposition, wenn keine Koordinaten uebergeben werden (Ghost-Button liefert
        kein Event)."""
        m = getattr(self, "_ctx_menu", None)
        if m is None:
            m = self._ctx_menu = tk.Menu(self.root, tearoff=0)  # EIN Menue, wiederverwendet
        m.delete(0, "end")
        m.add_command(label=header, state="disabled")   # Doppelklick-Fang: nicht klickbar
        m.add_separator()
        m.add_command(label=action_label, command=action)
        if x is None or y is None:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _close_window_menu(self, win):
        """Bestaetigungsmenue zum Schliessen des ganzen VS-Code-Fensters (inkl. aller
        Agenten darin) – ausgeloest vom ✕ im Fensterkopf."""
        repo = self.bindings.get(win) or f"{i18n.L('Fenster', 'Window')} {win}"
        self._confirm_menu(i18n.L(f"„{repo}“ schließen?", f"Close “{repo}”?"),
                           i18n.L("Ja, VS-Code-Fenster schließen", "Yes, close the VS Code window"),
                           lambda g=win: self.close_window(g))

    def _card_menu(self, slot, ev):
        """Rechtsklick auf eine Agenten-Kachel: Model, Ticket, Effort und Mode dieses
        Agenten anpassen (je ein Untermenue) oder ihn schliessen. Ein bewusst per
        Rechtsklick gewaehlter Eintrag ist Absicht genug -> Schliessen hier direkt (der
        ✕-Doppelklick-Schutz lebt am ✕-Button)."""
        # Frage-Tooltip weg + geplanten Show abbrechen, sonst poppt er beim/nach dem
        # Rechtsklick ueber dem Menue auf. keep_hover=True: das erneute <Enter>, das beim
        # Schliessen des Menues auf derselben Kachel feuert, wird von _hover_enter ignoriert
        # -> der Tooltip kommt nicht gleich wieder (analog zum Links-Klick in focus_slot).
        self._hide_prompt_tip(keep_hover=True)
        # Frisches Menue je Aufruf (statt dem wiederverwendeten self._ctx_menu): es haengen
        # Untermenues (Model/Ticket/Effort/Mode) als eigene tk.Menu-Kinder dran; die liessen
        # sich in einem geteilten Menue nur umstaendlich neu auf-/abbauen. Referenz auf self
        # halten, damit Menue + Kinder nicht mitten im Popup vom GC eingesammelt werden.
        m = self._card_menu_ref = tk.Menu(self.root, tearoff=0)

        # ── Model: /model <wert> an den Agenten (statusLine zeigt danach das neue Modell)
        mm = tk.Menu(m, tearoff=0)
        for label, val in cset.MODEL_CHOICES:
            mm.add_command(label=label,
                           command=lambda s=slot, v=val: self._set_slot_model(s, v))
        m.add_cascade(label="Model", menu=mm)

        # ── Ticket: zuweisen/suchen (Dialog) bzw. entfernen – wie bisher, nur ins Menue
        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label=i18n.L("Ticket zuweisen …", "Assign ticket …"),
                       command=lambda s=slot: self.assign_ticket(s))
        cur = self.tickets.get(slot)
        if cur:
            tm.add_command(label=i18n.L(f"Ticket „{cur}“ entfernen", f"Remove ticket “{cur}”"),
                           command=lambda s=slot: self.clear_ticket(s))
        m.add_cascade(label="Ticket", menu=tm)

        # ── Effort: /effort <level> + Wert merken (nur so bleibt ultracode von xhigh
        #    unterscheidbar, die statusLine meldet fuer beide nur 'xhigh')
        em = tk.Menu(m, tearoff=0)
        for label in cset.EFFORT_CHOICES:
            em.add_command(label=label,
                           command=lambda s=slot, l=label: self._set_slot_effort(s, l))
        m.add_cascade(label="Effort", menu=em)

        # ── Mode: gezielt per Shift+Tab in den Ziel-Permission-Mode (siehe _set_slot_mode)
        cycle = getattr(cfg, "MODE_CYCLE", ["manual", "accept", "plan", "auto"])
        om = tk.Menu(m, tearoff=0)
        for md in cycle:
            om.add_command(label=md.capitalize(),
                           command=lambda s=slot, t=md, c=cycle: self._menu_set_mode(s, t, c))
        m.add_cascade(label="Mode", menu=om)

        m.add_separator()
        m.add_command(label=i18n.L(f"Agent {slot} schließen", f"Close agent {slot}"),
                      command=lambda s=slot: self.close_agent(s))
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _set_slot_model(self, slot, value):
        """Model dieses Slots umschalten: /model <value> an den Agenten schicken (die
        Extension fokussiert den Ziel-Pane vorher selbst -> landet im richtigen Chat).
        Die statusLine zeigt danach das neue Modell auf der Karte."""
        if not self.broker.connected(slot[0]):
            return
        self.active_slot = slot                 # Auswahl auf die angeklickte Kachel
        self.cmds.send_text(slot, "/model " + value)

    def _set_slot_effort(self, slot, label):
        """Reasoning-Effort dieses Slots setzen: /effort <level> schicken und den Wert
        merken. Das Merken ist noetig, weil die statusLine fuer xhigh UND ultracode nur
        'xhigh' meldet – nur mit dem gemerkten Wert bleibt die Karte korrekt (siehe
        status_model.resolve_effort). Level = Label kleingeschrieben ("Ultracode" ->
        "ultracode", "xhigh" -> "xhigh", …)."""
        if not self.broker.connected(slot[0]):
            return
        level = label.lower()
        self.active_slot = slot
        self.cmds.send_text(slot, "/effort " + level)
        self.slot_effort[slot] = level
        self.store.save_effort()

    def _menu_set_mode(self, slot, target, cycle):
        """Permission-Mode aus dem Kachel-Menue setzen: Auswahl auf die Kachel legen und
        gezielt in den Ziel-Modus schalten (_set_slot_mode schickt die noetigen Shift+Tab
        und merkt den neuen Modus)."""
        if not self.broker.connected(slot[0]):
            return
        self.active_slot = slot
        self._set_slot_mode(slot, target, cycle)

    # ── Status laufend aktualisieren ────────────────────
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

    # ── Aktionen ────────────────────────────────────────
    def _raise_window(self, win_key):
        """Das verknuepfte VS-Code-Fenster per Win32 nach vorn holen."""
        repo = self.bindings.get(win_key)
        if not repo:
            return False
        hwnd = wf.find_window(repo, cfg.VSCODE_MARKER)
        if not hwnd:
            return False
        wf.focus_window(hwnd)
        return True

    def focus_slot(self, slot):
        win = slot[0]
        if not self.bindings.get(win):
            return
        # Tooltip weg, aber _hover_slot BEHALTEN: das durch die Klick-Animation (Skalieren
        # der Kachel-Items) ausgeloeste erneute Enter derselben Kachel wird dann ignoriert
        # -> kein Tooltip ueber dem nach vorn geholten VS-Code-Fenster.
        self._hide_prompt_tip(keep_hover=True)
        self.active_slot = slot
        self.anim.press(slot)                # 01 'Press & Pop': taktiles Eindrücken/Zurückfedern
        # 02 'Glow Surge' BEWUSST ENTFERNT: das kurze Aufschwellen des Halos (bis ~2,4×)
        # beim Klick wirkte wie ein ~1 s "Ausrasten" des Glows. Nur das taktile Press & Pop
        # bleibt als Klick-Feedback. Die weiße Auswahl-Kante wird hier SOFORT gesetzt (reine
        # Kante, kein Halo-Effekt), damit die Selektion nicht erst beim nächsten Poll
        # (bis POLL_MS) sichtbar wird; _update_tiles bestätigt sie danach ohnehin.
        ids = self.tiles.get(slot)
        if ids and "rect" in ids:
            try:
                self.deck.itemconfig(ids["rect"], outline=SEL_BORDER, width=2)
            except tk.TclError:
                pass
        # "ungelesene" Antwort (gruen) als gelesen markieren, sobald du sie ansiehst
        st = dc.read_all().get(slot)
        if st and st.get("status") == "done":
            dc.write_state(slot, "idle")
        self._raise_window(win)              # verknuepftes VS-Code-Fenster nach vorn (Win32)
        self.cmds.focus_pane(slot)

    def _set_slot_mode(self, slot, target, cycle, current=None):
        """Permission-Mode eines BESTIMMTEN Slots gezielt setzen: so viele Shift+Tab vom
        angenommenen aktuellen bis zum Ziel schicken und die Annahme merken. `current` =
        angenommener Ist-Modus-Index; None -> gemerkter slot_mode (bzw. MODE_START, falls
        keiner). Die Mode-Buttons nutzen None (dem Chat folgen); der Auto-Startmodus
        uebergibt explizit den MODE_START-Index, um sich NICHT auf einen evtl. veralteten
        slot_mode zu verlassen. Gibt True zurueck, wenn der Modus als gesetzt gilt (nichts
        zu tun ODER Senden erfolgreich), False nur bei fehlgeschlagenem Senden -> dann NICHT
        gemerkt, der Aufrufer kann erneut versuchen."""
        start = getattr(cfg, "MODE_START", "manual")
        remembered = current if current is not None else self.slot_mode.get(slot)
        got = sm.mode_steps(remembered, target, cycle, start)
        if got is None:
            return False                 # unbekannter Modus -> MODE_CYCLE in config.py pruefen
        steps, tgt = got
        if steps == 0:
            self.slot_mode[slot] = tgt   # vermutlich schon im Ziel-Modus
            return True
        if self.cmds.send_key(slot, "shift-tab", steps):
            self.slot_mode[slot] = tgt
            return True
        return False

    def create_agent(self, win):
        """＋-Kachel: die Extension oeffnet EIN weiteres Claude-Terminal.
        Der neu erscheinende Slot wird automatisch fokussiert (Deck + VS Code).

        Das Wunsch-Modell wird als `claude --model <wert>` beim Start ERZWUNGEN
        (CLI-Flag = hoechste Prioritaet). Der settings.json-'model' waere der
        schwaechste Hebel (User-Scope) und wuerde vom zuletzt per /model gewaehlten,
        in ~/.claude.json gemerkten Modell ueberstimmt -> genau das "zuletzt
        verwendete Modell statt des eingestellten". Quelle ist die deck-eigene
        Einstellung (deck_settings.json), da settings.json das '[1m]'-Suffix verwirft."""
        if not self.broker.connected(win):
            return
        model = self.settings.get("model") or cset.MODEL_CHOICES[0][1]
        if self.cmds.create_agent(win, model):
            # Ausgangsbestand merken -> neu hinzugekommenen Slot in refresh() auto-fokussieren.
            self._await_new = (win, set(self.broker.terminals(win)), time.time())

    def reload_window(self, win):
        """Loest 'Developer: Reload Window' im VS-Code-Fenster dieses Buchstabens aus."""
        if not self.broker.connected(win):
            return
        self.cmds.reload(win)

    def close_agent(self, slot):
        """Einen einzelnen Agenten schliessen: die Extension beendet dessen Terminal
        (und damit die Claude-Session). Ihr onDidCloseTerminal meldet die neue
        Terminalliste zurueck -> die Kachel verschwindet beim naechsten refresh()."""
        win = slot[0]
        if not self.broker.connected(win):
            return
        if self.cmds.close_agent(slot):
            self._cleanup_worktrees(slot)    # hing ein git worktree am Agenten -> loeschen
            if self.tickets.pop(slot, None) is not None:
                self.store.save_tickets()    # zugewiesenes Ticket mit dem Agenten vergessen
            self._clear_found_ticket(slot)   # auch die gemeldete ID (Marker-Datei) weg
            self._forget_slot(slot)          # gemerkten Modus/State tilgen (Slot-Name wird recycelt)
            if self.active_slot == slot:
                self.active_slot = None      # Auswahl auf die verschwindende Kachel loesen

    def _forget_slot(self, slot):
        """Beim Schliessen eines Agenten dessen Deck-seitige Spuren tilgen, damit ein
        spaeter WIEDERVERWENDETER Slot-Name (die Extension vergibt <Fenster><max+1>,
        recycelt also den Namen des geschlossenen hoechsten Agenten) NICHT den angenommenen
        Permission-Mode, dessen Hook-ts, eine offene Auto-Startmodus-Vormerkung oder den
        alten Status aus der liegengebliebenen Zustands-Datei erbt."""
        self.slot_mode.pop(slot, None)
        self._mode_ts.pop(slot, None)
        self._pending_auto.pop(slot, None)
        dc.clear_state(slot)

    def close_window(self, win):
        """Das ganze VS-Code-Fenster dieses Buchstabens schliessen (inkl. aller Agenten
        darin). Die Extension trennt sich danach vom Broker; sobald auch das native
        Fenster zu ist, raeumt _cleanup_closed_windows die Bindung nach kurzem Grace
        automatisch ab -> die Kachel verschwindet. Ein spaeter wieder geoeffnetes Fenster
        bindet sich per _sync_bindings von selbst neu (dann ggf. an einen anderen
        Buchstaben, falls der frei war)."""
        if not self.broker.connected(win):
            return
        if self.cmds.close_window(win):
            # Alle Agenten des Fensters gehen mit zu -> ihre angehaengten worktrees
            # ebenso aufraeumen wie beim Einzel-Schliessen (close_agent).
            changed = False
            for slot in self._slots_for_window(win):
                self._cleanup_worktrees(slot)
                if self.tickets.pop(slot, None) is not None:
                    changed = True
                self._clear_found_ticket(slot)
                self._forget_slot(slot)      # gemerkten Modus/State tilgen (Slot-Name wird recycelt)
            if changed:
                self.store.save_tickets()
            if self.active_slot and self.active_slot[0] == win:
                self.active_slot = None

    # ── Ticket -> isolierter git worktree ───────────────
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

    # ── Einstellungen ───────────────────────────────────
    def _open_settings(self):
        """Frost-gestyltes Einstellungs-Fenster (⚙ in der unteren Leiste). Steuert die
        vier Default-Werte fuer NEU gestartete Claude-Agenten direkt in Claude Codes
        globaler ~/.claude/settings.json (Modell, Permission-Modus, Effort, Antwort-
        sprache) und bietet weiterhin den Panel-Neustart. Stil + modal-Pause wie der
        Ticket-Dialog (sonst klaut ein neu erscheinender Agent den Tastaturfokus)."""
        dlg = tk.Toplevel(self.root)
        dlg.title(i18n.L("Einstellungen", "Settings"))
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        try:
            dlg.attributes("-topmost", True)
        except tk.TclError:
            pass
        dlg.withdraw()      # erst aufbauen+platzieren, dann zeigen (siehe _place_dialog)
        tk.Label(dlg, text=i18n.L("Einstellungen", "Settings"), bg=BG, fg=INK,
                 font=("Segoe UI", 12, "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 2))
        tk.Label(dlg, text=i18n.L(
                     "Standardwerte für neu gestartete Claude-Agenten\n"
                     "(schreibt ~/.claude/settings.json).",
                     "Defaults for newly started Claude agents\n"
                     "(writes ~/.claude/settings.json)."),
                 bg=BG, fg=INK_3, justify="left", font=("Segoe UI", 9)).grid(
                     row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

        # Ist-Werte aus settings.json vorbelegen (fehlt/unbekannt -> erster Eintrag).
        cur = cset.read_values()
        # Modell kommt aus der DECK-eigenen Einstellung (deck_settings.json), NICHT aus
        # settings.json: dort ist 'model' der schwaechste Hebel und verwirft das '[1m]'-
        # Suffix. Fallback auf einen evtl. alten settings.json-Wert, sonst erster Eintrag.
        cur_model = self.settings.get("model") or cur["model"]
        model_var = tk.StringVar(value=cset.value_to_label(cset.MODEL_CHOICES, cur_model, contains=True))
        mode_var = tk.StringVar(value=cset.value_to_label(cset.MODE_CHOICES, cur["mode"]))
        effort_var = tk.StringVar(value=cset.effort_label(cur["effort"], cur["ultracode"]))
        # Sprache: DECK-eigene, lokalisierte Anzeige-Labels mit direkter Wert-Zuordnung
        # (die kanonischen cset.LANG_CHOICES bleiben unangetastet – von den Unit-Tests
        # gepinnt). Reihenfolge Deutsch, Englisch; unbekannt/leer -> Deutsch. Dasselbe
        # Feld steuert BEIDES: Antwortsprache der Agenten UND Sprache der Deck-Oberflaeche.
        lang_display = [(i18n.L("Deutsch", "German"), "german"),
                        (i18n.L("Englisch", "English"), "english")]
        _lang_v2d = {v: d for d, v in lang_display}
        _lang_d2v = {d: v for d, v in lang_display}
        lang_var = tk.StringVar(
            value=_lang_v2d.get(i18n.normalize(cur["language"]), lang_display[0][0]))

        def _row(r, label, var, labels):
            tk.Label(dlg, text=label, bg=BG, fg=INK_2, font=("Segoe UI", 10)).grid(
                row=r, column=0, sticky="w", padx=(16, 10), pady=4)
            om = tk.OptionMenu(dlg, var, *labels)
            om.configure(bg="#23232b", fg=INK, activebackground="#33333d",
                         activeforeground="#ffffff", relief="flat", bd=0,
                         highlightthickness=0, anchor="w", width=18,
                         font=("Segoe UI", 9), cursor="hand2")
            try:
                om["menu"].configure(bg="#23232b", fg=INK, activebackground="#33333d",
                                     activeforeground="#ffffff", bd=0, relief="flat")
            except tk.TclError:
                pass
            om.grid(row=r, column=1, sticky="w", padx=(0, 16), pady=4)

        _row(2, i18n.L("Modell", "Model"), model_var, [l for l, _ in cset.MODEL_CHOICES])
        _row(3, i18n.L("Modus", "Mode"), mode_var, [l for l, _ in cset.MODE_CHOICES])
        _row(4, "Effort", effort_var, cset.EFFORT_CHOICES)
        _row(5, i18n.L("Sprache", "Language"), lang_var, [d for d, _ in lang_display])

        # Statuszeile (von Speichern UND dem Ring-Schalter genutzt) – vor beiden anlegen.
        status = tk.Label(dlg, text="", bg=BG, fg=INK_3, justify="left",
                          font=("Segoe UI", 9))
        status.grid(row=10, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 0))

        # ── Ring um Chat (VS-Code-Glow) ──────────────────────────────────────
        # Eigene Deck-Einstellung (deck_settings.json 'glow'), KEIN Claude-Setting:
        # patcht VS Codes workbench.html direkt (reenable_glow). Wirkt sofort beim
        # Umschalten; nach einem VS-Code-Update spielt das Deck den Patch beim Start
        # selbst wieder ein (_glow_self_heal). VS Code danach jeweils neu laden.
        glow_var = tk.BooleanVar(value=bool(self.settings.get("glow")))

        def _toggle_glow():
            on = glow_var.get()
            self.settings["glow"] = on
            self.store.save_settings()
            try:
                ok, total, err = rg.set_glow(on)
            except Exception as e:                                   # noqa: BLE001
                status.configure(text=i18n.L(f"Ring: Fehler – {e}", f"Ring: error – {e}"),
                                 fg="#ff6b6b")
                return
            if err:
                status.configure(text=f"Ring: {err}", fg="#ff6b6b")
            elif not total:
                status.configure(
                    text=i18n.L("Ring: keine VS-Code-Installation gefunden.",
                                "Ring: no VS Code installation found."),
                    fg="#ff6b6b")
            else:
                verb = i18n.L("aktiviert", "enabled") if on else i18n.L("entfernt", "removed")
                status.configure(
                    text=i18n.L(
                        f"Ring {verb} ({ok}/{total}) – in VS Code das Fenster neu laden.",
                        f"Ring {verb} ({ok}/{total}) – reload the window in VS Code."),
                    fg=("#6ee7a8" if on else INK_2))

        cb = tk.Checkbutton(dlg, text=i18n.L("Ring um Chat  (Glow um den fokussierten Chat)",
                                             "Ring around chat  (glow around the focused chat)"),
                            variable=glow_var, command=_toggle_glow, bg=BG, fg=INK_2,
                            selectcolor="#23232b", activebackground=BG,
                            activeforeground=INK, bd=0, highlightthickness=0,
                            anchor="w", font=("Segoe UI", 10), cursor="hand2")
        cb.grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 2))

        # ── Jira-Projekt-Präfix ──────────────────────────────────────────────
        # Eigene Deck-Einstellung (deck_settings.json 'jira_prefix'), KEIN Claude-
        # Setting. Wird einer NUR als Zahl eingegebenen Ticket-ID vorangestellt
        # (z.B. "2701" -> "<PREFIX>-2701"), damit der Agent das Jira-Ticket eindeutig
        # nachschlagen kann. Leer -> reine Nummern bleiben unveraendert. Wirkt ab der
        # naechsten Ticket-Zuweisung (kein Agenten-Neustart noetig). Default aus config.
        tk.Label(dlg, text=i18n.L("Jira-Projekt-Präfix  (z. B. PROJ → PROJ-2701)",
                                  "Jira project prefix  (e.g. PROJ → PROJ-2701)"), bg=BG,
                 fg=INK_2, font=("Segoe UI", 10)).grid(
                     row=7, column=0, sticky="w", padx=(16, 10), pady=4)
        jira_var = tk.StringVar(
            value=self.settings.get("jira_prefix", getattr(cfg, "JIRA_PROJECT_KEY", "")))
        jira_entry = tk.Entry(dlg, textvariable=jira_var, bg="#23232b", fg=INK,
                              insertbackground=INK, relief="flat",
                              font=("Segoe UI", 10), width=12)
        jira_entry.grid(row=7, column=1, sticky="w", padx=(0, 16), pady=4)

        # ── Am Rand andocken (Auto-Hide) ─────────────────────────────────────
        # Eigene Deck-Einstellung (deck_settings.json 'dock_edge'). Dockt das Fenster
        # an einen Bildschirmrand; es verschwindet dann bis auf einen schmalen Griff,
        # ueber den man es per Hover wieder hervorholt. Wirkt sofort. Hinweis: angedockt
        # gibt es keine Titelleiste – zum Schliessen der App hier wieder "Aus" waehlen.
        DOCK_CHOICES = [(i18n.L("Aus", "Off"), "off"), (i18n.L("Links", "Left"), "left"),
                        (i18n.L("Rechts", "Right"), "right"), (i18n.L("Oben", "Top"), "top")]
        _dock_l2v = {l: v for l, v in DOCK_CHOICES}
        _dock_v2l = {v: l for l, v in DOCK_CHOICES}
        cur_edge = self.dock.current_edge() if self.dock else \
            self.settings.get("dock_edge", "off")
        dock_var = tk.StringVar(value=_dock_v2l.get(cur_edge, DOCK_CHOICES[0][0]))

        def _on_dock(label):
            edge = _dock_l2v.get(label, "off")
            if not self.dock:
                return
            if edge == "off":
                self.dock.set_edge(edge)      # abdocken: Fenster kommt zurueck, Dialog bleibt
                return
            # Angedockt verschwindet das Panel bis auf den Griff-Balken – ein weiter
            # offener Dialog (modal + topmost) haengt dann frei im Bild und blockiert
            # das Deck. Also mit dem Andocken schliessen. Beides verzoegert und in
            # dieser Reihenfolge: wir stecken hier noch im command-Callback des
            # OptionMenus (ein destroy mittendrin zerreisst das Menue-Widget), und
            # erst nach dem Schliessen ist der grab weg, wenn der Dock-Poll anlaeuft.
            def _close_then_dock():
                dlg.destroy()
                self.root.after_idle(
                    lambda: self.dock.set_edge(edge) if self.dock else None)

            self.root.after(0, _close_then_dock)

        tk.Label(dlg, text=i18n.L("Am Rand andocken  (Auto-Hide auf Griff-Balken)",
                                  "Dock to edge  (auto-hide to a handle bar)"), bg=BG,
                 fg=INK_2, font=("Segoe UI", 10)).grid(
                     row=8, column=0, sticky="w", padx=(16, 10), pady=4)
        dock_om = tk.OptionMenu(dlg, dock_var, *[l for l, _ in DOCK_CHOICES],
                                command=_on_dock)
        dock_om.configure(bg="#23232b", fg=INK, activebackground="#33333d",
                          activeforeground="#ffffff", relief="flat", bd=0,
                          highlightthickness=0, anchor="w", width=18,
                          font=("Segoe UI", 9), cursor="hand2")
        try:
            dock_om["menu"].configure(bg="#23232b", fg=INK, activebackground="#33333d",
                                      activeforeground="#ffffff", bd=0, relief="flat")
        except tk.TclError:
            pass
        dock_om.grid(row=8, column=1, sticky="w", padx=(0, 16), pady=4)

        def _save():
            try:
                # Deck-eigene Werte zuerst persistieren (unabhaengig von den Claude-
                # Settings unten). Jira-Praefix gross geschrieben, wie ein Projekt-Key.
                self.settings["jira_prefix"] = jira_var.get().strip().upper()
                # Modell DECK-eigen speichern: nur so wird es beim Start als
                # `claude --model <wert>` erzwungen (CLI-Flag = hoechste Prioritaet).
                # NICHT nach settings.json schreiben – dort ist 'model' der schwaechste
                # Hebel (User-Scope), verwirft das '[1m]'-Suffix und wird vom zuletzt
                # per /model gewaehlten, in ~/.claude.json gemerkten Modell ueberstimmt.
                self.settings["model"] = cset.label_to_value(cset.MODEL_CHOICES, model_var.get())
                self.store.save_settings()
                lvl, uc = cset.effort_spec(effort_var.get())
                cset.write_values(
                    mode=cset.label_to_value(cset.MODE_CHOICES, mode_var.get()),
                    effort=lvl, ultracode=uc,
                    language=_lang_d2v.get(lang_var.get(), "german"),
                )
                i18n.refresh()   # Deck-Sprache sofort uebernehmen (voll durchgaengig nach Neustart)
                status.configure(
                    text=i18n.L(
                        "Gespeichert ✓ – gilt für neu gestartete Agenten (Sprache der "
                        "Oberfläche: Panel neu starten).",
                        "Saved ✓ – applies to newly started agents (UI language: restart "
                        "the panel)."),
                    fg="#6ee7a8")
            except Exception as e:                                   # noqa: BLE001 (dem Nutzer zeigen)
                status.configure(
                    text=i18n.L(f"Fehler beim Speichern: {e}", f"Error while saving: {e}"),
                    fg="#ff6b6b")

        btns = tk.Frame(dlg, bg=BG)
        btns.grid(row=11, column=0, columnspan=2, sticky="e", padx=12, pady=(10, 12))
        ck.btn(btns, i18n.L("💾 Speichern", "💾 Save"), _save)
        ck.btn(btns, i18n.L("⟳ Panel neu starten", "⟳ Restart panel"), self.restart)
        ck.btn(btns, i18n.L("Schließen", "Close"), dlg.destroy)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        self._place_dialog(dlg)
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        self._set_modal(True)
        try:
            self.root.wait_window(dlg)
        finally:
            self._set_modal(False)

    # ── Panel neu starten ───────────────────────────────
    def restart(self):
        """Das ganze Panel neu starten: eine frische Instanz mit DEMSELBEN Interpreter
        und denselben Argumenten starten, dann die aktuelle beenden. Erst wenn der neue
        Prozess erfolgreich gestartet ist, wird der Broker-Socket geschlossen (Port 8765
        frei) und os._exit gerufen – so bleibt bei einem Fehlstart die laufende Instanz
        heil. Persistente Dateien (bindings/effort) ueberleben den Neustart."""
        script = os.path.abspath(sys.argv[0])
        # RESTART_ENV im Kind setzen -> der Single-Instance-Guard erkennt das als
        # Neustart-Uebergabe (alt+neu leben kurz gleichzeitig) und tritt NICHT als
        # vermeintlicher Doppelstart zurueck.
        env = dict(os.environ)
        env[si.RESTART_ENV] = "1"
        try:
            subprocess.Popen([sys.executable, script] + sys.argv[1:],
                             cwd=os.path.dirname(script), env=env)
        except Exception:
            return          # Fehlstart -> laufende Instanz heil lassen (Port bleibt belegt)
        # Neuer Prozess laeuft -> Port sofort freigeben und alte Instanz hart beenden.
        self.broker.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
        # Spur hinterlassen, BEVOR os._exit alles abschneidet: _exit laesst weder
        # atexit noch faulthandler zum Zug kommen, dieser Neustart sah im Log also
        # aus wie ein spurloses Verschwinden ("ABGESCHOSSEN"). Bewusst KEINE
        # "normaler Exit"-Marke: kommt das Kind nicht hoch, SOLL der Waechter
        # einspringen (siehe watchdog.last_end).
        log.note("--- Panel-Ende (Neustart, Kind uebernimmt) ---")
        os._exit(0)

    def run(self):
        self.root.mainloop()
        # Regulaeres Ende: der mainloop kehrt zurueck, wenn das Fenster zerstoert
        # wurde (Schliessen/restart). Ein FEHLER kommt hier nicht durch – der fliegt
        # aus mainloop() heraus und landet im except in __main__ (und danach als
        # "UNBEHANDELTE EXCEPTION" im Log). Diese Zeile ist also kein Alarm, sondern
        # der Beleg, dass das Panel selbst gegangen ist; watchdog.last_end() liest sie
        # bewusst NICHT als Fehler.
        log.note("mainloop beendet (Fenster zerstoert) -> Panel endet regulaer")


if __name__ == "__main__":
    # Diagnose als Erstes: ein Fehlstart soll auch dann im Log stehen, wenn noch
    # gar kein Fenster existiert (unter pythonw gibt es sonst KEINE Ausgabe).
    log.install()
    # Single-Instance-Guard VOR dem Broker-Start: laeuft schon ein Panel, dieses
    # nach vorn holen und leise beenden -> kein zweites (totes) Panel, das alle
    # Fenster faelschlich als "nicht verbunden" zeigt.
    if not si.acquire_or_focus():
        log.note("schon ein Panel da -> dieses tritt zurueck")
        sys.exit(0)
    try:
        AgentDeck().run()
    except BaseException:
        log.exc("Panel-Start/Lauf")
        raise
