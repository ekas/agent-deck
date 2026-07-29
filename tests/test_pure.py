"""Unit-Tests fuer die pure (anzeigefreie) Logik, die durch den Umbau erst
erreichbar wurde: status_model, bindstore-Helfer, canvas_kit-Farb/Text-Helfer.

Laeuft mit pytest ODER direkt:  python tests/test_pure.py
(kein pytest noetig; unten ist ein Mini-Runner.)
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deck.domain import status_model as sm
from deck.domain import binding
from deck.render import kit as ck
from deck.claude.hooks import report
from deck.ui import theme
from deck.claude import usage as cu
from deck.domain import config as cfg
from deck.ops import worktree as wtc
from deck.claude import summarize as cs
from deck.claude import settings as cset
from deck.dock import controller as ed
from deck.render import capsule as hrender
from deck.render import fluid as hwave
from deck.platform import monitor as sf
from deck.platform import focus as wf  # nur fuer die Bildrate im Takt-Test (edge_dock laedt es ohnehin)
from deck import i18n

# Die Usage-/Anzeige-Tests pruefen die deutsche Baseline. Deck-Sprache hier fest auf
# Deutsch stellen, damit die Tests unabhaengig vom realen ~/.claude/settings.json
# (das schon auf 'english' stehen kann) deterministisch bleiben.
i18n._lang = i18n.GERMAN

_GLOW = {"idle": 1, "done": 1, "thinking": 1, "running": 1, "waiting": 1, "none": 1}
_CYCLE = ["manual", "accept", "plan", "auto"]


# ── status_model ─────────────────────────────────────────
def test_is_fresh():
    assert sm.is_fresh({"ts": 100}, 100, 900) is True
    assert sm.is_fresh({"ts": 100}, 1001, 900) is False
    assert sm.is_fresh(None, 100, 900) is False


def test_normalize_status():
    assert sm.normalize_status("thinking", False, _GLOW) == "idle"   # eingeschlafen
    assert sm.normalize_status("thinking", True, _GLOW) == "thinking"
    assert sm.normalize_status("running", False, _GLOW) == "idle"
    assert sm.normalize_status("waiting", False, _GLOW) == "waiting"  # nicht thinking/running
    assert sm.normalize_status("bogus", True, _GLOW) == "idle"        # unbekannt
    assert sm.normalize_status("none", True, _GLOW) == "none"


def test_is_lost():
    assert sm.is_lost("thinking", True, False) is True
    assert sm.is_lost("thinking", True, True) is False               # verbunden -> nicht lost
    assert sm.is_lost("none", True, False) is False                  # none nie lost
    assert sm.is_lost("idle", False, False) is False                 # nicht frisch -> nicht lost


def test_dominant_status():
    # Rangfolge fuer den Neon-Griff: Rueckfrage > ungelesen > getrennt > denkt > idle.
    assert sm.dominant_status(["idle", "thinking", "done", "waiting"]) == "waiting"
    assert sm.dominant_status(["idle", "thinking", "done"]) == "done"
    assert sm.dominant_status(["idle", "thinking", "lost"]) == "lost"
    assert sm.dominant_status(["idle", "running"]) == "thinking"   # running == denkt
    assert sm.dominant_status(["idle", "idle"]) == "idle"      # alle idle -> idle
    assert sm.dominant_status([]) == "none"                    # keine Kachel -> kein Leuchten
    assert sm.dominant_status(["none"]) == "none"
    assert sm.dominant_status(["bogus"]) == "none"             # Unbekanntes zaehlt nicht


def test_escalated():
    assert sm.escalated("idle", "waiting") is True             # dringlicher -> Blitz
    assert sm.escalated("thinking", "done") is True            # fertig geworden -> Blitz
    assert sm.escalated("done", "idle") is False               # gelesen -> kein Blitz
    assert sm.escalated("waiting", "done") is False            # ruhiger -> kein Blitz
    assert sm.escalated("done", "done") is False               # kein Wechsel
    assert sm.escalated("thinking", "running") is False        # derselbe Zustand, gleicher Rang
    assert sm.escalated("running", "thinking") is False        # ...auch andersherum


def test_neon_color_and_tint():
    """Neon-Griff (edge_dock): Farbrechnung der Röhren-Schichten, ohne Fenster."""
    AMBER = "#ffc48a"
    core_fade, halo_fade = ed.NEON_LAYERS[-1][1], ed.NEON_LAYERS[0][1]
    assert core_fade == 0.0 and halo_fade > 0.5      # Kern kraeftig, aussen blass
    # Volle Leuchtkraft: Kern = Statusfarbe Richtung Weiss, ohne Beimischung von HANDLE_BG.
    assert ed.neon_color(AMBER, core_fade, 1.0) == ck.mix(AMBER, "#ffffff",
                                                          ed.NEON_CORE_WHITE)
    # Aufblitzen (eff > 1) klemmt auf dieselbe Vollfarbe (kein Ueberlauf).
    assert ed.neon_color(AMBER, core_fade, 1.6) == ed.neon_color(AMBER, core_fade, 1.0)
    # Halo ist immer blasser als der Kern, aber nie ganz HANDLE_BG bei Vollausschlag.
    halo = ed.neon_color(AMBER, halo_fade, 1.0)
    assert halo != ed.HANDLE_BG and halo != AMBER
    # Dunkel (eff = 0) -> Schicht verschwindet in der Griff-Grundfarbe.
    assert ed.neon_color(AMBER, halo_fade, 0.0) == ed.HANDLE_BG
    # Unter dem Zeiger ist der Kern heller (mehr Weissanteil), aber dieselbe Familie.
    assert ed.neon_color(AMBER, core_fade, 1.0, hot=True) != ed.neon_color(
        AMBER, core_fade, 1.0)
    assert ed.NEON_HOT_WHITE > ed.NEON_CORE_WHITE
    # Grundflaeche: getaucht, aber deutlich dunkler als die Statusfarbe selbst.
    assert ed.neon_tint(AMBER, 0.0) == ed.HANDLE_BG
    assert ed.neon_tint(AMBER, 1.0) == ck.mix(ed.HANDLE_BG, AMBER, ed.NEON_TINT)
    assert ed.neon_tint(AMBER, 2.0) == ed.neon_tint(AMBER, 1.0)   # geklemmt


def test_resolve_effort():
    assert sm.resolve_effort("", "ultracode") == "ultracode"
    assert sm.resolve_effort("xhigh", "ultracode") == "ultracode"    # Kollision aufgeloest
    assert sm.resolve_effort("high", "ultracode") == "high"          # echter Wert gewinnt
    assert sm.resolve_effort("xhigh", None) == "xhigh"
    assert sm.resolve_effort("", None) == ""


def test_adopt_hook_mode():
    assert sm.adopt_hook_mode(0, {"mode": "plan", "ts": 5}, _CYCLE) == (2, 5)
    assert sm.adopt_hook_mode(9, {"mode": "plan", "ts": 5}, _CYCLE) is None   # aelterer Event
    assert sm.adopt_hook_mode(0, {"mode": "bogus", "ts": 5}, _CYCLE) is None  # ungueltig
    assert sm.adopt_hook_mode(0, {"ts": 5}, _CYCLE) is None                   # kein Modus


def test_mode_steps():
    # Unbekannter aktueller Modus (None) -> vom Start-Modus 'manual' (Index 0) aus rechnen.
    assert sm.mode_steps(None, "auto", _CYCLE, "manual") == (3, 3)   # manual->accept->plan->auto
    assert sm.mode_steps(None, "plan", _CYCLE, "manual") == (2, 2)
    assert sm.mode_steps(None, "manual", _CYCLE, "manual") == (0, 0)  # schon da -> 0 Schritte
    # Gemerkter aktueller Modus gewinnt: von 'plan' (2) nach 'auto' (3) = 1 Schritt.
    assert sm.mode_steps(2, "auto", _CYCLE, "manual") == (1, 3)
    # Zyklisch: von 'auto' (3) zurueck nach 'accept' (1) = (1-3) % 4 = 2 Schritte.
    assert sm.mode_steps(3, "accept", _CYCLE, "manual") == (2, 1)
    # Ungueltiges Ziel -> None (Aufrufer schaltet nicht).
    assert sm.mode_steps(None, "bogus", _CYCLE, "manual") is None
    # Start-Modus nicht im Zyklus -> Fallback auf Index 0.
    assert sm.mode_steps(None, "plan", _CYCLE, "weird") == (2, 2)


# ── Auto-Startmodus neuer Agenten (_apply_pending_auto / _set_slot_mode) ──
# Testet die gluecklogik an einem minimalen Fake-Self (ohne tkinter/Broker), indem die
# echten (ungebundenen) Methoden darauf aufgerufen werden. Deckt die im Review bestaetigten
# Faelle ab: Reuse mit veralteter State-Datei, Sende-Fehler-Retry, TTL, Button vs. Auto.
class _FakeCmds:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def send_key(self, slot, key, repeat=1):
        self.calls.append((slot, key, repeat))
        return self.ok


def _fake_deck(ok=True):
    from deck.ui import panel as ad
    f = type("F", (), {})()
    f._pending_auto = {}
    f.slot_mode = {}
    f.cmds = _FakeCmds(ok)
    f._apply_pending_auto = ad.AgentDeck._apply_pending_auto.__get__(f)
    f._set_slot_mode = ad.AgentDeck._set_slot_mode.__get__(f)
    return f, ad


def _pa(base_ts=0.0, reg_ts=100.0, ready_ts=0.0, sent_ts=0.0, tries=0):
    """Ein _pending_auto-Fortschritts-Dict fuer die Tests bauen (Defaults = frisch vorgemerkt)."""
    return {"base_ts": base_ts, "reg_ts": reg_ts,
            "ready_ts": ready_ts, "sent_ts": sent_ts, "tries": tries}


def test_apply_pending_auto():
    assert cfg.NEW_AGENT_MODE == "auto"          # Testdaten gehen von diesem Ziel aus
    from deck.ui import panel as ad
    GRACE = theme.AUTO_READY_GRACE

    # Readiness-Gate: der ERSTE frische Hook armt nur die Uhr, es wird NICHT sofort getippt.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(base_ts=0.0, reg_ts=100.0)}
    f._apply_pending_auto({"A1": {"ts": 101.0}}, 101.0, _CYCLE)
    assert f.cmds.calls == [] and f.slot_mode == {}
    assert f._pending_auto["A1"]["ready_ts"] == 101.0 and f._pending_auto["A1"]["sent_ts"] == 0.0

    # ... nach AUTO_READY_GRACE dann 3 Shift+Tab (ab MODE_START manual->auto), aber NOCH
    # vorgemerkt (auf Ist-Bestaetigung wartend), slot_mode = auto(3), tries=1.
    f._apply_pending_auto({"A1": {"ts": 101.0}}, 101.0 + GRACE, _CYCLE)
    assert f.cmds.calls == [("A1", "shift-tab", 3)] and f.slot_mode == {"A1": 3}
    assert "A1" in f._pending_auto and f._pending_auto["A1"]["tries"] == 1
    assert f._pending_auto["A1"]["sent_ts"] == 101.0 + GRACE

    # Bestaetigung: nach dem Senden meldet ein Hook (ts > sent_ts) mode='auto' -> fertig, vergessen.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(ready_ts=101.0, sent_ts=103.0, tries=1)}
    f._apply_pending_auto({"A1": {"ts": 105.0, "mode": "auto"}}, 110.0, _CYCLE)
    assert f.cmds.calls == [] and f._pending_auto == {}

    # Kurz gelandet: Hook meldet mode='plan' -> vom Ist (plan=2) 1 Shift+Tab nachtreiben, tries++.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(ready_ts=101.0, sent_ts=103.0, tries=1)}
    f._apply_pending_auto({"A1": {"ts": 105.0, "mode": "plan"}}, 110.0, _CYCLE)
    assert f.cmds.calls == [("A1", "shift-tab", 1)] and f.slot_mode == {"A1": 3}
    assert f._pending_auto["A1"]["tries"] == 2 and f._pending_auto["A1"]["sent_ts"] == 110.0

    # AUTO_MAX_TRIES erschoepft + immer noch nicht im Ziel -> aufgeben (kein weiteres Senden).
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(ready_ts=101.0, sent_ts=103.0, tries=theme.AUTO_MAX_TRIES)}
    f._apply_pending_auto({"A1": {"ts": 105.0, "mode": "plan"}}, 110.0, _CYCLE)
    assert f.cmds.calls == [] and f._pending_auto == {}

    # Externer Slot-Reuse: alte Restdatei meldet (vererbtes) mode='auto', base=alt, ready gesetzt.
    # Der Erst-Antrieb MUSS ab MODE_START rechnen (3 Schritte), NICHT dem vererbten Modus glauben.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(base_ts=50.0, ready_ts=101.0)}
    f._apply_pending_auto({"A1": {"ts": 101.0, "mode": "auto"}}, 101.0 + GRACE, _CYCLE)
    assert f.cmds.calls == [("A1", "shift-tab", 3)] and f.slot_mode == {"A1": 3}

    # Nur alte Restdatei, kein NEUERER Hook (ts == base) -> nichts, Uhr NICHT gearmt.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(base_ts=101.0)}
    f._apply_pending_auto({"A1": {"ts": 101.0}}, 101.5, _CYCLE)
    assert f.cmds.calls == [] and f._pending_auto["A1"]["ready_ts"] == 0.0

    # Sende-Fehler (Verbindungsabriss) beim Erst-Antrieb -> sent_ts bleibt 0 (Retry), slot_mode leer.
    f, _ = _fake_deck(ok=False); f._pending_auto = {"A1": _pa(ready_ts=101.0)}
    f._apply_pending_auto({"A1": {"ts": 101.0}}, 101.0 + GRACE, _CYCLE)
    assert f.cmds.calls == [("A1", "shift-tab", 3)] and f.slot_mode == {}
    assert f._pending_auto["A1"]["sent_ts"] == 0.0 and f._pending_auto["A1"]["tries"] == 0

    # TTL abgelaufen (relativ zur reg-ts) -> aufgeben, nichts senden.
    f, _ = _fake_deck(); f._pending_auto = {"A1": _pa(reg_ts=100.0)}
    f._apply_pending_auto({"A1": {"ts": 101.0}}, 100.0 + theme.PENDING_AUTO_TTL + 1, _CYCLE)
    assert f.cmds.calls == [] and f._pending_auto == {}

    # Button-Pfad (_set_slot_mode current=None) folgt dem gemerkten slot_mode: plan(2)->auto = 1 Schritt.
    f, _ = _fake_deck(); f.slot_mode = {"B2": 2}
    assert f._set_slot_mode("B2", "auto", _CYCLE) is True
    assert f.cmds.calls == [("B2", "shift-tab", 1)] and f.slot_mode == {"B2": 3}


# ── Neon-Griff: Panel -> Dock (_update_dock_glow) ─────────
class _FakeDock:
    def __init__(self):
        self.calls = []

    def set_glow(self, color, intensity=1.0, pulse=False, flash=False):
        self.calls.append((color, intensity, pulse, flash))


def _glow_deck():
    """Fake-Self mit der echten (ungebundenen) _update_dock_glow-Methode."""
    from deck.ui import panel as ad
    f = type("F", (), {})()
    f.dock = _FakeDock()
    f._dock_key = None
    f._update_dock_glow = ad.AgentDeck._update_dock_glow.__get__(f)
    return f, ad


def test_update_dock_glow():
    f, ad = _glow_deck()
    # Erster Aufruf: dominanter Status faerbt den Griff, aber KEIN Blitz (kein Vorzustand).
    f._update_dock_glow(["idle", "thinking", "waiting"])
    assert f.dock.calls == [
        (theme.GLOW_STYLE["waiting"][0], theme.GLOW_STYLE["waiting"][1], True, False)]
    assert f._dock_key == "waiting"

    # Ruhiger werdend (Rueckfrage beantwortet -> nur noch ungelesen): kein Blitz.
    f.dock.calls.clear()
    f._update_dock_glow(["idle", "done"])
    assert f.dock.calls == [(theme.GLOW_STYLE["done"][0], theme.GLOW_STYLE["done"][1], False, False)]

    # Gelesen -> alle idle: graue Ruhefarbe, kein Blitz.
    f.dock.calls.clear()
    f._update_dock_glow(["idle", "idle"])
    assert f.dock.calls == [(theme.GLOW_STYLE["idle"][0], theme.GLOW_STYLE["idle"][1], False, False)]

    # Jetzt wird einer fertig -> gruen UND Blitz (dringlicher als idle).
    f.dock.calls.clear()
    f._update_dock_glow(["idle", "done"])
    assert f.dock.calls == [(theme.GLOW_STYLE["done"][0], theme.GLOW_STYLE["done"][1], False, True)]

    # Verbindung verloren -> Rot kommt NICHT aus GLOW_STYLE (im Panel berechnet), ruhig.
    # Aus 'ungelesen' heraus ist Rot der ruhigere Rang -> kein Blitz, nur Farbwechsel.
    f.dock.calls.clear()
    f._update_dock_glow(["idle", "lost"])
    assert f.dock.calls == [(theme.LOST_GLOW, 1.0, False, False)]

    # Keine Kachel -> 'none' (Intensitaet 0; das Dock faellt selbst auf Cyan zurueck).
    f.dock.calls.clear()
    f._update_dock_glow([])
    assert f.dock.calls == [(theme.GLOW_STYLE["none"][0], 0.0, False, False)]

    # Ohne Dock (schwebendes Fenster) darf nichts passieren.
    f.dock = None
    f._update_dock_glow(["waiting"])          # kein AttributeError


# ── bindstore-Helfer ─────────────────────────────────────
def test_is_placeholder_ws():
    assert binding.is_placeholder_ws("") is True
    assert binding.is_placeholder_ws(None) is True
    assert binding.is_placeholder_ws("unknown") is True
    assert binding.is_placeholder_ws("  Unknown  ") is True        # getrimmt/case-insensitiv
    assert binding.is_placeholder_ws("myrepo") is False


def test_repo_from_title():
    assert binding.repo_from_title("main.py - myrepo - Visual Studio Code") == "myrepo"
    assert binding.repo_from_title("● app.tsx - acme-client - Visual Studio Code") == "acme-client"
    assert binding.repo_from_title("Visual Studio Code") == "Visual Studio Code"  # nur Marker -> kein Ordner davor


def test_ticket_slug():
    assert binding.ticket_slug("ABC-123") == "abc-123"
    assert binding.ticket_slug("ABC-123: Login fix") == "abc-123-login-fix"
    assert binding.ticket_slug("  #42  ") == "42"                   # getrimmt, Sonderzeichen -> weg
    assert binding.ticket_slug("a//b__c") == "a-b-c"               # Laeufe zu einem '-'
    assert binding.ticket_slug("") == ""
    assert binding.ticket_slug("---") == ""                        # nur Trenner -> leer


def test_ticket_branch():
    assert binding.ticket_branch("ABC-123") == "ticket/abc-123"
    assert binding.ticket_branch("ABC-123", prefix="feat/") == "feat/abc-123"
    assert binding.ticket_branch("") == ""                          # leerer Slug -> kein Branch
    assert binding.ticket_branch("!!!") == ""


def test_jira_key():
    assert binding.jira_key("2701", project="PROJ") == "PROJ-2701"   # nur Nummer -> Praefix davor
    assert binding.jira_key("  #42 ", project="PROJ") == "PROJ-42"   # getrimmt, '#' weg
    assert binding.jira_key("PROJ-2701", project="PROJ") == "PROJ-2701"  # schon ein Key -> so lassen
    assert binding.jira_key("abc-123", project="PROJ") == "ABC-123"  # Key gewinnt, Projektteil gross
    assert binding.jira_key("2701", project="") == "2701"            # kein Projekt -> Nummer roh
    assert binding.jira_key("", project="PROJ") == ""                # leer -> leer


# ── Ticket-Prompts: EINZEILIG + alle Platzhalter gefuellt (inkl. {wt_marker}) ──
# Der Text geht per sendText(execute=True) in den pty; ein \n wuerde ihn zerreissen,
# ein uebrig gebliebenes {…} wuerde der Agent woertlich sehen.
def _assert_clean_prompt(p, must_contain):
    assert "\n" not in p and "\r" not in p, "Prompt ist mehrzeilig"
    assert "{" not in p and "}" not in p, "unaufgeloester Platzhalter"
    for s in must_contain:
        assert s in p, f"fehlt im Prompt: {s}"


def test_ticket_prompt_single_line_and_filled():
    wt = "C:/Users/x/AppData/Local/claude-agent-deck/state/A1.worktree"
    p = cfg.TICKET_PROMPT.format(ticket="ABC-123", jira_key="ABC-123",
                                 branch="ticket/abc-123", slug="abc-123",
                                 wt_marker=wt, task="mach was")
    # jira_key + Jira-Lookup/Kurzvorstellung muessen drinstehen.
    _assert_clean_prompt(p, ["ABC-123", "ticket/abc-123", wt, "mach was", "Jira"])


def test_ticket_search_prompt_single_line_and_filled():
    marker = "C:/x/state/A1.ticket"
    wt = "C:/x/state/A1.worktree"
    s = cfg.TICKET_SEARCH_PROMPT.format(prefix="ticket/", marker=marker,
                                        wt_marker=wt, task="mach was")
    _assert_clean_prompt(s, ["ticket/", marker, wt, "mach was", "Jira"])


# ── worktree_cleanup: pure Parser/Selektoren ─────────────────────────────
_WT_PORCELAIN = (
    "worktree C:/repo/my-backend\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree C:/repo/my-backend.wt/abc-123\n"
    "HEAD 2222222222222222222222222222222222222222\n"
    "branch refs/heads/ticket/abc-123\n"
    "\n"
    "worktree C:/repo/detached-one\n"
    "HEAD 3333333333333333333333333333333333333333\n"
    "detached\n"
)


def test_wt_parse_porcelain():
    e = wtc.parse_worktrees_porcelain(_WT_PORCELAIN)
    assert [x["path"] for x in e] == [
        "C:/repo/my-backend",
        "C:/repo/my-backend.wt/abc-123",
        "C:/repo/detached-one",
    ]
    assert [x["branch"] for x in e] == ["main", "ticket/abc-123", None]  # refs/heads/ ab
    assert wtc.parse_worktrees_porcelain("") == []
    assert wtc.parse_worktrees_porcelain(None) == []


def test_wt_main_and_branch_lookup():
    e = wtc.parse_worktrees_porcelain(_WT_PORCELAIN)
    assert wtc.main_path(e) == "C:/repo/my-backend"          # erster Eintrag
    assert wtc.path_for_branch(e, "ticket/abc-123") == "C:/repo/my-backend.wt/abc-123"
    assert wtc.path_for_branch(e, "ticket/nope") is None                 # kein Treffer
    assert wtc.path_for_branch(e, "") is None                            # kein Branch
    assert wtc.path_for_branch([], "ticket/abc-123") is None
    assert wtc.main_path([]) is None


# ── worktree_cleanup: die Sicherung is_linked_worktree (die einzige Schranke vor
# rmtree, wenn git fehlt). Nur ein VERLINKTER worktree (.git -> …/worktrees/<name>)
# darf True sein – Submodul (…/modules/…), separate-git-dir und Haupt-Checkout NICHT.
def test_is_linked_worktree_guard():
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtguard_")
    try:
        def mk(name, gitfile_content=None, gitdir=False):
            d = os.path.join(base, name)
            os.makedirs(d)
            if gitdir:
                os.makedirs(os.path.join(d, ".git"))                     # Haupt-Checkout
            elif gitfile_content is not None:
                with open(os.path.join(d, ".git"), "w", encoding="utf-8") as f:
                    f.write(gitfile_content)
            return d

        linked = mk("linked", "gitdir: C:/repo/.git/worktrees/abc-123\n")
        submod = mk("submod", "gitdir: ../.git/modules/sub\n")
        sepdir = mk("sepdir", "gitdir: C:/elsewhere/store.git\n")
        maindir = mk("maindir", gitdir=True)
        plaindir = mk("plaindir")                                        # gar kein .git
        garbage = mk("garbage", "not a gitdir line\n")

        assert wtc.is_linked_worktree(linked) is True
        assert wtc.is_linked_worktree(submod) is False                   # Submodul -> modules
        assert wtc.is_linked_worktree(sepdir) is False                   # separate-git-dir
        assert wtc.is_linked_worktree(maindir) is False                  # .git ist Verzeichnis
        assert wtc.is_linked_worktree(plaindir) is False
        assert wtc.is_linked_worktree(garbage) is False
        assert wtc.is_linked_worktree(os.path.join(base, "does-not-exist")) is False
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── worktree_cleanup: '<repo>.wt'-Pfadkonvention (Disk-Orphan-Sweep) ──────
def test_wt_dir_for_repo_roundtrip():
    root = os.path.normpath("C:/repo/my-web-ui")
    wt = wtc.wt_dir_for_repo(root)
    assert wt == root + ".wt"                                        # Geschwisterordner + '.wt'
    assert wtc.repo_root_from_wt_dir(wt) == root                     # Umkehr trifft wieder das Root
    # aus einem worktree-Marker das Repo-Root gewinnen (dirname -> repo_root_from_wt_dir)
    marker = os.path.join(wt, "46845651463")
    assert wtc.repo_root_from_wt_dir(os.path.dirname(marker)) == root
    # kein '.wt'-Ordner / leere Eingaben -> None
    assert wtc.repo_root_from_wt_dir(os.path.normpath("C:/repo/plain")) is None
    assert wtc.wt_dir_for_repo("") is None
    assert wtc.repo_root_from_wt_dir("") is None


def test_list_child_dirs():
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtdisk_")
    try:
        wt = os.path.join(base, "repo.wt")
        os.makedirs(os.path.join(wt, "a"))
        os.makedirs(os.path.join(wt, "b"))
        with open(os.path.join(wt, "loose.txt"), "w") as f:    # Datei -> kein Kind-Ordner
            f.write("x")
        got = sorted(os.path.basename(d) for d in wtc.list_child_dirs(wt))
        assert got == ["a", "b"]
        assert wtc.list_child_dirs(os.path.join(base, "nope")) == []   # kein Verzeichnis -> leer
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_remove_orphan_dir_guard_and_removal():
    """remove_orphan_dir raeumt einen '.wt'-Rest GANZ OHNE .git ab (den remove_worktree
    verweigert), verweigert aber JEDES Verzeichnis, das noch ein .git enthaelt – egal ob
    .git-VERZEICHNIS (echter Checkout) oder .git-DATEI (Submodul/separate-git-dir/worktree).
    Ohne repo laeuft kein git -> reiner rmtree, in den Tests unkritisch."""
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtorphan_")
    try:
        # (1) Rest ohne .git -> wird entfernt.
        leftover = os.path.join(base, "repo.wt", "2701")
        os.makedirs(leftover)
        with open(os.path.join(leftover, "file.txt"), "w") as f:
            f.write("stale")
        assert wtc.remove_orphan_dir(leftover) is True
        assert not os.path.isdir(leftover)
        # (2a) Ordner mit .git-VERZEICHNIS (echter Checkout) -> tabu, bleibt stehen.
        checkout = os.path.join(base, "repo.wt", "real")
        os.makedirs(os.path.join(checkout, ".git"))
        assert wtc.remove_orphan_dir(checkout) is False
        assert os.path.isdir(checkout)
        # (2b) Ordner mit .git-DATEI, die NICHT auf einen worktree zeigt (Submodul/
        #      separate-git-dir) -> ebenfalls tabu (der Fix gegen versehentliches Loeschen).
        submod = os.path.join(base, "repo.wt", "submod")
        os.makedirs(submod)
        with open(os.path.join(submod, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: ../.git/modules/submod\n")
        assert wtc.remove_orphan_dir(submod) is False
        assert os.path.isdir(submod)
        # (3) schon weg -> idempotent True; leerer Pfad -> False.
        assert wtc.remove_orphan_dir(os.path.join(base, "gone")) is True
        assert wtc.remove_orphan_dir("") is False
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── report: letzte User-Frage aus dem UserPromptSubmit-stdin ──────────────
def test_prompt_of():
    assert report._prompt_of({"prompt": "  Hallo Welt  "}) == "Hallo Welt"   # getrimmt
    assert report._prompt_of({"prompt": "   "}) is None                      # nur Whitespace
    assert report._prompt_of({}) is None                                     # kein Feld
    assert report._prompt_of({"prompt": 123}) is None                        # kein String
    long = "x" * 600
    got = report._prompt_of({"prompt": long})
    assert got.endswith("…") and len(got) == 501                             # gekuerzt auf 500 + …


# ── report: Notification -> echte Rueckfrage vs. blosser Leerlauf ─────────
def test_is_real_query():
    # notification_type ist das verlaessliche Kriterium.
    assert report._is_real_query({"notification_type": "permission_prompt"}) is True
    assert report._is_real_query({"notification_type": "elicitation_dialog"}) is True
    assert report._is_real_query({"notification_type": "agent_needs_input"}) is True
    assert report._is_real_query({"notification_type": "idle_prompt"}) is False   # nur Leerlauf
    assert report._is_real_query({"notification_type": "agent_completed"}) is False
    assert report._is_real_query({"notification_type": "auth_success"}) is False
    # Fallback ohne notification_type (aeltere Version): Permission-Meldung erkennen.
    assert report._is_real_query({"message": "Bash: Allow this command?"}) is True
    assert report._is_real_query({"message": "Claude needs your permission to use Bash"}) is True
    assert report._is_real_query({"message": "Claude is waiting for your input"}) is False
    assert report._is_real_query({}) is False                                    # nichts -> keine Rueckfrage


# ── canvas_kit pure Helfer ───────────────────────────────
def test_color_helpers():
    assert ck.hex_to_rgb("#ffffff") == (255, 255, 255)
    assert ck.hex_to_rgb("#010203") == (1, 2, 3)
    assert ck.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert ck.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert ck.mix("#000000", "#ffffff", 2.0) == "#ffffff"            # ueber 1 klemmt
    assert ck.short_model("Opus 5 (1M context)") == "Opus 5 (1M)"
    assert ck.short_model(None) == "—"


def test_plus_liegt_symmetrisch_auf_ganzen_pixeln():
    # Achse und Arm muessen ganzzahlig herauskommen: der tk-Canvas antialiast nicht,
    # eine Linie liegt nur dann symmetrisch um ihre Achse, wenn beide auf dem Raster
    # sitzen. (Die Kachel-Mitte kommt bei Skalierung fast immer gebrochen herein.)
    ax, ay, arm, w = ck.plus_geom(55.5, 69.0, 8.1, 3.3)
    assert (ax, ay) == (56.0, 69.0)
    assert (arm, w) == (8, 3)
    assert float(ax).is_integer() and float(ay).is_integer()

    # .5 rundet immer nach oben – nicht wie round() zur geraden Zahl, sonst haengt
    # die Verschiebung davon ab, wo die Kachel gerade steht.
    assert ck.plus_geom(10.5, 11.5, 5, 3)[:2] == (11.0, 12.0)

    # Strich und Arm bleiben sichtbar, auch wenn klein gerechnet wird
    assert ck.plus_geom(0, 0, 0.1, 0.1)[2:] == (1, 1)


# ── claude_usage: pure Parser der oauth/usage-Antwort ────────────────────
# Ausschnitt einer echten API-Antwort (Session kritisch bei 91 %, Woche 15 %, dazu
# ein modell-spezifisches Wochenlimit bei 0 %).
_USAGE_SAMPLE = {
    "five_hour": {"utilization": 91.0, "resets_at": "2026-07-21T23:00:00+00:00"},
    "seven_day": {"utilization": 15.0, "resets_at": "2026-07-28T12:00:00+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 91, "severity": "critical",
         "resets_at": "2026-07-21T23:00:00+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 15, "severity": "normal",
         "resets_at": "2026-07-28T12:00:00+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal",
         "resets_at": None, "is_active": False,
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
    ],
}


def test_usage_fmt_reset():
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert cu.fmt_reset("2026-01-01T12:45:00+00:00", base) == "45 Min."
    assert cu.fmt_reset("2026-01-01T14:00:00+00:00", base) == "2 Std."
    assert cu.fmt_reset("2026-01-01T14:30:00+00:00", base) == "2 Std. 30 Min."
    assert cu.fmt_reset("2026-01-03T12:00:00+00:00", base) == "2 Tg."
    assert cu.fmt_reset("2026-01-03T15:00:00+00:00", base) == "2 Tg. 3 Std."
    assert cu.fmt_reset("2026-01-01T11:00:00+00:00", base) == "jetzt"    # Vergangenheit
    assert cu.fmt_reset("2026-01-01T12:45:00", base) == "45 Min."        # naiv -> als UTC
    assert cu.fmt_reset(None, base) == ""
    assert cu.fmt_reset("kaputt", base) == ""


def test_usage_severity_color():
    assert cu.severity_color("critical", 91) == "#ff6b6b"
    assert cu.severity_color("warning", 60) == "#ffc48a"
    assert cu.severity_color("normal", 15) == "#6ee7a8"
    assert cu.severity_color("", None) == "#8b8b99"                      # kein Wert -> grau
    assert cu.severity_color("", 30) == "#6ee7a8"                        # Fallback per Schwelle
    assert cu.severity_color("", 70) == "#ffc48a"
    assert cu.severity_color("", 95) == "#ff6b6b"


def test_usage_parse_limits():
    p = cu.parse_usage(_USAGE_SAMPLE)
    assert p["session"]["percent"] == 91
    assert p["session"]["severity"] == "critical"
    assert p["session"]["group"] == "session"
    assert [l["label"] for l in p["limits"]] == ["Session", "Woche", "Fable (Woche)"]


def test_usage_parse_fallback():
    # Aeltere Antwort ohne 'limits' -> aus five_hour/seven_day rekonstruiert.
    p = cu.parse_usage({"five_hour": {"utilization": 91.0, "resets_at": "x"},
                        "seven_day": {"utilization": 15.0, "resets_at": "y"}})
    assert p["session"]["percent"] == 91
    assert [l["label"] for l in p["limits"]] == ["Session", "Woche"]


def test_usage_parse_empty():
    p = cu.parse_usage({})
    assert p["session"] is None and p["limits"] == []


def test_usage_tooltip_text():
    base = datetime(2026, 7, 21, 21, 55, tzinfo=timezone.utc)
    snap = {"state": "ok", "limits": cu.parse_usage(_USAGE_SAMPLE)["limits"], "error": None}
    txt = cu.tooltip_text(snap, base)
    assert "Claude – Nutzung" in txt
    assert "Session: 91 %" in txt
    assert "Woche: 15 %" in txt
    assert "Reset in 1 Std. 5 Min." in txt          # Session-Reset relativ zu base
    assert "Fable" not in txt                        # 0 %/kein Reset/inaktiv -> ausgefiltert


def test_usage_tooltip_error():
    txt = cu.tooltip_text({"state": "error", "limits": [], "error": "nicht angemeldet"})
    assert "nicht angemeldet" in txt


# ── claude_usage: Token der Claude-Code-CLI ──────────────
# Aufbau von ~/.claude/.credentials.json, wie 2026-07-29 vorgefunden. Die Werte sind
# erfunden; geprueft wird nur, dass wir die richtigen FELDER lesen.
def _creds(token="tok-neu", expires_at=None, key="claudeAiOauth"):
    inner = {"accessToken": token, "refreshToken": "rt", "scopes": ["user:inference"],
             "subscriptionType": "max", "rateLimitTier": "default"}
    if expires_at is not None:
        inner["expiresAt"] = expires_at
    return {key: inner}


def test_cli_token_wird_gelesen():
    soon = (time.time() + 3600) * 1000                   # Ablauf in Millisekunden
    assert cu.tokens_from_credentials(_creds("tok-a", soon)) == ["tok-a"]


def test_cli_token_abgelaufen_wird_nicht_gesendet():
    past = (time.time() - 60) * 1000
    assert cu.tokens_from_credentials(_creds("tok-alt", past)) == []


def test_cli_token_ohne_ablaufzeit_gilt_als_gueltig():
    """Ein totes Token kostet nur einen 401 – fetch_usage nimmt dann das naechste.
    Es wegzuwerfen, nur weil das Feld fehlt, waere der teurere Fehler."""
    assert cu.tokens_from_credentials(_creds("tok-b")) == ["tok-b"]


def test_cli_token_auch_ohne_bekannten_container():
    """Das Dateiformat gehoert der CLI und ist nicht dokumentiert. Liegt das Token
    flach oder in snake_case, darf die Anzeige trotzdem nicht ausfallen."""
    assert cu.tokens_from_credentials({"accessToken": "flach"}) == ["flach"]
    assert cu.tokens_from_credentials(_creds("snake", key="claude_ai_oauth")) == ["snake"]
    assert cu.tokens_from_credentials({"claudeAiOauth": {"token": "alt"}}) == ["alt"]


def test_cli_token_muell_gibt_leere_liste():
    for muell in (None, {}, [], "text", {"claudeAiOauth": {}},
                  {"claudeAiOauth": {"accessToken": ""}}, {"claudeAiOauth": None}):
        assert cu.tokens_from_credentials(muell) == [], muell


def test_cli_credentials_pfad_folgt_der_umgebungsvariable():
    alt = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join("X:", "woanders")
        assert cu.cli_credentials_path() == os.path.join("X:", "woanders",
                                                         ".credentials.json")
        os.environ.pop("CLAUDE_CONFIG_DIR")
        assert cu.cli_credentials_path().endswith(
            os.path.join(".claude", ".credentials.json"))
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        if alt is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = alt


def test_beide_tokenquellen_werden_zusammengelegt():
    """Der Kern des Fallbacks: faellt EINE Quelle aus, traegt die andere. Erst wenn
    beide nichts liefern, ist es ein Fehler – und der nennt beide Quellen."""
    ruhe = cu._token_cache
    cli, desk = cu._read_tokens_from_cli, cu._read_tokens_from_disk
    try:
        cu._token_cache = []
        cu._read_tokens_from_cli = lambda: ["cli"]
        cu._read_tokens_from_disk = lambda: ["desk"]
        assert cu.read_oauth_token(force=True) == ["cli", "desk"]   # CLI zuerst

        def weg():
            raise FileNotFoundError("kein Claude Desktop")

        cu._read_tokens_from_disk = weg
        assert cu.read_oauth_token(force=True) == ["cli"]           # Desktop fehlt -> egal

        cu._read_tokens_from_cli = weg
        try:
            cu.read_oauth_token(force=True)
            assert False, "ohne jede Quelle muss NoTokenError fliegen"
        except cu.NoTokenError as e:
            assert "CLI" in str(e) and "Desktop" in str(e), str(e)
    finally:
        cu._read_tokens_from_cli, cu._read_tokens_from_disk = cli, desk
        cu._token_cache = ruhe


# ── chat_summary (Hover-Zusammenfassung) ─────────────────
def _line(rec):
    return json.dumps(rec)


def test_extract_turns():
    lines = [
        # echte getippte User-Frage
        _line({"type": "user", "message": {"role": "user", "content": "Bau Feature X"}}),
        # Assistant: nur die Text-Bloecke zaehlen, thinking/tool_use raus
        _line({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "Mache ich."},
            {"type": "tool_use", "name": "Bash", "input": {}}]}}),
        # User-Zug, dessen content eine LISTE ist = Tool-Ergebnis -> kein echter Text
        _line({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "total 5"}]}}),
        # Slash-Kommando-/System-Einschub (beginnt mit '<') -> raus
        _line({"type": "user", "message": {"role": "user", "content": "<command-name>/x"}}),
        # Caveat-Einschub -> raus
        _line({"type": "user", "message": {"role": "user", "content": "Caveat: nope"}}),
        # System-Zeile ohne message-dict -> ignoriert
        _line({"type": "system", "subtype": "info"}),
        "   ",            # Leerzeile
        "{kaputt",        # nicht-JSON -> uebersprungen
    ]
    turns = cs.extract_turns(lines)
    assert turns == [("user", "Bau Feature X"), ("assistant", "Mache ich.")]


def test_extract_turns_accepts_dicts():
    # extract_turns darf auch schon geparste dicts bekommen (nicht nur Roh-Strings)
    turns = cs.extract_turns([
        {"type": "user", "message": {"role": "user", "content": "Hallo"}}])
    assert turns == [("user", "Hallo")]


def test_build_digest_empty():
    assert cs.build_digest([]) == ""


def test_build_digest_topic_plus_recent():
    turns = [("user", "T" + "a" * 5)] + [("user", "u%d" % i) for i in range(1, 6)] \
            + [("assistant", "letzte Antwort")]
    d = cs.build_digest(turns, max_chars=60, per_turn=600)
    lines = d.splitlines()
    assert lines[0].startswith("User: T")          # erster Zug (Thema) IMMER dabei
    assert "…" in lines                             # Luecke, weil nicht alles reinpasst
    assert lines[-1] == "Assistant: letzte Antwort"  # juengster Zug dabei


def test_build_digest_no_gap_when_all_fit():
    turns = [("user", "Thema"), ("assistant", "ok"), ("user", "weiter")]
    d = cs.build_digest(turns, max_chars=3500)
    assert "\n…\n" not in d and not d.startswith("…")  # nichts ausgelassen -> keine Luecke
    assert d.splitlines() == ["User: Thema", "Assistant: ok", "User: weiter"]


def test_build_digest_per_turn_cap():
    d = cs.build_digest([("user", "x" * 900)], per_turn=100)
    assert d.startswith("User: ") and d.endswith("…") and len(d) <= 120


def test_clean_summary():
    assert cs.clean_summary("  Bottom-Bar bauen  ") == "Bottom-Bar bauen"
    assert cs.clean_summary('"Feature X umsetzen"') == "Feature X umsetzen"   # Quotes weg
    assert cs.clean_summary("„Deutsches Zitat“") == "Deutsches Zitat"          # dt. Quotes
    assert cs.clean_summary("Zusammenfassung: Y machen") == "Y machen"         # Praefix weg
    assert cs.clean_summary("erste Zeile\nzweite") == "erste Zeile"            # nur 1. Absatz
    assert cs.clean_summary("a\n\n  b") == "a"
    assert cs.clean_summary("") == "" and cs.clean_summary(None) == ""
    long = cs.clean_summary("z" * 300, max_len=50)
    assert long.endswith("…") and len(long) == 51


def test_i18n_normalize_and_L():
    assert i18n.normalize("english") == i18n.ENGLISH
    assert i18n.normalize("EN-us") == i18n.ENGLISH
    assert i18n.normalize("german") == i18n.GERMAN
    assert i18n.normalize(None) == i18n.GERMAN          # fehlend -> Deutsch
    assert i18n.normalize("klingon") == i18n.GERMAN     # unbekannt -> Deutsch
    prev = i18n._lang
    try:
        i18n._lang = i18n.ENGLISH
        assert i18n.L("Speichern", "Save") == "Save" and i18n.is_english()
        i18n._lang = i18n.GERMAN
        assert i18n.L("Speichern", "Save") == "Speichern" and not i18n.is_english()
    finally:
        i18n._lang = prev


def test_summary_instruction_language():
    # Die Modell-Anweisung folgt der Sprache; nur "english" -> englische Fassung.
    assert cs.instruction("english") is cs._INSTRUCTION_EN
    assert cs.instruction("german") is cs._INSTRUCTION_DE
    assert cs.instruction("klingon") is cs._INSTRUCTION_DE   # Fallback Deutsch
    assert "English sentence" in cs._INSTRUCTION_EN
    assert "deutschen Satz" in cs._INSTRUCTION_DE


def test_enc_cwd():
    # Umlaute/ß werden zu je einem '-' (darum "M--ig" aus "Müßig") – genau die
    # Kodierung, die Claude Code fuer ~/.claude/projects/<ordner> verwendet.
    assert cs.enc_cwd(r"C:\Users\Max Müßig\Projekte\agent-deck") \
        == "C--Users-Max-M--ig-Projekte-agent-deck"
    assert cs.enc_cwd("") == "" and cs.enc_cwd(None) == ""


# ── chat_summary: Ticketnummer aus dem Chat lesen ────────
def test_find_ticket_plain_key():
    assert cs.find_ticket([("user", "Wir machen PROJ-2691 fertig")], "PROJ") == "PROJ-2691"
    # ohne konfiguriertes Projekt trotzdem: jeder Key in Jira-Form zaehlt
    assert cs.find_ticket([("user", "bitte ABC-123 anschauen")]) == "ABC-123"
    assert cs.find_ticket([]) == "" and cs.find_ticket("kein Ticket hier") == ""


def test_find_ticket_ignores_tech_lookalikes():
    """UTF-8 & Co. sehen aus wie ein Key, sind aber keiner – sonst steht Muell im Hover."""
    noise = "UTF-8, SHA-256, ISO-8601, RFC-2119, CVE-2021, AES-256, GPT-5, python-3, top-10"
    assert cs.find_ticket([("user", noise)], "PROJ") == ""


def test_find_ticket_project_lowercase_and_bare_number():
    # das konfigurierte Projekt wird auch klein erkannt (Branch-/Pfadnamen) …
    assert cs.find_ticket([("user", "schau in ticket/proj-2691")], "PROJ") == "PROJ-2691"
    # … und eine blosse Nummer nach 'Ticket'/'Issue' bekommt den Projekt-Praefix
    assert cs.find_ticket([("user", "Ticket 2701 bitte")], "PROJ") == "PROJ-2701"
    assert cs.find_ticket([("user", "Issue #42 ist offen")], "PROJ") == "PROJ-42"
    # ohne Projekt-Key gibt es aus einer blossen Nummer nichts zu machen
    assert cs.find_ticket([("user", "Ticket 2701 bitte")]) == ""


def test_find_ticket_context_allows_single_digit():
    # einstellige Nummer nur mit 'Ticket …' davor (sonst waere UTF-8 wieder drin)
    assert cs.find_ticket([("user", "Ticket PROJ-1 bitte")]) == "PROJ-1"
    assert cs.find_ticket([("user", "der Wert PROJ-1 steht da")]) == ""


def test_find_ticket_picks_the_one_it_is_about():
    """Haeufigkeit schlaegt Reihenfolge: ein nebenbei erwaehnter Key gewinnt nicht."""
    turns = [("user", "nebenbei ABC-12"), ("assistant", "ok"),
             ("user", "eigentlich geht es um XYZ-77"), ("assistant", "XYZ-77, verstanden")]
    assert cs.find_ticket(turns) == "XYZ-77"
    # gleiche Punktzahl -> der ZULETZT erwaehnte gewinnt (das Gespraech ist weitergezogen)
    assert cs.find_ticket([("user", "erst ABC-11"), ("user", "jetzt ABC-22")]) == "ABC-22"
    # das konfigurierte Projekt sticht einen fremden Key derselben Haeufigkeit
    assert cs.find_ticket([("user", "PROJ-2691 vs FOO-2692")], "PROJ") == "PROJ-2691"


def test_find_ticket_needs_more_than_a_side_remark():
    """Eine EINMALIGE Nebenbei-Nennung des Agenten reicht nicht (min_score): lieber
    keine ID im Hover als eine falsche. Nennt der Nutzer sie (oder faellt sie mehrfach),
    steht sie da."""
    side = [("assistant", "das behebt uebrigens ABC-99")]
    assert cs.find_ticket(side) == ""
    assert cs.find_ticket(side + [("assistant", "ABC-99 ist damit durch")]) == "ABC-99"
    assert cs.find_ticket([("user", "mach ABC-99")]) == "ABC-99"


def test_find_ticket_no_key_inside_longer_code():
    # Regel-/Normkennungen wie Dockle "CIS-DI-0006" duerfen nicht als "DI-0006" durch
    assert cs.find_ticket([("user", "Dockle meldet CIS-DI-0006"),
                           ("user", "CIS-DI-0006 behoben")]) == ""


def test_find_ticket_robust_against_junk_turns():
    assert cs.find_ticket([None, ("user",), ("user", None), 42,
                           ("user", "ABC-123")]) == "ABC-123"


# ── chat_summary: PR-Nummer aus dem Chat lesen ───────────
def test_find_pr_keyword_and_url():
    assert cs.find_pr([("user", "Fix die Bugs aus PR #62")]) == "62"
    assert cs.find_pr([("user", "siehe https://github.com/acme/webapp/pull/128")]) == "128"
    assert cs.find_pr([("user", "pull request 903 reviewen")]) == "903"
    assert cs.find_pr([("user", "merge request 12 anschauen")]) == "12"
    assert cs.find_pr([("user", "nichts davon hier")]) == "" and cs.find_pr([]) == ""


def test_find_pr_bare_hash_needs_two_mentions():
    """Ein blosses '#62' kann alles sein (Issue, Kommentar) -> erst ab der zweiten
    Nennung glauben wir es; mit 'PR' davor reicht eine."""
    once = [("user", "schau dir #62 an")]
    assert cs.find_pr(once) == ""
    assert cs.find_pr(once + [("user", "#62 ist noch offen")]) == "62"
    assert cs.find_pr([("user", "schau dir PR #62 an")]) == "62"


def test_find_pr_ignores_non_pr_hashes():
    # 'Issue #42'/'Zeile #42' ist kein Pull Request; Hex-Farben schon gar nicht
    assert cs.find_pr([("user", "Issue #42 offen"), ("user", "Issue #42 noch offen")]) == ""
    assert cs.find_pr([("user", "in Zeile #42"), ("user", "Zeile #42 nochmal")]) == ""
    assert cs.find_pr([("user", "Farbe #6289ab"), ("user", "wieder #6289ab")]) == ""


def test_find_refs_can_return_both():
    refs = cs.find_refs([("user", "Bugs aus PR #62 zum Ticket PROJ-2651 fixen")], "PROJ")
    assert refs == {"ticket": "PROJ-2651", "pr": "62"}
    assert cs.find_refs([("user", "nur reden")], "PROJ") == {"ticket": "", "pr": ""}


# ── Hover-Tooltip/Karte: erkanntes Ticket + PR ───────────
def _tip_deck(cached=None, cached_summary=None, auto=None, bindings=None,
              worktrees=None):
    """Fake-Self mit den echten (ungebundenen) Tooltip-Methoden; chat_summary wird
    gemockt, damit kein Transcript/Cache auf der Platte noetig ist. bindings/worktrees
    speisen die Herkunftszeile (Repo · Fenster · Slot, siehe _origin_lines)."""
    from deck.ui import panel as ad
    f = type("F", (), {})()
    f._auto_refs = dict(auto or {})
    f.bindings = dict(bindings or {})
    f._worktrees = dict(worktrees or {})
    f._tip_refs = ad.AgentDeck._tip_refs.__get__(f)
    f._refs_label = ad.AgentDeck._refs_label
    f._origin_lines = ad.AgentDeck._origin_lines.__get__(f)
    f._tip_text = ad.AgentDeck._tip_text.__get__(f)
    orig = (ad.cs.cached_refs, ad.cs.cached_summary)
    ad.cs.cached_refs = lambda sid: dict(cached or {"ticket": "", "pr": ""})
    ad.cs.cached_summary = lambda sid: cached_summary
    return f, ad, orig


def test_tip_text_shows_detected_ticket_and_pr():
    f, ad, orig = _tip_deck(cached={"ticket": "PROJ-2691", "pr": "62"},
                            cached_summary="Bottom-Bar bauen")
    try:
        txt = f._tip_text({}, "sess-1")
        assert txt.splitlines()[0] == "Ticket: PROJ-2691 · PR #62"   # Bezug steht oben
        assert "Bottom-Bar bauen" in txt
        assert f._auto_refs["sess-1"]["pr"] == "62"    # gemerkt -> auch fuer die Karte
        # noch keine Zusammenfassung -> Bezug trotzdem sofort da, darunter der Platzhalter
        ad.cs.cached_summary = lambda sid: None
        txt = f._tip_text({}, "sess-1")
        assert txt.startswith("Ticket: PROJ-2691 · PR #62\n") and "wird erstellt" in txt
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_text_pr_only_and_none():
    f, ad, orig = _tip_deck(cached={"ticket": "", "pr": "62"}, cached_summary="Review")
    try:
        assert f._tip_text({}, "s") == "PR #62\nWorum es geht:\nReview"
        # gar kein Bezug -> exakt wie vorher, nur die Zusammenfassung
        f._auto_refs.clear()
        ad.cs.cached_refs = lambda sid: {"ticket": "", "pr": ""}
        assert f._tip_text({}, "s") == "Worum es geht:\nReview"
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_text_names_repo_window_and_slot():
    """Die Herkunft steht GANZ oben – sie ist beim Hovern die erste Frage, wenn mehrere
    Repos offen sind. Ohne Slot (Aufruf ohne Kachelbezug) bleibt der Text wie zuvor."""
    wt = os.path.join("C:", os.sep, "code", "agent-deck.wt", "abc-2691")
    f, ad, orig = _tip_deck(cached={"ticket": "", "pr": ""}, cached_summary="Review",
                            bindings={"A": "agent-deck"}, worktrees={"A2": wt})
    try:
        lines = f._tip_text({}, "s", "A2").splitlines()
        assert lines[0] == "agent-deck · Fenster A · A2"
        assert lines[1] == "↳ wt/abc-2691"       # der Agent sitzt NEBEN dem Repo
        # ohne worktree faellt die zweite Zeile weg
        assert f._tip_text({}, "s", "A1").splitlines()[1] == "Worum es geht:"
        # ohne gebundenes Repo bleibt nur der Fensterbuchstabe
        assert f._tip_text({}, "s", "B1").splitlines()[0] == "Fenster B · B1"
        # ohne Slot exakt wie vorher (kein leerer Kopf, keine Trennzeile)
        assert f._tip_text({}, "s") == "Worum es geht:\nReview"
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_tip_refs_prefers_memory_over_cache_file():
    f, ad, orig = _tip_deck(cached={"ticket": "ALT-1", "pr": ""},
                            auto={"sess-3": {"ticket": "NEU-2", "pr": ""}})
    try:
        assert f._tip_refs("sess-3")["ticket"] == "NEU-2"  # frisch gescannt > Cache-Datei
        assert f._tip_refs("")["ticket"] == ""
        # Gepatcht wird in hover, NICHT in panel: _tip_refs lebt im HoverMixin und
        # liest TICKET_AUTO aus dem Namensraum von deck/ui/hover.py. panel hat eine
        # eigene Bindung desselben Werts - ein Patch dort bliebe wirkungslos, und der
        # Test waere still gruen, ohne den Aus-Fall zu pruefen.
        from deck.ui import hover
        prev, hover.TICKET_AUTO = hover.TICKET_AUTO, False
        try:
            assert f._tip_refs("sess-3") == {"ticket": "", "pr": ""}   # Erkennung aus
        finally:
            hover.TICKET_AUTO = prev
    finally:
        ad.cs.cached_refs, ad.cs.cached_summary = orig


def test_refs_card_label_fits_the_narrow_line():
    from deck.ui import panel as ad
    L = ad.AgentDeck._refs_card_label
    assert L({"ticket": "PROJ-2691", "pr": "62"}) == "PROJ-2691 #62"   # beides, 13 Z.
    assert L({"ticket": "", "pr": "62"}) == "#62"
    assert L({"ticket": "PROJ-2691", "pr": ""}) == "PROJ-2691"
    assert L(None) == "" and L({}) == ""
    # zu lang fuer beides -> das Ticket gewinnt (dauerhafter als der PR)
    assert L({"ticket": "LONGPROJ-12345", "pr": "62"}) == "LONGPROJ-12345"
    # nur ein (zu langer) PR -> hart gekuerzt statt ueber das Effort zu laufen
    assert L({"ticket": "", "pr": "1234567890123456"}, max_chars=8) == "#123456…"


# ── claude_settings: Merge/Read/Mapping der 4 gesteuerten Keys ──────────────
def test_claude_settings_write_merges_and_preserves():
    """write_values darf NUR die vier Keys anfassen – Hooks, statusLine und vor allem
    permissions.allow muessen unangetastet bleiben (sonst zerschiesst das Deck die
    handgepflegte settings.json)."""
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="csettings_")
    p = os.path.join(base, "settings.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"hooks": {"Stop": [1]}, "statusLine": {"x": 1},
                       "model": "sonnet",
                       "permissions": {"allow": ["Read"], "defaultMode": "plan"}}, f)
        cset.write_values(model="opus[1m]", mode="auto", effort="xhigh",
                          language="english", path=p)
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        assert d["model"] == "opus[1m]"
        assert d["effortLevel"] == "xhigh"
        assert d["language"] == "english"
        assert d["permissions"]["defaultMode"] == "auto"
        assert d["hooks"] == {"Stop": [1]}                 # Fremd-Keys unangetastet …
        assert d["statusLine"] == {"x": 1}
        assert d["permissions"]["allow"] == ["Read"]       # … inkl. permissions.allow!
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_claude_settings_write_creates_file_and_is_partial():
    """Fehlende Datei/Ordner wird angelegt; None-Argumente lassen ihren Key weg
    (kein leeres permissions:{} wenn mode=None)."""
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="csettings2_")
    p = os.path.join(base, "nested", "settings.json")
    try:
        cset.write_values(model="fable", path=p)
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        assert d == {"model": "fable"}
        vals = cset.read_values(path=p)
        assert vals["model"] == "fable" and vals["mode"] is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_claude_settings_read_missing_is_safe():
    vals = cset.read_values(path=os.path.join(os.path.dirname(__file__), "nope.json"))
    assert vals == {"model": None, "mode": None, "effort": None,
                    "ultracode": False, "language": None}


def test_claude_settings_label_value_mapping():
    assert cset.value_to_label(cset.MODE_CHOICES, "acceptEdits") == "Accept Edits"
    assert cset.label_to_value(cset.MODE_CHOICES, "Plan") == "plan"
    # Modell-Fuzzy: nacktes "opus" und volle ID -> Opus(1M); "fable" -> Fable.
    assert cset.value_to_label(cset.MODEL_CHOICES, "opus", contains=True) == "Opus (1M)"
    assert cset.value_to_label(cset.MODEL_CHOICES, "claude-opus-5", contains=True) == "Opus (1M)"
    # Aeltere/kuenftige IDs derselben Reihe greifen genauso (Basis-Name entscheidet).
    assert cset.value_to_label(cset.MODEL_CHOICES, "claude-opus-4-8", contains=True) == "Opus (1M)"
    assert cset.value_to_label(cset.MODEL_CHOICES, "fable", contains=True) == "Fable"
    # Neue Aliasse (exakt).
    assert cset.value_to_label(cset.MODEL_CHOICES, "sonnet") == "Sonnet"
    assert cset.value_to_label(cset.MODEL_CHOICES, "haiku") == "Haiku"
    # Unbekannt/None -> erster Eintrag (Fallback).
    assert cset.value_to_label(cset.MODE_CHOICES, "bogus") == "Auto"
    assert cset.value_to_label(cset.LANG_CHOICES, "english", contains=True) == "Englisch"


def test_model_choices_stay_version_free():
    """Damit das Deck IMMER das neueste Modell startet, duerfen weder Wert noch Label
    eine Modellversion einfrieren: `claude --model` loest die nackten Aliasse
    ("opus", "fable", …) selbst auf das jeweils neueste Modell der Reihe auf. Eine
    feste ID ("claude-opus-4-8") oder ein Versions-Label ("Opus 4.8") wuerde beim
    naechsten Release still veralten – genau der Fall, den dieser Test verhindert."""
    import re
    for label, val in cset.MODEL_CHOICES:
        base = val.split("[")[0]                 # "opus[1m]" -> "opus"
        assert base in {"opus", "fable", "sonnet", "haiku"}, f"keine reine Alias-Angabe: {val!r}"
        assert not re.search(r"\d+[.\-]\d", val), f"Version im Wert: {val!r}"
        # Im Label ist "(1M)" (Kontextgroesse) erlaubt, "Opus 4.8"/"Fable 5" nicht.
        assert not re.search(r"[A-Za-z]\s+\d", label), f"Version im Label: {label!r}"


def test_claude_settings_effort_and_ultracode():
    # Label -> (effortLevel, ultracode)
    assert cset.effort_spec("max") == ("max", False)
    assert cset.effort_spec("Ultracode") == ("xhigh", True)
    assert cset.effort_spec("xhigh") == ("xhigh", False)
    assert cset.effort_spec("unbekannt") == ("xhigh", False)     # Fallback
    # (effortLevel, ultracode) -> Label; ultracode gewinnt.
    assert cset.effort_label("max", False) == "max"
    assert cset.effort_label("xhigh", True) == "Ultracode"
    assert cset.effort_label("high", False) == "high"
    assert cset.effort_label("bogus", False) == "xhigh"          # Fallback


def test_claude_settings_effort_ultracode_roundtrip():
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="csettings3_")
    p = os.path.join(base, "settings.json")
    try:
        cset.write_values(effort="xhigh", ultracode=True, path=p)
        v = cset.read_values(path=p)
        assert v["effort"] == "xhigh" and v["ultracode"] is True
        cset.write_values(effort="max", ultracode=False, path=p)      # Ultracode aus, max an
        v = cset.read_values(path=p)
        assert v["effort"] == "max" and v["ultracode"] is False
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_reenable_glow_pure_helpers():
    from deck.ops import vscode_glow as rg
    assert rg.fileurl_to_path("file:///C:/a%20b/x.css") == "C:/a b/x.css"
    assert rg.fileurl_to_path("C:/plain.css") == "C:/plain.css"
    imports = rg._extract_imports(
        '{"vscode_custom_css.imports": ["file:///C:/x.css", "file:///C:/y.js"]}')
    assert imports == ["file:///C:/x.css", "file:///C:/y.js"]
    assert rg._extract_imports("{}") is None
    # clear_existing entfernt einen Marker-Block restlos wieder.
    cleared = rg.clear_existing("<head>keep" + rg.START + "junk" + rg.END + "</head>")
    assert rg.START not in cleared and rg.END not in cleared and "keep" in cleared


# ── single_instance (Doppelstart-Guard) ──────────────────
def _patch_si(alive, focus_ret, restart_env=False):
    """single_instance fuer den Test isolieren: Lock UND Reveal-Marker in ein Temp-
    Verzeichnis legen, _pid_alive/focus_pid faken, RESTART_ENV setzen/loeschen. Gibt
    (si, restore, focus_calls) zurueck; restore() setzt am Ende alles zurueck."""
    import tempfile
    from deck.ops import instance as si
    focus_calls = []
    saved = {"LOCK_PATH": si.LOCK_PATH, "REVEAL_PATH": si.REVEAL_PATH,
             "_pid_alive": si._pid_alive,
             "focus_pid": si.wf.focus_pid, "env": os.environ.get(si.RESTART_ENV)}
    tmp = tempfile.mkdtemp()
    si.LOCK_PATH = os.path.join(tmp, "panel.lock")
    si.REVEAL_PATH = os.path.join(tmp, "panel.reveal")
    si._pid_alive = lambda pid: alive
    si.wf.focus_pid = lambda pid: (focus_calls.append(pid), focus_ret)[1]
    if restart_env:
        os.environ[si.RESTART_ENV] = "1"
    else:
        os.environ.pop(si.RESTART_ENV, None)

    def restore():
        si.LOCK_PATH = saved["LOCK_PATH"]
        si.REVEAL_PATH = saved["REVEAL_PATH"]
        si._pid_alive = saved["_pid_alive"]
        si.wf.focus_pid = saved["focus_pid"]
        if saved["env"] is None:
            os.environ.pop(si.RESTART_ENV, None)
        else:
            os.environ[si.RESTART_ENV] = saved["env"]

    return si, restore, focus_calls


def test_si_lock_roundtrip():
    si, restore, _ = _patch_si(alive=False, focus_ret=False)
    try:
        assert si._read_lock_pid() == 0                 # keine Datei -> 0
        si._write_lock()
        assert si._read_lock_pid() == os.getpid()       # eigene PID gelesen
        with open(si.LOCK_PATH, "w") as f:
            f.write("kein-int")
        assert si._read_lock_pid() == 0                 # Muell -> 0, nie Exception
    finally:
        restore()


def test_si_pid_alive_real():
    """Echter ctypes-Pfad: der eigene Prozess lebt, unsinnige PIDs nicht."""
    from deck.ops import instance as si
    assert si._pid_alive(os.getpid()) is True
    assert si._pid_alive(0) is False
    assert si._pid_alive(-1) is False


def test_si_takes_over_dead_lock():
    """Totes Lock (PID lebt nicht) -> uebernehmen (True, eigene PID), NICHT fokussieren."""
    si, restore, focus_calls = _patch_si(alive=False, focus_ret=True)
    try:
        with open(si.LOCK_PATH, "w") as f:
            f.write("424242")
        assert si.acquire_or_focus() is True
        assert si._read_lock_pid() == os.getpid()
        assert focus_calls == []
    finally:
        restore()


def test_si_defers_to_live_panel():
    """Lebendes Lock mit Fenster -> fokussieren + False (Zweit-Instanz beendet sich),
    Lock bleibt unveraendert. Zusaetzlich MUSS ein Reveal-Wunsch hinterlassen werden:
    angedockt ist das Panel eingeklappt, Fokus allein bliebe unsichtbar."""
    si, restore, focus_calls = _patch_si(alive=True, focus_ret=True)
    try:
        with open(si.LOCK_PATH, "w") as f:
            f.write("424242")
        assert si.acquire_or_focus() is False
        assert focus_calls == [424242]
        assert si._read_lock_pid() == 424242
        assert si.take_reveal_request() is True     # Wunsch liegt vor
    finally:
        restore()


def test_si_reveal_request_once():
    """Wunsch gilt genau einmal: nach dem Abholen ist der Marker weg."""
    si, restore, _ = _patch_si(alive=False, focus_ret=False)
    try:
        assert si.take_reveal_request() is False    # nichts da
        si.request_reveal()
        assert si.take_reveal_request() is True
        assert si.take_reveal_request() is False    # verbraucht
        assert not os.path.exists(si.REVEAL_PATH)
    finally:
        restore()


def test_si_reveal_request_stale_ignored():
    """Liegengebliebener Wunsch (harter Absturz) wird verworfen UND weggeraeumt –
    sonst klappt das Deck irgendwann grundlos auf."""
    si, restore, _ = _patch_si(alive=False, focus_ret=False)
    try:
        si.request_reveal()
        old = time.time() - si.REVEAL_MAX_AGE_S - 5
        os.utime(si.REVEAL_PATH, (old, old))
        assert si.take_reveal_request() is False
        assert not os.path.exists(si.REVEAL_PATH)
    finally:
        restore()


def test_si_fresh_start_clears_stale_reveal():
    """Uebernimmt diese Instanz das Lock, gehoert ein liegengebliebener Wunsch nicht
    ihr: er wird geraeumt, damit das frisch eingeklappte Deck nicht aufklappt."""
    si, restore, _ = _patch_si(alive=False, focus_ret=True)
    try:
        si.request_reveal()
        assert si.acquire_or_focus() is True
        assert si.take_reveal_request() is False
    finally:
        restore()


def test_si_live_pid_no_window_reclaims():
    """PID lebt, aber kein Panel-Fenster (recycelte PID) -> Lock uebernehmen (True)."""
    si, restore, focus_calls = _patch_si(alive=True, focus_ret=False)
    try:
        with open(si.LOCK_PATH, "w") as f:
            f.write("424242")
        assert si.acquire_or_focus() is True
        assert focus_calls == [424242]                  # Versuch gemacht …
        assert si._read_lock_pid() == os.getpid()       # … aber Lock uebernommen
    finally:
        restore()


def test_si_restart_env_claims_without_check():
    """RESTART_ENV gesetzt -> Lock direkt uebernehmen, Doppelstart-Pruefung ueberspringen
    (auch bei lebendem Fremd-Lock KEIN focus_pid)."""
    si, restore, focus_calls = _patch_si(alive=True, focus_ret=True, restart_env=True)
    try:
        with open(si.LOCK_PATH, "w") as f:
            f.write("424242")
        assert si.acquire_or_focus() is True
        assert si._read_lock_pid() == os.getpid()
        assert focus_calls == []
    finally:
        restore()


# ── edge_dock: Slide-Animation (pure Geometrie + Kurve) ──
def _dock(edge, target):
    """EdgeDock ohne __init__/Tk – reicht fuer die reine Slide-Mathematik."""
    d = object.__new__(ed.EdgeDock)
    d.edge = edge
    d._slide_target = target
    return d


def test_dock_slide_endpoints():
    """v=0 -> genau HANDLE_THICK ragt ueber den Rand (das Fenster startet dort, wo
    der Griff sass), v=1 -> auf dem Ziel, das EDGE_GAP vom Rand weg liegt. Beides
    exakt, sonst springt es. Die Ziele sind die, die _expanded_rect liefert –
    inklusive der EDGE_GAP-Einrueckung, sonst faende der Startstreifen den Rand nicht."""
    t, g = ed.handle_thick(), ed.EDGE_GAP
    d = _dock("left", (g, 100, 300, 200))
    assert d._slide_geom(0.0) == (t - 300, 100, 300, 200)
    assert d._slide_geom(1.0) == (g, 100, 300, 200)
    d = _dock("right", (1920 - 300 - g, 100, 300, 200))   # Screen 1920 breit
    assert d._slide_geom(0.0) == (1920 - t, 100, 300, 200)
    assert d._slide_geom(1.0) == (1920 - 300 - g, 100, 300, 200)
    d = _dock("top", (100, g, 300, 200))
    assert d._slide_geom(0.0) == (100, t - 200, 300, 200)
    assert d._slide_geom(1.0) == (100, g, 300, 200)


def test_dock_clip_covers_everything_beyond_the_edge():
    """Weggeschnitten wird MINDESTENS der Teil jenseits der Dockkante – bliebe auch
    nur ein Pixel stehen, faende es sich als Geisterbild auf dem Nachbar-Monitor
    wieder. Gerundet wird in CLIP_QUANT-Stufen (jede Aenderung kostet ein
    SetWindowRgn samt Neuzeichnen), und zwar nach OBEN: hoechstens knapp eine Stufe
    zu viel, nie eine zu wenig. Kante ist der Bildschirmrand (0), nicht das um
    EDGE_GAP eingerueckte Ziel."""
    for edge, target, axis in (("left", (ed.EDGE_GAP, 100, 300, 200), 0),
                               ("top", (100, ed.EDGE_GAP, 300, 200), 1)):
        d = _dock(edge, target)
        d._clip_on = True
        for i in range(41):
            v = i / 40.0
            beyond = max(0, -d._slide_geom(v)[axis])     # was links/oberhalb von 0 liegt
            assert beyond <= d._clip_for(v) < beyond + ed.CLIP_QUANT, (edge, v)
        assert d._clip_for(1.0) == 0                     # aufgeklappt -> Region weg
        d._clip_on = False                               # kein Nachbar-Monitor
        assert all(d._clip_for(i / 10.0) == 0 for i in range(11))


def _dock_rect(edge, win, screen=(1920, 1080), along=300):
    """EdgeDock ohne Tk fuer _expanded_rect: nur Bildschirmmasse + Inhaltsgroesse."""
    sw, sh = screen

    class _Root:
        def update_idletasks(self):
            pass

        def winfo_screenwidth(self):
            return sw

        def winfo_screenheight(self):
            return sh

        def winfo_reqwidth(self):
            return win[0]

        def winfo_reqheight(self):
            return win[1]

    d = object.__new__(ed.EdgeDock)
    d.edge = edge
    d.root = _Root()
    d._anchor = (along, along)
    d._last_size = win
    return d


def test_dock_expanded_rect_keeps_border_visible_on_all_four_sides():
    """Der selbst gezeichnete Cyan-Rand muss RUNDUM zu sehen sein. Windows 11 legt bei
    runden Ecken seinen eigenen Rand ueber die aeusserste Pixelreihe – buendig am
    Bildschirmrand fiel deshalb genau die Kante an der Dockseite optisch aus. Also
    haelt _expanded_rect an JEDER Kante mindestens EDGE_GAP Abstand: an der Dockkante
    exakt, quer dazu auch dann, wenn das Deck fast so hoch/breit wie der Schirm ist."""
    g, sw, sh = ed.EDGE_GAP, 1920, 1080
    for edge, win in (("left", (300, 200)), ("right", (300, 200)), ("top", (300, 200)),
                      ("left", (300, sh - 2 * g)),      # so hoch wie eben erlaubt
                      ("top", (sw - 2 * g, 200))):
        x, y, w, h = _dock_rect(edge, win)._expanded_rect()
        assert (x, y, w, h) == (x, y, win[0], win[1])   # Groesse bleibt der Inhalt
        assert x >= g and y >= g, (edge, win, x, y)     # links/oben Luft
        assert x + w <= sw - g and y + h <= sh - g, (edge, win, x + w, y + h)
        # An der Dockkante GENAU EDGE_GAP: mehr waere ein sichtbarer Spalt, weniger
        # verschluckte den Rand wieder.
        assert {"left": x, "right": sw - (x + w), "top": y}[edge] == g


def test_dock_slide_monotone_and_fixed_size():
    """Steigendes v laeuft nie zurueck (kein Zucken) und die GROESSE bleibt fest –
    animiert wird nur die Position, sonst gibt es Reflow-Flackern."""
    d = _dock("left", (0, 0, 300, 200))
    rects = [d._slide_geom(i / 40.0) for i in range(41)]
    xs = [r[0] for r in rects]
    assert all(b >= a for a, b in zip(xs, xs[1:]))
    assert {r[2:] for r in rects} == {(300, 200)}


def _spring_track(response_ms, dt_ms=10.0, steps=200, d0=-1.0, v0=0.0):
    """Eine Feder von d0 (Abstand zum Ziel) aus laufen lassen; liefert den Weg-
    Anteil 0..1 je Frame. Genau der Rechenweg, den _anim_step geht."""
    omega = 2.0 * math.pi / (response_ms / 1000.0)
    d, v, out = d0, v0, []
    for _ in range(steps):
        d, v = ed.EdgeDock._spring_at(d, v, omega, dt_ms / 1000.0)
        out.append(1.0 + d)                       # Ziel ist 1.0
    return out


def test_dock_spring_is_exact_regardless_of_step_size():
    """Die Feder wird ANALYTISCH gerechnet, nicht Schritt fuer Schritt integriert.
    Deshalb liefert sie nach derselben Zeit dasselbe Ergebnis, egal in wie vielen
    Frames man dort hinkommt – ein integrierendes Verfahren wuerde hier auseinander-
    laufen und bei grossem dt sogar explodieren. Genau das macht sie robust gegen
    ausgefallene Frames (Standby, blockiertes Tk)."""
    fein = _spring_track(220.0, dt_ms=1.0, steps=200)[-1]      # 200 ms in 200 Schritten
    grob = _spring_track(220.0, dt_ms=50.0, steps=4)[-1]       # 200 ms in 4 Schritten
    einer = _spring_track(220.0, dt_ms=200.0, steps=1)[-1]     # 200 ms in EINEM Schritt
    assert abs(fein - grob) < 1e-9 and abs(fein - einer) < 1e-9
    # Ein absurd grosses dt (eingeschlafener Rechner) landet sauber am Ziel.
    assert abs(_spring_track(220.0, dt_ms=60000.0, steps=1)[-1] - 1.0) < 1e-9


def test_dock_spring_never_overshoots():
    """Kritische Daempfung = kein Ueberschwingen. Ein Randpanel, das ueber sein Ziel
    hinausschiesst, wirkt wackelig – Overshoot gehoert zu Bewegungen, die der Nutzer
    mit Schwung angestossen hat, nicht zu einem Hover-Panel."""
    for response in (ed.COLLAPSE_RESPONSE_MS, ed.REVEAL_RESPONSE_MS):
        track = _spring_track(response)
        assert max(track) <= 1.0 + 1e-12, response
        assert all(b >= a - 1e-12 for a, b in zip(track, track[1:]))   # nie zurueck


def test_dock_spring_is_front_loaded_but_starts_from_rest():
    """Der Charakter der Bewegung: bei halber Zeit schon deutlich ueber halbem Weg
    (das ist der Unterschied zwischen 'reagiert' und 'laeuft ab' – smoothstep steht
    dort exakt bei 50 %), aber der erste Frame legt nur wenig zurueck. Genau daran
    war hier schon einmal ein cubic-ease-out gescheitert: sein Vollgas-Start ruckte
    sichtbar. Die Feder startet aus dem Stand."""
    track = _spring_track(ed.REVEAL_RESPONSE_MS)
    ende = next(i for i, x in enumerate(track) if x > 0.99)
    assert track[ende // 2] > 0.75                     # front-loaded
    assert track[0] < 0.05                             # kein Sprung im ersten Frame
    # Und sie ist in brauchbarer Zeit durch (nicht: kriecht ewig ans Ziel).
    assert 15 <= ende <= 35, ende                      # Frames a 10 ms


def test_dock_spring_is_not_slower_than_the_curve_it_replaced():
    """Die Feder darf sich nicht als Verlangsamung anfuehlen: nach 120 ms muss sie
    dort sein, wo die alte smoothstep-Kurve ueber 170 ms auch war (~90 %). Ihr
    Gewinn liegt DAVOR – die Halbzeit-Marke muss deutlich weiter sein."""
    def smoothstep(p):
        p = max(0.0, min(1.0, p))
        return p * p * (3.0 - 2.0 * p)

    feder = _spring_track(ed.REVEAL_RESPONSE_MS)
    assert feder[11] >= smoothstep(120 / 170.0) - 0.02      # 12 Frames = 120 ms
    assert feder[7] > smoothstep(80 / 170.0) + 0.20         # nach 80 ms klar voraus
    assert ed.COLLAPSE_RESPONSE_MS < ed.REVEAL_RESPONSE_MS  # Wegräumen zügiger


def test_dock_spring_reversal_is_velocity_continuous():
    """Beim Richtungswechsel wird nur das Ziel getauscht – die Geschwindigkeit laeuft
    weiter. Das Deck bremst also aus voller Fahrt ab, statt seine Kurve rueckwaerts
    abzuspulen: die Bewegung kehrt weich um und braucht dafuer nur so lange, wie der
    Restweg hergibt."""
    omega_auf = 2.0 * math.pi / (ed.REVEAL_RESPONSE_MS / 1000.0)
    omega_zu = 2.0 * math.pi / (ed.COLLAPSE_RESPONSE_MS / 1000.0)
    d, v = -1.0, 0.0
    for _ in range(8):                                  # 80 ms aufklappen
        d, v = ed.EdgeDock._spring_at(d, v, omega_auf, 0.010)
    pos_wechsel, v_wechsel = 1.0 + d, v
    assert 0.2 < pos_wechsel < 0.95 and v_wechsel > 0   # mitten in der Fahrt, nach aussen
    # Ziel jetzt 0.0 -> Abstand ist die Position selbst, Geschwindigkeit bleibt.
    # Fein abgetastet, denn die Traegheit spielt sich in wenigen Millisekunden ab:
    # die Feder laeuft noch ein STUECK weiter nach aussen, statt die Richtung im
    # selben Moment umzuklappen. Sichtbar ist das kaum (das Maximum liegt vor dem
    # ersten 10-ms-Frame) – gemeint ist es auch nicht als Effekt, sondern als Beleg,
    # dass die Geschwindigkeit stetig durch den Wechsel laeuft.
    d, v = pos_wechsel, v_wechsel
    fein = []
    for _ in range(600):
        d, v = ed.EdgeDock._spring_at(d, v, omega_zu, 0.001)
        fein.append(d)
    assert max(fein) > pos_wechsel                      # kein Vorzeichensprung
    assert max(fein) - pos_wechsel < 0.05               # aber auch kein Ausschlag
    # ... und danach sauber zurueck auf null, ohne durchzuschlagen.
    assert fein[-1] < 0.005 and min(fein) > -1e-9


# ── edge_dock: Haltefrist nach reveal_for_request ────────
def _dock_poll(now_ms, hold_until, pointer_inside=False):
    """EdgeDock ohne Tk, aufgeklappt, fuer _poll_once. Der Zeiger-Stub fliegt auf,
    wenn er waehrend der Haltefrist ueberhaupt befragt wird."""
    class _Pointer:
        def __init__(self, allowed):
            self.allowed = allowed

        def _p(self):
            if not self.allowed:
                raise AssertionError("Zeiger darf in der Haltefrist nicht zaehlen")
            return 0
        winfo_pointerx = winfo_pointery = _p

    d = object.__new__(ed.EdgeDock)
    d.edge = "right"
    d.expanded = True
    d._anim = None
    d._outside_since = 111          # "Zeiger war schon draussen" – darf die Frist nicht kippen
    d._hold_until = hold_until
    d._now_ms = lambda: now_ms
    d.app = None                    # getattr(_modal) -> False
    d._app_dragging = lambda: False
    d._pointer_in_window = lambda px, py: pointer_inside
    d.root = _Pointer(now_ms >= hold_until)
    d._collapsed = []
    d.collapse = lambda: d._collapsed.append(now_ms)
    return d


def test_dock_hold_blocks_collapse():
    """Waehrend der Haltefrist (von aussen aufgeklappt, Zeiger noch woanders) wird
    NICHT eingeklappt – sonst waere das Deck weg, bevor man hinsieht."""
    d = _dock_poll(now_ms=900, hold_until=1000)
    d._poll_once()
    assert d._collapsed == []
    assert d._outside_since is None      # Frist setzt zurueck -> volle Kulanz danach


def test_dock_hold_expires_with_full_delay():
    """Ganze Sequenz: waehrend der Frist gehalten, nach Fristende gilt wieder die
    normale Regel – erst Zeiger-draussen merken, einklappen erst COLLAPSE_DELAY_MS
    spaeter (kein schlagartiges Zuklappen im Moment des Fristendes)."""
    d = _dock_poll(now_ms=900, hold_until=1000)      # noch in der Frist
    d._poll_once()
    assert d._outside_since is None
    d._now_ms = lambda: 1000                          # Frist gerade abgelaufen
    d.root.allowed = True
    d._poll_once()
    assert d._collapsed == [] and d._outside_since == 1000
    d._now_ms = lambda: 1000 + ed.COLLAPSE_DELAY_MS
    d._poll_once()
    assert len(d._collapsed) == 1


def test_dock_hold_ignored_when_pointer_arrives():
    """Kommt der Zeiger aufs Deck, uebernimmt die normale Logik (kein Einklappen)."""
    d = _dock_poll(now_ms=2000, hold_until=0, pointer_inside=True)
    d._poll_once()
    assert d._collapsed == [] and d._outside_since is None


# ── edge_dock: Robustheit der Slide-Zustandsmaschine ─────
# Der eine Zustand, den es nicht geben darf, ist ein halb ausgefahrenes Deck. Die
# Tests hier stellen genau die Stoerungen nach, die frueher dazu fuehrten: eine
# Bewegung, deren Frames ausbleiben, und eine, deren geometry() nicht durchgeht.
_ANIM_TARGET = (2, 100, 300, 200)


def _dock_anim(edge="left"):
    """EdgeDock ohne Tk fuer die Slide-Zustandsmaschine. Uhr, geometry() und after()
    sind von aussen steuerbar, damit sich Frame-Ausfaelle und Fehler nachstellen
    lassen; die Haltegriffe (1-ms-Timer, Kachel-Animator) werden nur GEZAEHLT –
    getestet wird ihre Paarigkeit, nicht ihre Wirkung."""
    clock = [1000.0]

    class _Root:
        def __init__(self):
            self.geoms = []
            self.jobs = []
            self.fail = False

        def geometry(self, spec):
            if self.fail:
                raise ed.tk.TclError("Fenster gerade weg")
            self.geoms.append(spec)

        def after(self, ms, fn):
            self.jobs.append(fn)
            return "job%d" % len(self.jobs)

        def after_cancel(self, job):
            pass

        def update_idletasks(self):
            pass

        # _settle_expanded misst hier immer "steht am Ziel"
        def winfo_rootx(self):
            return _ANIM_TARGET[0]

        def winfo_rooty(self):
            return _ANIM_TARGET[1]

        def winfo_width(self):
            return _ANIM_TARGET[2]

        def winfo_height(self):
            return _ANIM_TARGET[3]

    d = object.__new__(ed.EdgeDock)
    d.app = None
    d.edge = edge
    d.root = _Root()
    d.handle = None                 # -> _flash_border haelt sich raus
    d.expanded = False
    d._anim = None
    d._slide_target = _ANIM_TARGET
    d._retarget = False
    d._clip_on = False
    d._clip_px = 0
    d._outside_since = None
    d._reveal_lock = 0
    d._last_size = _ANIM_TARGET[2:]
    d.clock = clock
    d._now_ms = lambda: clock[0]
    d.held = [0]
    d._anim_hold = lambda: d.held.__setitem__(0, d.held[0] + 1)
    d._anim_release = lambda: d.held.__setitem__(0, d.held[0] - 1)
    d._reassert_topmost = lambda: None
    d.collapsed = []
    d._collapse_now = lambda: d.collapsed.append(True)
    return d


def _run_frames(d, step_ms=10.0, limit=200):
    """Die eingeplanten Frames abarbeiten und dabei die Uhr weiterdrehen."""
    n = 0
    while d.root.jobs and n < limit:
        d.clock[0] += step_ms
        d.root.jobs.pop()()
        n += 1
    return n


def test_dock_anim_reaches_target_and_releases():
    """Regulaerer Durchlauf: das Deck landet EXAKT auf dem Ziel, die Animation ist
    danach beendet und beide Haltegriffe (1-ms-Timer, Kachel-Animator) sind wieder
    freigegeben. Ein nicht freigegebener Haltegriff liesse den Prozess im schnellen
    Timer-Takt und die Kacheln fuer immer eingefroren zurueck."""
    d = _dock_anim()
    d._anim_to(+1)
    assert d.held[0] == 1                      # gehalten, solange es laeuft
    _run_frames(d)
    assert d._anim is None and d.expanded is True
    assert d.held[0] == 0
    x, y, w, h = _ANIM_TARGET
    assert d.root.geoms[-1].endswith(f"+{x}+{y}")


def test_dock_anim_only_first_frame_carries_size():
    """Die Groesse steht waehrend des Slides fest und geht nur in den ERSTEN Frame.
    Jedes weitere WxH+X+Y triebe Tk je Frame durch seinen Geometry-Manager – das ist
    Arbeit zwischen zwei Frames, und die sieht man als Ruckeln."""
    d = _dock_anim()
    d._anim_to(+1)
    _run_frames(d)
    assert "x" in d.root.geoms[0].split("+")[0]          # "300x200+..."
    assert all(g.startswith("+") for g in d.root.geoms[1:])
    assert len(d.root.geoms) > 3                          # es lief wirklich animiert


def test_dock_anim_deadline_snaps_to_target():
    """Notbremse: kommen die Frames nicht mehr (Tk blockiert, Timer verschluckt),
    springt die Bewegung ans Ziel statt auf halber Strecke stehenzubleiben."""
    d = _dock_anim()
    d._anim_to(+1)
    d._anim["deadline"] = d.clock[0] + 1        # Frist laeuft sofort ab
    d.clock[0] += 2
    d.root.jobs.pop()()                          # ein einziger, verspaeteter Frame
    assert d._anim is None and d.expanded is True
    assert d.held[0] == 0
    x, y, _w, _h = _ANIM_TARGET
    assert d.root.geoms[-1].endswith(f"+{x}+{y}")
    assert not d.root.jobs                       # kein Frame mehr eingeplant


def test_dock_anim_geometry_error_still_finishes():
    """Nimmt Tk die Geometrie nicht an (Fenster gerade weg oder neu gebaut), endete
    die Animation frueher einfach – das Deck blieb sichtbar auf halber Strecke
    stehen. Jetzt wird der Endzustand trotzdem hergestellt."""
    d = _dock_anim()
    d._anim_to(+1)
    d.root.fail = True
    d.clock[0] += 10
    d.root.jobs.pop()()
    assert d._anim is None and d.expanded is True and d.held[0] == 0


def test_dock_anim_watchdog_recovers_lost_frame_timer():
    """Verschluckt Tk den eingeplanten Frame (modaler Dialog, fremdes update()), kaeme
    nie wieder einer. Der Poll laeuft unabhaengig weiter und ist die einzige Instanz,
    die das bemerken kann – er holt die Bewegung ans Ziel."""
    d = _dock_anim()
    d._anim_to(+1)
    d.root.jobs.clear()                          # der Frame ist weg
    d._anim["job"] = None
    d._anim_watchdog()
    assert d._anim is None and d.expanded is True and d.held[0] == 0


def test_dock_anim_reverse_keeps_hold_and_motion():
    """Richtungswechsel mitten in der Bewegung: Position UND Geschwindigkeit werden
    uebernommen (sonst springt das Deck bzw. knickt seine Bewegung ab), und die
    Haltegriffe fallen dabei NICHT auf null – sonst gaebe es ein
    timeEndPeriod/timeBeginPeriod-Pingpong mitten im Slide."""
    d = _dock_anim()
    d._anim_to(+1)
    d.clock[0] += 60
    d.root.jobs.pop()()
    pos_mid, vel_mid = d._anim["pos"], d._anim["vel"]
    assert 0.0 < pos_mid < 1.0 and vel_mid > 0
    d._anim_to(-1)
    assert d._anim["pos"] == pos_mid and d._anim["vel"] == vel_mid
    assert d._anim["dir"] == -1 and d._anim["target"] == 0.0
    assert d.held[0] == 1
    _run_frames(d)
    assert d._anim is None and d.collapsed == [True] and d.held[0] == 0


def test_dock_edge_switch_during_slide_leaves_defined_state():
    """Rand wechseln, waehrend das Deck gerade herausgleitet: den Slide nur
    abzubrechen liesse das Fenster auf halber Strecke stehen – und ZWAR OHNE GRIFF,
    denn den hat reveal() beim Losfahren versteckt. Das Deck waere weder zu sehen
    noch hervorzuholen (angedockt gibt es keine Titelleiste). Also muss ein
    definierter Zustand herauskommen."""
    d = _dock_anim()
    d.app = type("A", (), {"settings": {}, "store": None})()
    d.app.settings = {}
    d._save_settings = lambda: None
    d._reposition_expanded = lambda: d.collapsed.append("repos")
    d._position_handle = lambda: None
    d._clear_clip = lambda: None
    d._anim_to(+1)
    d.clock[0] += 60
    d.root.jobs.pop()()                          # mitten in der Bewegung
    assert d._anim is not None
    d.set_edge("top")
    assert d._anim is None and d.held[0] == 0    # Haltegriff freigegeben
    assert d.expanded is True                    # war am Aufklappen -> gilt als offen
    assert d.collapsed == ["repos"]              # und wurde neu ausgerichtet


def test_dock_resize_during_slide_is_deferred():
    """Ein Inhalts-Resize (Agent kommt/geht) darf das Ziel nicht MITTEN in der
    Bewegung verschieben – das Deck spraenge sichtbar. Gemerkt und danach nachgezogen."""
    d = _dock_anim()
    d._anim_to(+1)
    before = d._slide_target
    d.on_resized()
    assert d._slide_target == before and d._retarget is True


# ── edge_dock: Aufklappen auch ohne Maus-Ereignis ────────
def _dock_hover(pointer, along=300, shown=True, lock=0, now=1000.0):
    """EdgeDock ohne Tk fuer _poll_reveal: Griff links, Zeiger frei setzbar."""
    class _Root:
        def winfo_pointerx(self):
            return pointer[0]

        def winfo_pointery(self):
            return pointer[1]

        def winfo_screenwidth(self):
            return 1920

        def winfo_screenheight(self):
            return 1080

    d = object.__new__(ed.EdgeDock)
    d.edge = "left"
    d.root = _Root()
    d._drag = None
    d.handle = object()
    d._handle_shown = shown
    d._reveal_job = None
    d._reveal_lock = lock
    d._now_ms = lambda: now
    d._anchor = (0, along)
    d._last_size = (300, 200)
    d._handle_drawn = (ed.handle_thick(), 0)
    d.revealed = []
    d.reveal = lambda: d.revealed.append(True)
    return d


def test_dock_poll_reveals_without_mouse_event():
    """Der Griff ist ein rahmenloses Topmost-Fenster, das beim Ein-/Ausklappen durch
    withdraw/deiconify geht. Taucht er unter einem STEHENDEN Zeiger auf, schickt
    Windows kein Mausereignis – Tk feuert weder <Enter> noch <Motion>, und frueher
    tat sich dann gar nichts ('klappt nicht auf'). Der Poll muss das auffangen."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    d = _dock_hover((hx + hw // 2, hy + 4))      # oberes Ende, ausserhalb der Zieh-Zone
    d._poll_reveal()
    assert d.revealed == [True]


