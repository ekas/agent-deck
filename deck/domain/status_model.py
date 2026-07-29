"""Reine Status-Interpretation, aus dem refresh()-Renderloop herausgezogen: keine
Anzeige, kein tkinter, keine Seiteneffekte -> ohne laufendes Panel testbar.

Es geht um die kleinen, aber kniffligen Regeln: wann gilt eine Meldung als frisch,
wann faellt ein 'denkt' auf idle zurueck, wann ist eine Verbindung verloren, wie
loest sich die xhigh/ultracode-Effort-Kollision auf und wann uebernimmt das Deck
einen per Hook gemeldeten Permission-Mode.
"""
from collections.abc import Container, Iterable, Sequence
from typing import Any


def is_fresh(st: dict[str, Any] | None, now: float, stale_s: float) -> bool:
    """Meldung frisch = existiert und ist nicht aelter als stale_s Sekunden.

    Der leere Zustand zaehlt bewusst wie kein Zustand: ein {} entsteht, wenn eine
    Statusdatei halb geschrieben oder kaputt war."""
    if not st:
        return False
    return now - st.get("ts", 0) <= stale_s


def normalize_status(status: str, fresh: bool, valid: Container[str]) -> str:
    """Status fuer die Anzeige normalisieren: 'thinking'/'running' ohne frische
    Meldung gilt als eingeschlafen (idle); ein unbekannter Status ebenfalls idle.
    `valid` = erlaubte Status (z.B. die Schluessel von GLOW_STYLE)."""
    if status in ("thinking", "running") and not fresh:
        return "idle"                              # lange still -> als idle
    if status not in valid:
        return "idle"
    return status


def is_lost(status: str, fresh: bool, connected: bool) -> bool:
    """Rot = Verbindung zum Fenster verloren. Nur fuer frische, aktive Agenten,
    damit alte Restdateien beim Start nicht faelschlich rot werden."""
    return status != "none" and fresh and not connected


# Rangfolge der Kachel-Status fuer den Griff-Balken (Neon): wichtig -> unwichtig.
# 'lost' ist kein gemeldeter Status, sondern der im Panel berechnete Verbindungsverlust.
DECK_PRIORITY = ("waiting", "done", "lost", "thinking", "idle", "none")
# 'running' und 'thinking' sind derselbe Zustand ("denkt", gleiche Farbe) -> gleicher
# Rang und derselbe kanonische Name. Sonst blitzte der Griff bei jedem Wechsel
# zwischen den beiden Meldungen auf, obwohl sich sichtbar nichts aendert.
_DECK_ALIAS = {"running": "thinking"}


def _deck_rank(key: str) -> int:
    """Rang in DECK_PRIORITY (klein = dringlicher); Unbekanntes zaehlt als harmlos."""
    key = _DECK_ALIAS.get(key, key)
    return DECK_PRIORITY.index(key) if key in DECK_PRIORITY else len(DECK_PRIORITY)


def dominant_status(keys: Iterable[str]) -> str:
    """Alle Kachel-Status zu EINEM Deck-Gesamtzustand verdichten (fuer die Neon-Farbe
    des eingeklappten Griff-Balkens): Rueckfrage > ungelesen > getrennt > denkt > idle.
    So sieht man am Griff, ob einer etwas von dir will, auch wenn das Deck zu ist.
    `keys` = die status_keys der Kacheln; liefert einen kanonischen Status (also
    'thinking' auch fuer 'running'). Keine Kacheln -> 'none' (kein Agent)."""
    ks = {_DECK_ALIAS.get(k, k) for k in keys}
    for k in DECK_PRIORITY:
        if k in ks:
            return k
    return "none"


def escalated(prev: str, key: str) -> bool:
    """Wird der Deck-Gesamtzustand DRINGLICHER? Nur dann blitzt der Griff kurz auf.
    Ein Wechsel auf einen ruhigeren Zustand (z.B. ungelesen -> idle, weil du die
    Antwort gelesen hast) ist deine eigene Geste und blitzt bewusst NICHT."""
    return key != prev and _deck_rank(key) < _deck_rank(prev)


def resolve_effort(live_eff: str, remembered: str) -> str:
    """Effort-Kollision aufloesen: die statusLine meldet fuer xhigh UND ultracode
    nur 'xhigh'. Das per Button gemerkte Effort gewinnt bei leer/'xhigh' (und
    ueberbrueckt fehlende Live-Daten); meldet die statusLine ein KONKRETES anderes
    Level (z.B. nach Modellwechsel auf den Modell-Default zurueckgesetzt), gewinnt
    dieser echte Wert -> keine veraltete Anzeige."""
    return remembered if (remembered and live_eff in ("", "xhigh")) else live_eff


def mode_steps(remembered: int | None, target: str, cycle: Sequence[str],
               start: str) -> tuple[int, int] | None:
    """Anzahl Shift+Tab vom angenommenen aktuellen zum Ziel-Modus (zyklisch).
    `remembered` = gemerkter Modus-Index des Slots (None -> es wird der Start-Modus
    angenommen, wie bei einem frischen Chat). Liefert (steps, ziel_index) oder None,
    wenn das Ziel nicht im Zyklus liegt. Gemeinsame Basis fuer die Mode-Buttons und
    den Auto-Startmodus neuer Agenten."""
    if target not in cycle:
        return None
    cur = remembered if remembered is not None else (
        cycle.index(start) if start in cycle else 0)
    tgt = cycle.index(target)
    return (tgt - cur) % len(cycle), tgt


def adopt_hook_mode(prev_ts: float, st: dict[str, Any],
                    cycle: Sequence[str]) -> tuple[int, float] | None:
    """Ist-Permission-Mode aus einem Hook-Event uebernehmen (self-correcting):
    liefert (mode_index, ts) bei einem NEUEREN Event mit gueltigem Modus, sonst
    None. So folgt die Deck-Annahme dem zuletzt gemeldeten echten Modus."""
    m = st.get("mode")
    ts = st.get("ts", 0)
    if m in cycle and ts > prev_ts:
        return cycle.index(m), ts
    return None
