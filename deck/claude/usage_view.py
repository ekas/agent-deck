"""Usage-Antwort auswerten und fuer Menschen aufbereiten - pur, headless testbar.

Parsen und Formatieren liegen zusammen, weil beide Schritte derselben Kette gehoeren und
beide ohne Netz, Token und Anzeige pruefbar sind: JSON rein, Snapshot raus - Snapshot
rein, Zeile raus.

Zahlen fuer Menschen brauchen einen festen Punkt als Dezimaltrenner; eine
locale-abhaengige Formatierung zeigt auf einem deutschen System sonst $0,15 statt $0.15.
"""
from datetime import datetime
from datetime import timezone

from deck import i18n


# Ampelfarben (identisch zur Deck-Palette: done-gruen / waiting-amber / lost-rot),
# damit das Badge sich nahtlos einfuegt. severity kommt direkt aus der API.
_GREEN, _AMBER, _RED, _GRAY = "#6ee7a8", "#ffc48a", "#ff6b6b", "#8b8b99"
_SEVERITY_COLORS = {"normal": _GREEN, "warning": _AMBER, "critical": _RED}


def fmt_reset(iso, now=None):
    """ISO-Zeit -> 'X Tg. Y Std.' / 'X Std. Y Min.' / 'X Min.' relativ zu now
    (tz-aware datetime; Default = jetzt UTC). Leer/kaputt -> ''; Vergangenheit ->
    'jetzt'. now injizierbar, damit Tests nicht von der Wanduhr abhaengen."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    delta = (dt - now).total_seconds()
    if delta <= 0:
        return i18n.L("jetzt", "now")
    d, h, m = int(delta // 86400), int((delta % 86400) // 3600), int((delta % 3600) // 60)
    if d:
        return i18n.L(f"{d} Tg. {h} Std.", f"{d}d {h}h") if h else i18n.L(f"{d} Tg.", f"{d}d")
    if h:
        return i18n.L(f"{h} Std. {m} Min.", f"{h}h {m}min") if m else i18n.L(f"{h} Std.", f"{h}h")
    return i18n.L(f"{m} Min.", f"{m}min")


def severity_color(severity, percent):
    """Hex-Farbe fuers Badge. Zuerst die API-severity (normal/warning/critical);
    fehlt sie, per Schwellwert (wie der Usage-Monitor: <50 gruen, <80 amber, sonst
    rot). Ohne Wert grau."""
    if severity in _SEVERITY_COLORS:
        return _SEVERITY_COLORS[severity]
    if percent is None:
        return _GRAY
    if percent < 50:
        return _GREEN
    if percent < 80:
        return _AMBER
    return _RED


def _limit_label(lim):
    """Menschlicher (deutscher) Name eines API-Limits fuers Hover."""
    kind = (lim.get("kind") or "").lower()
    if kind == "session":
        return "Session"
    if "opus" in kind:
        return i18n.L("Opus (Woche)", "Opus (week)")
    if "sonnet" in kind:
        return i18n.L("Sonnet (Woche)", "Sonnet (week)")
    if kind == "weekly_scoped":
        scope = lim.get("scope") or {}
        model = (scope.get("model") or {}).get("display_name") if isinstance(scope, dict) else None
        return i18n.L(f"{model} (Woche)", f"{model} (week)") if model \
            else i18n.L("Woche (Modell)", "Week (model)")
    if kind.startswith("weekly"):
        return i18n.L("Woche", "Week")
    return kind.replace("_", " ").title() or "Limit"


def _pct(v):
    return int(round(v)) if isinstance(v, (int, float)) else None


def parse_usage(data):
    """Rohe API-Antwort -> normalisiertes Dict:
        {"session": <limit|None>, "limits": [<limit>, …]}
    Ein <limit> ist {kind, group, label, percent, severity, resets_at, active}.
    Nutzt bevorzugt das moderne 'limits'-Array; faellt sonst auf die aelteren
    Felder five_hour/seven_day zurueck."""
    limits = []
    raw = data.get("limits") if isinstance(data, dict) else None
    if isinstance(raw, list) and raw:
        for lim in raw:
            if not isinstance(lim, dict):
                continue
            limits.append({
                "kind": lim.get("kind") or "",
                "group": lim.get("group") or "",
                "label": _limit_label(lim),
                "percent": _pct(lim.get("percent")),
                "severity": lim.get("severity") or "",
                "resets_at": lim.get("resets_at"),
                "active": bool(lim.get("is_active")),
            })
    else:                                   # aeltere Antwort ohne 'limits'
        five = (data.get("five_hour") if isinstance(data, dict) else None) or {}
        seven = (data.get("seven_day") if isinstance(data, dict) else None) or {}
        if five.get("utilization") is not None:
            limits.append({"kind": "session", "group": "session", "label": "Session",
                           "percent": _pct(five["utilization"]), "severity": "",
                           "resets_at": five.get("resets_at"), "active": True})
        if seven.get("utilization") is not None:
            limits.append({"kind": "weekly_all", "group": "weekly",
                           "label": i18n.L("Woche", "Week"),
                           "percent": _pct(seven["utilization"]), "severity": "",
                           "resets_at": seven.get("resets_at"), "active": False})
    session = next((l for l in limits if l["group"] == "session" or l["kind"] == "session"), None)
    return {"session": session, "limits": limits}


def _keep_in_tooltip(lim):
    """Welche Limits im Hover erscheinen: Session + Wochen-Gesamt immer, modell-
    spezifische Wochenlimits nur, wenn sie Signal tragen (Prozent > 0 oder aktiv).
    So bleibt der Tooltip aufgeraeumt, wenn ein Modell-Limit noch bei 0 % steht."""
    if lim["group"] == "session" or lim["kind"] == "weekly_all":
        return True
    return bool(lim["percent"]) or lim["active"]


def tooltip_text(snap, now=None):
    """Mehrzeiliger Hover-Text aus einem Poller-Snapshot (siehe UsagePoller)."""
    head = i18n.L("Claude – Nutzung", "Claude – usage")
    limits = [l for l in (snap.get("limits") or []) if _keep_in_tooltip(l)]
    if not limits:
        return f"{head}\n{snap.get('error') or i18n.L('warte auf Daten…', 'waiting for data…')}"
    lines = [head]
    for l in limits:
        pct = f"{l['percent']} %" if l["percent"] is not None else "— %"
        reset = fmt_reset(l["resets_at"], now)
        line = f"{l['label']}: {pct}"
        if reset:
            line += i18n.L(f"  ·  Reset in {reset}", f"  ·  resets in {reset}")
        lines.append(line)
    if snap.get("error"):
        lines.append(i18n.L(f"(letzter Wert – {snap['error']})",
                            f"(last value – {snap['error']})"))
    return "\n".join(lines)
