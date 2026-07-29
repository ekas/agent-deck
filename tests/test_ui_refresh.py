"""Auto-Startmodus neuer Agenten und die Neon-Farbe, die das Panel an den Griff
gibt (_apply_pending_auto, _set_slot_mode, _update_dock_glow).
"""

import helpers  # setzt sys.path und die Deck-Sprache

from deck.domain import config as cfg
from deck.ui import theme

from helpers import _CYCLE


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