def test_dock_poll_leaves_grip_zone_alone():
    """Im unsichtbaren POLSTER neben der Kapsel wird gegriffen, NICHT aufgeklappt –
    sonst waere der Griff im Moment des Zufassens schon weg. Die Mitte der Laenge ist
    dagegen ganz normale Kapsel: dort MUSS es aufklappen (frueher war es umgekehrt)."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    im_polster = _dock_hover((hx + hw - 2, hy + hh // 2))
    im_polster._poll_reveal()
    assert im_polster.revealed == []
    auf_kapsel = _dock_hover((hx + 2, hy + hh // 2))     # Mitte der Laenge, an der Kante
    auf_kapsel._poll_reveal()
    assert auf_kapsel.revealed == [True]


def test_dock_grip_zone_is_the_invisible_pad_on_every_edge():
    """Die Zonengrenze laeuft QUER zum Griff und haengt an capsule_extent(): bis dahin
    Kapsel (Hover klappt auf), dahinter Polster (Greifen). Bei „rechts" liegt die
    Dockkante am ANDEREN Ende, dort muss gespiegelt gerechnet werden – sonst laege die
    Greif-Zone auf dem Leuchten und das Aufklappen im Unsichtbaren."""
    thick, ext = ed.handle_thick(), ed.capsule_extent()
    assert 0 < ext < thick                    # es gibt ueberhaupt ein Polster

    class _Ev:
        def __init__(self, x, y):
            self.x, self.y = x, y

    def dock(edge):
        d = object.__new__(ed.EdgeDock)
        d.edge = edge
        d._handle_drawn = (thick, 200) if edge != "top" else (200, thick)
        return d

    links = dock("left")
    assert links._in_grip(_Ev(thick - 1, 100)) is True      # innen = Polster
    assert links._in_grip(_Ev(1, 100)) is False             # an der Kante = Kapsel
    rechts = dock("right")
    assert rechts._in_grip(_Ev(0, 100)) is True             # bei rechts ist innen LINKS
    assert rechts._in_grip(_Ev(thick - 2, 100)) is False
    oben = dock("top")
    assert oben._in_grip(_Ev(100, thick - 1)) is True       # quer laeuft hier ueber y
    assert oben._in_grip(_Ev(100, 1)) is False


def test_dock_poll_reveal_off_handle_and_locked():
    """Neben dem Griff passiert nichts – und direkt nach dem Einklappen sperrt die
    Anti-Flatter-Frist den Poll-Weg, damit ein zufaellig dort liegender Zeiger das
    Deck nicht im selben Atemzug wieder aufreisst."""
    hx, hy, hw, hh = _dock_hover((0, 0))._handle_geom()
    off = _dock_hover((hx + hw + 50, hy + 4))
    off._poll_reveal()
    assert off.revealed == []
    locked = _dock_hover((hx + hw // 2, hy + 4), lock=2000.0, now=1000.0)
    locked._poll_reveal()
    assert locked.revealed == []
    hidden = _dock_hover((hx + hw // 2, hy + 4), shown=False)   # aufgeklappt
    hidden._poll_reveal()
    assert hidden.revealed == []


# ── screen_fit: Tooltip/Dialog bleibt auf dem Monitor ────
# Arbeitsflaeche wie ein 1920x1080-Schirm mit 40 px Taskleiste unten.
_AREA = (0, 0, 1920, 1040)


def test_fit_prefers_the_offset_position():
    """Solange Platz ist, sitzt das Fenster genau dort, wo es gedacht ist:
    Anker + Versatz. Kein Klemmen, kein Klappen."""
    assert sf.fit(500, 400, 300, 120, _AREA, dx=14, dy=18) == (514, 418)


def test_fit_flips_around_the_anchor_at_the_edges():
    """Am rechten/unteren Rand klappt das Fenster auf die ANDERE Seite des Ankers –
    beim rechts angedockten Deck ist genau das der Normalfall. Es wird NICHT nur an
    den Rand geschoben: dort laege der Tooltip unter dem Mauszeiger und verdeckte die
    Kachel, auf die er sich bezieht."""
    assert sf.fit(1900, 400, 300, 120, _AREA, dx=14, dy=18) == (1900 - 14 - 300, 418)
    assert sf.fit(500, 1030, 300, 120, _AREA, dx=14, dy=18) == (514, 1030 - 18 - 120)
    x, y = sf.fit(1900, 1030, 300, 120, _AREA, dx=14, dy=18)   # Ecke: beide Achsen
    assert (x, y) == (1586, 892)


def test_fit_clamps_when_flipping_does_not_help_either():
    """Passt es auch gespiegelt nicht (Fenster fast so breit wie der Schirm, Anker
    mittig), wird geklemmt – und zwar so, dass die linke/obere Kante sichtbar bleibt:
    dort sitzen Titel und Beschriftungen."""
    assert sf.fit(1000, 500, 1900, 1000, _AREA, dx=30, dy=60) == (20, 40)
    # Groesser als der Schirm -> Anschlag links/oben, nicht ins Negative.
    assert sf.fit(1000, 500, 3000, 2000, _AREA, dx=30, dy=60) == (0, 0)


def test_fit_never_leaves_the_work_area_on_a_grid_of_anchors():
    """Rundumprobe: fuer jede Anker-Position auf dem Schirm liegt das Fenster
    vollstaendig in der Arbeitsflaeche (solange es hineinpasst) – auch mit negativen
    Koordinaten, wie sie ein Monitor LINKS des Hauptschirms hat."""
    for area in (_AREA, (-1920, 232, -384, 1144)):
        l, t, r, b = area
        for ax in range(l, r + 1, 97):
            for ay in range(t, b + 1, 61):
                x, y = sf.fit(ax, ay, 320, 140, area, dx=14, dy=18)
                assert l <= x and x + 320 <= r, (area, ax, ay, x)
                assert t <= y and y + 140 <= b, (area, ax, ay, y)


def test_fit_without_a_known_area_stays_unclamped():
    """Kein Windows/kein Monitor-Info -> work_area() liefert None. Dann wird BEWUSST
    nicht geklemmt: eine geratene Bildschirmgroesse (winfo_screenwidth = nur der
    Primaerschirm) zog den Tooltip auf dem zweiten Monitor auf den falschen Schirm."""
    assert sf.fit(2500, 900, 300, 120, None, dx=14, dy=18) == (2514, 918)


def test_work_area_is_a_plausible_rect_or_none():
    """work_area() darf nie halb Gares liefern: entweder ein echtes Rechteck oder
    None. Auf dem Entwicklungsrechner (Windows) kommt der Hauptmonitor."""
    a = sf.work_area(10, 10)
    assert a is None or (a[0] < a[2] and a[1] < a[3])


# ── handle_render: Neonröhre mit Alphakanal (reine Bildrechnung, kein Tk) ─
# Getestet wird handle_rgba/handle_bits – beides braucht kein Fenster. Was NICHT
# hierher kann, ist das Schieben ins Fenster selbst (win_focus.layered_push, reines
# Win32); das prüft der Screenshot-Durchlauf. Fehlt Pillow, gibt es nichts zu
# prüfen – edge_dock fällt dann auf den Linien-Pfad zurück.
HR_W, HR_TUBE, HR_LEN = 26, 16, 160      # Fenster 26 px = Röhre 16 + Hof-Luft 10


def _hr(edge="left", color="#ffc48a", eff=1.0, hot=False):
    w, h = (HR_LEN, HR_W) if edge == "top" else (HR_W, HR_LEN)
    return hrender.handle_rgba(w, h, edge, HR_TUBE, color, eff, hot=hot)


def _hr_light(img):
    """Mittleres AUSGESTRAHLTES Licht: Helligkeit mit der Deckung gewichtet. Nur die
    Farbe zu messen wäre irreführend – ein Pixel mit Alpha 0 leuchtet nicht, egal
    welche Farbe darunter steht."""
    px = list(img.getdata())
    return sum((r + g + b) * a for r, g, b, a in px) / float(len(px) * 255)


def test_handle_has_no_box_around_it():
    """Die Zusage des Entwurfs: um die Kapsel herum ist NICHTS zu SEHEN – kein Kasten,
    kein Saum. Geprüft an den vier Ecken UND an den drei freien Rändern des Fensters:
    sind die leer, ist der Bloom nirgends abgeschnitten. Der vierte Rand (die Dockkante)
    darf Licht tragen – dort schneidet der Bildschirm selbst ab, wie in der Vorlage.

    „Leer" heißt HIT_ALPHA, nicht 0: das Polster ist die Zieh-Zone und muss Mausereignisse
    bekommen, und dafür darf das Alpha nicht auf 0 fallen (sonst klickt es durch). Ein
    Alpha von 1 von 255 ist unsichtbar, aber anfassbar."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    floor = hrender.HIT_ALPHA
    for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        assert img.getpixel(p)[3] <= floor, f"Ecke {p} ist sichtbar"
    assert all(img.getpixel((w - 1, y))[3] <= floor for y in range(h)), "Innenkante"
    assert all(img.getpixel((x, 0))[3] <= floor for x in range(w)), "obere Kante"
    assert all(img.getpixel((x, h - 1))[3] <= floor for x in range(w)), "untere Kante"
    assert max(a for _r, _g, _b, a in img.getdata()) == 255   # die Kapsel deckt voll


