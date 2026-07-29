"""claude_usage: Parser der oauth/usage-Antwort und die Token der Claude-Code-CLI.
"""

from datetime import datetime
from datetime import timezone
import os
import time

import helpers  # setzt sys.path und die Deck-Sprache

from deck.claude import usage as cu


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