def test_handle_pad_stays_clickable():
    """Der Maus-Hit-Test eines layered Fensters folgt dem ALPHA: wo es 0 ist, klickt man
    durch und es kommt kein Ereignis an. Das Polster ist aber genau die Greif-Zone zum
    Verschieben – KEIN Pixel des Griffs darf also ganz auf 0 fallen."""
    if not hrender.AVAILABLE:
        return
    assert hrender.HIT_ALPHA >= 1
    for edge in ("left", "right", "top"):
        assert min(a for _r, _g, _b, a in _hr(edge).getdata()) >= hrender.HIT_ALPHA, edge


def test_handle_capsule_sits_where_the_template_had_it():
    """Die Kapsel schwebt mit kleinem Abstand von der Dockkante (wie in der Vorlage),
    ist quer voll deckend und lässt zur Fenstermitte hin Platz für den Bloom."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    y = h // 2
    # Schwelle 200, nicht 255: die Flanken der Kapsel sind ANTIALIASED, die äußersten
    # Spalten decken also absichtlich nicht voll. Genau dafür wird das Bild in
    # vierfacher Auflösung gezeichnet.
    solid = [x for x in range(w) if img.getpixel((x, y))[3] >= 200]
    assert solid, "keine deckende Kapsel gefunden"
    assert 1 <= solid[0] <= 5                      # kleiner Abstand zur Dockkante
    assert len(solid) >= HR_TUBE - 2               # Kapsel in voller Breite
    assert solid[-1] < w - 3                       # innen bleibt Luft fuer den Bloom


def test_handle_idle_stays_dim():
    """Bei idle soll der Griff findbar bleiben, aber nicht leuchten wie eine Rückfrage.

    Die Schwelle ist bewusst nicht scharf: der KÖRPER behält seine Deckung mit Absicht
    (eingeklappt ist er die einzige Bedienfläche), gedämpft werden Kern und Hof. Ein
    guter Teil des Unterschieds liegt ausserdem in der FARBE – idle-Grau ist
    unauffällig, ohne dunkler zu sein – und das kann diese Messung nicht sehen."""
    if not hrender.AVAILABLE:
        return
    idle = _hr_light(_hr(color="#8b8b99", eff=0.45))
    wait = _hr_light(_hr(color="#ffc48a", eff=1.0))
    assert idle < wait * 0.7
    assert idle > 0                                # aber nicht unsichtbar


def test_handle_body_is_brighter_in_the_middle():
    """Der Kapselkörper trägt einen Längs-Verlauf: in der Mitte heller, zu den Enden
    hin zurückgenommen. Das ist es, was ihn als Körper und nicht als Farbfliese lesen
    lässt – gemessen INNERHALB der Kapsel, damit der Bloom nicht mitmisst."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    h = img.size[1]
    x = int(HR_TUBE * hrender.OUT) + HR_TUBE // 2
    mid = sum(img.getpixel((x, h // 2))[:3])
    near_end = sum(img.getpixel((x, int(h * 0.12)))[:3])
    assert mid > near_end * 1.05
    assert img.getpixel((x, h // 2))[3] == 255      # trotzdem voll deckend


def test_handle_hot_and_flash_brighten():
    """Zeiger auf dem Griff und Aufblitzen müssen heller sein.

    Der Hover wird an der KAPSEL gemessen, nicht am Bildmittel: er wirkt vor allem auf
    den Körper, und im Mittel über das ganze Fenster (das grösstenteils durchsichtig
    ist) verschwindet das unter 5 %. Genau daran wäre die Messung fast vorbeigelaufen –
    die Vorlage hebt unter dem Zeiger nur den Weissanteil des Mitten-Highlights, und
    das sind gemessen 2 % Licht, also nichts.

    Das Aufblitzen darf am Mittel gemessen werden: dort zieht auch der Bloom auf, und
    das ist gerade der Sinn – es soll im Augenwinkel auffallen."""
    if not hrender.AVAILABLE:
        return
    x, y = int(HR_TUBE * hrender.OUT) + HR_TUBE // 2, HR_LEN // 2
    body = sum(_hr().getpixel((x, y))[:3])
    assert sum(_hr(hot=True).getpixel((x, y))[:3]) > body * 1.03
    assert _hr_light(_hr(eff=1.9)) > _hr_light(_hr()) * 1.10


def test_handle_edge_orientation():
    """Die Röhre klebt an JEDER Kante am Bildschirmrand: rechts andocken spiegelt das
    kanonische (linke) Bild, oben andocken dreht es um 90 Grad im Uhrzeigersinn –
    dabei wird aus der linken Spalte die obere Zeile."""
    if not hrender.AVAILABLE:
        return
    from PIL import Image
    left, right, top = _hr("left"), _hr("right"), _hr("top")
    assert list(right.getdata()) == list(left.transpose(Image.FLIP_LEFT_RIGHT).getdata())
    assert top.size == (HR_LEN, HR_W)
    assert list(top.getdata()) == list(left.transpose(Image.ROTATE_270).getdata())


def test_handle_has_a_glass_edge_toward_the_dock_side():
    """Die Glaskante liegt IM Körper, auf der Seite zur Dockkante – sie ist das Detail,
    das die Kapsel wie Glas und nicht wie Plastik aussehen lässt. Also muss es dort
    eine hellere Spalte geben als in der Kapselmitte."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    y = img.size[1] // 2
    x0 = int(HR_TUBE * hrender.OUT)
    inner = [sum(img.getpixel((x0 + d, y))[:3]) for d in range(HR_TUBE)]
    sheen = max(inner[:HR_TUBE // 3])              # helle Spalte auf der Dockseite
    plain = inner[HR_TUBE // 2]                    # Kapselmitte
    assert sheen > plain


def test_handle_bits_are_premultiplied_bgra():
    """UpdateLayeredWindow will BGRA mit VORMULTIPLIZIERTEM Alpha. Ohne die
    Vormultiplikation bekommen die weichen Kanten einen hellen Saum – und wo Alpha 0
    ist, MÜSSEN alle Kanäle 0 sein, sonst leuchtet dort ein Rest."""
    if not hrender.AVAILABLE:
        return
    img = _hr()
    w, h = img.size
    bits = hrender.handle_bits(w, h, "left", HR_TUBE, "#ffc48a", 1.0)
    assert len(bits) == w * h * 4
    for i in range(0, len(bits), 4):
        b, g, r, a = bits[i:i + 4]
        assert max(b, g, r) <= a     # vormultipliziert: kein Kanal ueber dem Alpha
    # Oben links = Ecke: unsichtbar (keine Farbe), aber mit HIT_ALPHA anfassbar. Bei
    # vormultipliziertem Alpha heisst unsichtbar zwangslaeufig auch farblos.
    assert bits[0:3] == b"\x00\x00\x00" and bits[3] == hrender.HIT_ALPHA
    # BGRA, nicht RGBA: Amber (255,196,138) hat mehr Rot als Blau, im Puffer steht
    # Blau also VOR Rot – sonst waere der Griff blau statt amber. Gemessen in der
    # KAPSEL, nicht im Bloom: dort ist die Farbe unverwaessert.
    x = int(HR_TUBE * hrender.OUT) + HR_TUBE // 2
    mid = ((h // 2) * w + x) * 4
    assert bits[mid + 2] > bits[mid]


def test_handle_breathing_is_a_ramp_not_a_staircase():
    """Der Griff atmet nur zwischen 60 und 100 % Leuchtkraft – ein schmaler Weg. Wird
    die Leuchtkraft für den Bild-Cache zu grob gerastert (_qe), fällt er in wenige
    Stufen: mit dem von den Kacheln übernommenen 0.07er-Schritt kamen gemessen ganze
    SECHS verschiedene Bilder je Atemzug heraus, eine Stufe stand 605 ms unverändert.
    Das Atmen war damit keine Rampe, sondern eine Treppe – und sah genau so aus.

    Geprüft wird deshalb am fertigen Cache-Schlüssel, dass ein Atemzug für die MEHRHEIT
    seiner Frames ein neues Bild ergibt und keine Stufe merklich stehen bleibt. Dazu die
    Dauer des Atemzugs selbst: sie hängt an NEON_MS × NEON_PULSE_TICKS, und wer am Takt
    dreht, muss die Tickzahl mitziehen – sonst atmet der Griff plötzlich schneller."""
    d = object.__new__(ed.EdgeDock)
    d._glow_int, d._glow_pulse, d._bloom = 1.0, True, 0.0
    keys = []
    for i in range(ed.NEON_PULSE_TICKS):
        d._pulse_i = i
        keys.append(round(hrender._qe(d._eff_intensity()), 6))
    assert len(set(keys)) >= ed.NEON_PULSE_TICKS * 0.3, len(set(keys))
    laengste, lauf = 1, 1
    for a, b in zip(keys, keys[1:]):
        lauf = lauf + 1 if a == b else 1
        laengste = max(laengste, lauf)
    assert laengste * ed.NEON_MS <= 200, laengste * ed.NEON_MS
    assert 2100 <= ed.NEON_MS * ed.NEON_PULSE_TICKS <= 2500


def test_dock_frame_tick_is_one_frame_per_screen_refresh():
    """Der Animations-Takt ist die BILDPERIODE des Monitors, keine feste Zahl mehr.

    Vorher standen dort 10 ms (~100 Frames/s) in der Annahme, mehr als die 60 Hz des
    Schirms zu rechnen sei sicherer. Gemessen ist es das Gegenteil: 100 auf 60 gehen
    nicht auf, von je fünf Frames werden drei gezeigt (2-1-2-1-…), und die Schrittweite
    je ANGEZEIGTEM Bild springt zwischen einfach und doppelt – das war das Stottern.
    Dazu kostet ein Fenster-Move beim Hereinfahren ~8-9 ms, der 10-ms-Takt hatte also
    1 ms Luft und platzte laufend (Abstände 9,7 bis 19,5 ms statt 10).

    Die Rate selbst kommt vom Rechner, auf dem der Test läuft – geprüft wird darum die
    Rechnung darum herum, nicht ein fester Wert."""
    ed._tick_ms = None                       # Messung erzwingen (Wert wird gemerkt)
    tick = ed.frame_tick_ms()
    assert ed.ANIM_TICK_MIN_MS <= tick <= ed.ANIM_TICK_MAX_MS, tick
    if ed.ANIM_TICK_MIN_MS < tick < ed.ANIM_TICK_MAX_MS:      # nicht an die Grenze geklemmt
        assert tick == int(1000.0 / float(wf.refresh_hz())), tick
    assert ed.frame_tick_ms() == tick        # gemerkt, kein Win32-Aufruf je Frame
    # Eine unbrauchbar gemeldete Rate darf nie einen Takt von 0 ergeben (Timer-Sturm).
    ed._tick_ms = None
    real, wf.refresh_hz = wf.refresh_hz, lambda *a, **k: 0
    try:
        assert ed.frame_tick_ms() == ed.ANIM_TICK_FALLBACK_MS
    finally:
        wf.refresh_hz = real
        ed._tick_ms = None


def test_handle_cache_reuse_and_clear():
    """Die FORM hängt nur an der Größe, nicht an der Farbe – dieselbe Größe muss
    denselben Maskensatz zurückgeben (sonst würde der atmende Griff je Frame neu
    rendern), und clear_cache muss ihn nach einem Monitorwechsel freigeben."""
    if not hrender.AVAILABLE:
        return
    hrender.clear_cache()
    first = hrender._masks(HR_W, HR_LEN, "left", HR_TUBE, False)
    assert hrender._masks(HR_W, HR_LEN, "left", HR_TUBE, False) is first
    assert hrender._masks(HR_W, HR_LEN, "left", HR_TUBE, True) is not first
    hrender.clear_cache()
    assert hrender._masks(HR_W, HR_LEN, "left", HR_TUBE, False) is not first


# ── Schwappen im Kern (handle_wave + handle_render.WAVE_*) ────────────────
# Variante 09 der Fluid-Vorlage: das helle Mittelstueck kippt zur einen Seite, zurueck
# zur anderen, und kommt zur Ruhe. Die Zusage dahinter ist, dass der ausgewaehlte
# Entwurf der NULLPUNKT bleibt – darum steht dieser Test zuerst.
WAVE_PEAK = 0.4        # s nach dem Anstoss: erste Kippbewegung am Umkehrpunkt


def _wave_light(img, at):
    """Mittlere Helligkeit der Kapsel auf einem Bruchteil `at` ihrer Laenge."""
    x = int(HR_TUBE * hrender.OUT) + HR_TUBE // 2
    y = int(img.size[1] * at)
    return sum(img.getpixel((x, y))[:3])


def test_wave_is_only_a_deviation_from_the_selected_design():
    """Ein Profil aus Nullen muss Pixel fuer Pixel dasselbe Bild ergeben wie GAR KEIN
    Profil. Das ist die Zusage, auf der der ganze Umbau steht: das Schwappen ist eine
    Auslenkung aus dem ausgewaehlten Entwurf, kein neuer Entwurf. Wer es abschaltet
    (edge_dock.WAVE_ON), bekommt exakt den alten Griff zurueck – und niemand muss die
    Kapselform, den Bloom oder die Glaskante nachmessen."""
    if not hrender.AVAILABLE:
        return
    ruhe = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    null = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0,
                               prof=[0.0] * HR_LEN)
    assert list(null.getdata()) == list(ruhe.getdata())


def test_wave_tips_the_capsule_to_one_side():
    """Im Ruhezustand ist die Roehre laengs SYMMETRISCH (der Verlauf faellt zu beiden
    Enden gleich ab). Genau das bricht das Schwappen: am Umkehrpunkt ist die eine
    Haelfte hell und die andere zurueckgenommen. Deshalb wird hier die Asymmetrie
    gemessen – sie kann im alten Bild gar nicht vorkommen."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)
    assert max(prof) > 0.5 and min(prof) < -0.5, "Welle ist am Umkehrpunkt ausgelenkt"
    ruhe = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    welle = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert abs(_wave_light(ruhe, 0.15) - _wave_light(ruhe, 0.85)) < 12   # vorher gleich
    hell, dunkel = _wave_light(welle, 0.15), _wave_light(welle, 0.85)
    assert hell > dunkel + 90, (hell, dunkel)


def test_wave_needs_all_three_levers_to_be_visible():
    """Warum die Welle nicht allein an der WARM-Schicht haengt.

    Der erste Anlauf tat genau das – und war kaum zu sehen. Der Grund steht bei
    WARM_WHITE_HOT: die Schicht mischt 16 % Weiss ueber einen Koerper, der schon
    Vollfarbe ist, und ihre Deckung klemmt in der Kapselmitte ohnehin auf 255. Gemessen
    bewegte dieselbe Welle darueber 21 von 255 Graustufen, ueber Koerper + Weissglut +
    Leuchthof 88. Der Test haelt das Verhaeltnis fest, damit niemand die drei Hebel
    „aufraeumt" und sich hinterher wundert, dass man nichts mehr sieht."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)

    def spanne(**aus):
        alt = {k: getattr(hrender, k) for k in aus}
        try:
            for k, v in aus.items():
                setattr(hrender, k, v)
            img = hrender.handle_rgba(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0,
                                      prof=prof)
            return (_wave_light(img, 0.15) - _wave_light(img, 0.85)) / 3.0
        finally:
            for k, v in alt.items():
                setattr(hrender, k, v)

    voll = spanne()
    nur_warm = spanne(WAVE_DARK=0.0, WAVE_WHITE=0.0, WAVE_BLOOM=0.0)
    assert voll > 60, voll                        # deutlich sichtbar
    assert voll > nur_warm * 2.5, (voll, nur_warm)


def test_wave_keeps_the_frame_invisible_and_grabbable():
    """Die zwei Zusagen des Rahmens gelten auch im Schwappen: um die Kapsel herum ist
    NICHTS zu sehen (kein Kasten), und kein Pixel faellt auf Alpha 0 (sonst klickt man
    durch das Polster hindurch und der Griff waere nicht mehr zu greifen).

    Der Leuchthof zieht in der Welle auf – genau dann koennte er den Fensterrand
    erreichen. Dass er es nicht tut, liegt an BLOOM_FLOOR: dort ist er auf 0 gekappt,
    und 0 bleibt es auch, wenn eine zweite Schicht daruebergeht.

    Geprueft an JEDER Dockkante, und das ist kein Selbstzweck: am oberen Rand liegt der
    Griff quer, dort sind Breite und Hoehe getauscht (_canon) und das Profil muss sich
    mitdrehen. Eine Verwechslung faellt hier sofort auf – die Masken haetten dann nicht
    mehr dieselbe Groesse."""
    if not hrender.AVAILABLE:
        return
    floor = hrender.HIT_ALPHA
    for edge in ("left", "right", "top"):
        w0, h0 = (HR_LEN, HR_W) if edge == "top" else (HR_W, HR_LEN)
        for at in (WAVE_PEAK, WAVE_PEAK + 0.8, 2.0):
            prof = hwave.profile(HR_LEN, at)          # Profil laeuft immer LAENGS
            img = hrender.handle_rgba(w0, h0, edge, HR_TUBE, "#ffc48a", 1.0, prof=prof)
            w, h = img.size
            assert (w, h) == (w0, h0), edge
            # Die drei freien Raender; der vierte ist die Dockkante, dort klebt die Kapsel.
            frei = ([(w - 1, y) for y in range(h)] if edge == "left" else
                    [(0, y) for y in range(h)] if edge == "right" else
                    [(x, h - 1) for x in range(w)])
            assert all(img.getpixel(p)[3] <= floor for p in frei), (edge, at)
            assert min(a for _r, _g, _b, a in img.getdata()) >= floor, (edge, at)


def test_wave_bits_stay_premultiplied_bgra():
    """Was in das Fenster geschoben wird, muss AUCH im Schwappen vormultipliziertes
    BGRA sein – kein Kanal ueber dem Alpha.

    Das ist hier keine Formalie: die Welle legt eine WEISSE Schicht ueber den Koerper,
    und Weiss ist der Fall, in dem ein Kanal am ehesten ueber das Alpha steigt. Ginge
    das schief, bekaeme die Kapsel genau dort einen hellen Saum, wo sie am hellsten ist
    – und der Fehler saehe nach einem Fehler im Entwurf aus, nicht nach einem in der
    Bytefolge."""
    if not hrender.AVAILABLE:
        return
    prof = hwave.profile(HR_LEN, WAVE_PEAK)
    bits = hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert len(bits) == HR_W * HR_LEN * 4
    for i in range(0, len(bits), 4):
        b, g, r, a = bits[i:i + 4]
        assert max(b, g, r) <= a, i // 4
    assert bits[0:3] == b"\x00\x00\x00" and bits[3] == hrender.HIT_ALPHA
    # Und weiterhin BGRA, nicht RGBA: Amber hat mehr Rot als Blau.
    x = int(HR_TUBE * hrender.OUT) + HR_TUBE // 2
    mid = ((HR_LEN // 2) * HR_W + x) * 4
    assert bits[mid + 2] > bits[mid]
    # Ein Wellenbild darf NICHT im Cache landen: es traefe nie wieder und wuerde nur
    # die Ruhezustaende hinausdruecken, von denen die Ruhephase lebt.
    hrender.clear_cache()
    hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0, prof=prof)
    assert not hrender._bits_cache
    hrender.handle_bits(HR_W, HR_LEN, "left", HR_TUBE, "#ffc48a", 1.0)
    assert len(hrender._bits_cache) == 1


def test_wave_is_a_damped_swing_that_returns_to_rest():
    """Die Bewegung selbst: ein Anstoss, ein Kippen hin und zurueck, dann Ruhe – und
    nach PERIOD faengt es von vorn an. Dass sie zur Ruhe kommt, ist der Grund, warum
    der Bild-Cache noch etwas taugt (quiet() -> der Griff nimmt sein gecachtes
    Ruhebild), und dass sie exakt zyklisch ist, macht sie zur reinen Funktion der Zeit:
    nach einer Pause muss nichts nachgerechnet werden."""
    assert hwave.quiet(0.0)                                   # im Anstoss selbst noch still
    assert not hwave.quiet(WAVE_PEAK)                         # kurz danach voll ausgelenkt
    assert hwave.quiet(hwave.PERIOD - 0.05)                   # vor dem naechsten Stoss Ruhe
    # Vorzeichenwechsel = es kippt zurueck, statt nur einmal auszuschlagen.
    oben = [hwave.profile(16, t)[0] for t in (0.4, 1.2)]
    assert oben[0] > 0 > oben[1], oben
    # Zyklisch (auf Rundung, nicht auf das Bit: fmod laesst in der letzten Stelle
    # Rest) – das ist es, was die Bewegung zur reinen Funktion der Zeit macht.
    a, b = hwave.profile(24, 0.7), hwave.profile(24, 0.7 + hwave.PERIOD)
    assert max(abs(x - y) for x, y in zip(a, b)) < 1e-9
    p = hwave.profile(101, WAVE_PEAK)
    assert len(p) == 101 and all(hwave.LO <= v <= hwave.HI for v in p)
    assert abs(p[50]) < 0.35 < abs(p[0]), (p[0], p[50])       # Knoten in der Mitte


def test_wave_profile_is_off_without_alpha_or_in_rest():
    """Wann der Griff KEIN Wellen-Profil bekommt – drei Faelle, jeder mit Grund:
    abgeschaltet, kein Alpha-Pfad (im Linien-Rueckfall gibt es keine Schicht, in der
    eine Welle Platz haette) und Ruhephase. In allen drei Faellen zeichnet er sein
    gecachtes Ruhebild, und das ist genau der alte Griff."""
    d = object.__new__(ed.EdgeDock)
    d._layered = True
    d._wave_t0 = d._now_ms() - WAVE_PEAK * 1000.0
    assert d._wave_profile(HR_LEN) is not None
    d._layered = False
    assert d._wave_profile(HR_LEN) is None, "Linien-Rueckfall traegt keine Welle"
    d._layered = True
    assert d._wave_profile(4) is None, "zu kurz fuer ein Profil"
    d._wave_t0 = d._now_ms() - (hwave.PERIOD - 0.05) * 1000.0
    assert d._wave_profile(HR_LEN) is None, "Ruhephase -> gecachtes Ruhebild"
    real, ed.WAVE_ON = ed.WAVE_ON, False
    try:
        d._wave_t0 = d._now_ms() - WAVE_PEAK * 1000.0
        assert d._wave_profile(HR_LEN) is None, "abgeschaltet"
    finally:
        ed.WAVE_ON = real


def test_wave_kick_and_timer_and_sliding():
    """Die drei Verdrahtungen im Dock.

    (1) Ein dringlicher werdender Status stoesst neu an: der Blitz sagt „jetzt", die
        Welle danach sagt „gerade passiert" – aus einem Aufblitzen wird eine Spur.
    (2) Der Puls-Timer muss fuer die Welle laufen, auch wenn nichts atmet und kein
        Blitz abklingt; vorher hing er allein an diesen beiden.
    (3) Waehrend das Deck GLEITET, wird nicht gemalt: die Feder laeuft im selben Thread
        und braucht jeden Frame. Die Schwingung selbst laeuft an der Uhr weiter und ist
        danach an der richtigen Stelle – genau dafuer ist es eine Uhr."""
    d = object.__new__(ed.EdgeDock)
    d._layered, d._handle_shown = True, True
    d._glow_pulse, d._bloom, d._glow_int = False, 0.0, 1.0
    d._glow_color, d._hot, d._pulse_i = "#ffc48a", False, 0
    d._wave_t0 = d._now_ms() - 3000.0
    d.handle = d.handle_canvas = None
    d._glow_job = None
    d._start_glow = lambda: None              # der echte Timer braucht ein Tk-Fenster
    # (1)
    alt = d._wave_t0
    d.set_glow("#6ee7a8", 0.85, False, flash=True)
    assert d._wave_t0 > alt, "Eskalation stoesst den Kern neu an"
    # (2) – der Blitz aus (1) brennt noch, der zaehlte sonst selbst als Grund
    d._bloom = 0.0
    assert d._glow_needed() is True
    d._layered = False                       # ohne Alpha-Pfad wieder wie vorher
    assert d._glow_needed() is False
    d._layered = True
    d._handle_shown = False                  # aufgeklappt -> kein Griff, kein Timer
    assert d._glow_needed() is False
    # (3)
    gemalt = []
    d._handle_shown = True
    d._paint_handle = lambda: gemalt.append(1)
    d._anim = object()                        # sliding() liest genau das
    d._glow_tick()
    assert gemalt == [], "waehrend des Slides nicht malen"
    d._anim = None
    d._glow_tick()
    assert gemalt == [1]


def test_handle_never_pushes_into_a_hidden_window():
    """Ein VERSTECKTES layered Fenster nimmt kein Bild an – UpdateLayeredWindow lehnt
    mit ERROR_INVALID_PARAMETER ab. Das hat den Griff einmal komplett gekostet:
    _collapse_now positioniert (und zeichnet) ihn, BEVOR _show_handle ihn einblendet,
    der allererste Schub scheiterte, und der Griff fiel dauerhaft auf den Linien-Pfad
    zurück – dunkler Kasten statt Kapsel.

    Der Test haelt die Reihenfolge fest, nicht die Win32-Regel: solange der Griff nicht
    sichtbar ist, darf _paint_layered NICHTS schieben und vor allem nicht aufgeben."""
    pushed = []
    d = object.__new__(ed.EdgeDock)
    d.edge = "left"
    d._layered = True
    d._handle_hwnd = 1234
    d._img_size = (29, 220)
    d._handle_shown = False              # eingeklappt, aber Fenster noch versteckt
    d._hot = d._grip_hot = False
    d.handle = d.handle_canvas = None
    d._paint_layered("#ffc48a", 1.0)
    assert pushed == []                  # kein Schub
    assert d._layered is True            # und NICHT aufgegeben
    # Sichtbar geworden -> jetzt darf (und muss) es schieben. Ohne Tk-Fenster scheitert
    # der Schub, der Rueckfall ist also der erwartete Weg – entscheidend ist, dass es
    # ueberhaupt versucht wird.
    d._handle_shown = True
    d._enable_alpha = lambda force=False: None
    d._report_layer_failure = lambda w, h: None    # der Rueckfall ist hier gewollt
    d._draw_handle = lambda w, h: pushed.append(("linien", w, h))
    d._handle_drawn = (0, 0)
    d._paint_layered("#ffc48a", 1.0)
    assert pushed and pushed[0][0] == "linien"
    assert d._layered is False


def test_handle_window_is_thicker_than_the_capsule():
    """Das Fenster braucht Luft: für den Bloom (sonst wäre er an der Fensterkante
    abgeschnitten) UND als Greif-Zone. Alles Geometrische (Griff-Position,
    Slide-Startstreifen) rechnet deshalb mit der FENSTERdicke, nicht mit der Kapsel –
    und zwar unabhängig davon, ob Pillow da ist: die Zieh-Zone darf nicht an einer
    Bibliothek hängen."""
    assert ed.handle_thick() == ed.HANDLE_THICK + ed.HANDLE_PAD
    assert ed.HANDLE_PAD > 0
    # Die Grenze muss INNERHALB des Fensters liegen, sonst gäbe es keine der beiden
    # Zonen: 0 -> alles Polster (nie aufklappen), ganze Dicke -> alles Kapsel (nie greifen).
    assert 0 < ed.capsule_extent() < ed.handle_thick()


# ── watchdog: die Beurteilung des letzten Panel-Laufs. Daran haengt die einzige
# Frage, die der Waechter falsch machen DARF und nicht falsch machen SOLL: darf er
# das Panel wieder hochholen? Ein bewusst geschlossenes Deck muss geschlossen
# bleiben, ein abgestuerztes muss zurueckkommen.
def _befund_fuer(log_text):
    """last_end() gegen ein praepariertes panel.log laufen lassen."""
    import tempfile
    from deck.ops import log
    from deck.ops import watchdog as wd
    fd, path = tempfile.mkstemp(prefix="panellog_", suffix=".log")
    os.close(fd)
    alt = log.LOG_PATH
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(log_text)
        log.LOG_PATH = path
        return wd.last_end()
    finally:
        log.LOG_PATH = alt
        try:
            os.remove(path)
        except OSError:
            pass


def test_watchdog_sieht_sauberes_ende():
    """Panel hat sich selbst beendet -> der Nutzer hat es geschlossen. Der Waechter
    muss das als CLEAN_END erkennen, sonst kommt das Deck alle drei Minuten zurueck."""
    from deck.ops import watchdog as wd
    befund = _befund_fuer(
        "[..] --- Panel-Start (PID 1, Python 3.14.0) ---\n"
        "[..] mainloop beendet (Fenster zerstoert) -> Panel endet regulaer\n"
        "[..] --- Panel-Ende (normaler Exit) ---\n")
    assert befund.startswith(wd.CLEAN_END), befund


def test_watchdog_sieht_abschuss():
    """Log bricht mitten im Lauf ab: keine Exit-Marke, kein Dump -> von aussen
    abgeschossen. Muss neu gestartet werden (also NICHT als CLEAN_END gelten)."""
    from deck.ops import watchdog as wd
    befund = _befund_fuer(
        "[..] --- Panel-Start (PID 1, Python 3.14.0) ---\n"
        "[..] Fehler in Tk-Callback:\n(harmlos, Panel lief weiter)\n")
    assert "ABGESCHOSSEN" in befund
    assert not befund.startswith(wd.CLEAN_END)


def test_watchdog_sieht_harten_absturz():
    """faulthandler-Dump (Tcl-Panic) schlaegt alles andere – auch eine Exit-Marke,
    die zufaellig noch im selben Abschnitt steht."""
    befund = _befund_fuer(
        "[..] --- Panel-Start (PID 1, Python 3.14.0) ---\n"
        "Fatal Python error: Aborted\n\nCurrent thread 0x00001234 (most recent call first):\n")
    assert "HARTER ABSTURZ" in befund


def test_watchdog_beurteilt_nur_den_letzten_lauf():
    """Nur der Abschnitt nach der LETZTEN Panel-Start-Marke zaehlt: ein Absturz von
    vorgestern darf den heutigen sauberen Lauf nicht ueberstimmen."""
    from deck.ops import watchdog as wd
    befund = _befund_fuer(
        "[..] --- Panel-Start (PID 1, Python 3.14.0) ---\n"
        "Fatal Python error: Aborted\n"
        "[..] --- Panel-Start (PID 2, Python 3.14.0) ---\n"
        "[..] --- Panel-Ende (normaler Exit) ---\n")
    assert befund.startswith(wd.CLEAN_END), befund


def test_watchdog_waechter_zeilen_sind_kein_panel_lauf():
    """Der Waechter selbst schreibt KEINE Start-/Ende-Marken (log.install(marks=False)).
    Stuenden welche im Log, wuerde er seinen eigenen Lauf beurteilen und jedes
    geschlossene Deck wieder hochholen."""
    from deck.ops import watchdog as wd
    befund = _befund_fuer(
        "[..] --- Panel-Start (PID 1, Python 3.14.0) ---\n"
        "[..] --- Panel-Ende (normaler Exit) ---\n"
        "[..] Waechter: kein Panel da. Vorgaenger-Ende: irgendwas\n")
    assert befund.startswith(wd.CLEAN_END), befund


def test_heartbeat_frische_und_pid_muessen_passen():
    """beats_for(): nur ein frisches Lebenszeichen DIESER PID gilt. Sonst haelt der
    Guard ein fremdes/altes Signal fuer ein lebendes Panel (oder umgekehrt)."""
    import tempfile
    from deck.ops import instance as si
    d = tempfile.mkdtemp(prefix="beat_")
    alt = si.BEAT_PATH
    try:
        si.BEAT_PATH = os.path.join(d, "panel.heartbeat")
        assert si.beat_age() is None                  # noch gar keins
        assert si.beats_for(4711) is False
        si.beat()                                     # schreibt die EIGENE PID
        assert si.beat_pid() == os.getpid()
        assert si.beats_for(os.getpid()) is True
        assert si.beats_for(os.getpid() + 1) is False  # fremde PID -> nein
        age = si.beat_age()
        assert age is not None and age < si.BEAT_FRESH_S
        # Zu alt -> gilt nicht mehr (mtime zurueckdrehen statt zu warten).
        old = time.time() - si.BEAT_FRESH_S - 5
        os.utime(si.BEAT_PATH, (old, old))
        assert si.beats_for(os.getpid()) is False
    finally:
        si.BEAT_PATH = alt
        import shutil
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    fails = 0
    for n, f in fns:
        try:
            f()
            print(f"  ok  {n}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {n}: {e}")
    print(f"\n{len(fns) - fails}/{len(fns)} bestanden")
    sys.exit(1 if fails else 0)
