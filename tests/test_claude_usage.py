"""claude_usage: Parser der oauth/usage-Antwort und die Token der Claude-Code-CLI.
"""

import os
import time
from datetime import UTC, datetime

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from deck.claude import usage_token as utok
from deck.claude import usage_view as uview

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
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert uview.fmt_reset("2026-01-01T12:45:00+00:00", base) == "45 Min."
    assert uview.fmt_reset("2026-01-01T14:00:00+00:00", base) == "2 Std."
    assert uview.fmt_reset("2026-01-01T14:30:00+00:00", base) == "2 Std. 30 Min."
    assert uview.fmt_reset("2026-01-03T12:00:00+00:00", base) == "2 Tg."
    assert uview.fmt_reset("2026-01-03T15:00:00+00:00", base) == "2 Tg. 3 Std."
    assert uview.fmt_reset("2026-01-01T11:00:00+00:00", base) == "jetzt"    # Vergangenheit
    assert uview.fmt_reset("2026-01-01T12:45:00", base) == "45 Min."        # naiv -> als UTC
    assert uview.fmt_reset(None, base) == ""
    assert uview.fmt_reset("kaputt", base) == ""


def test_usage_severity_color():
    assert uview.severity_color("critical", 91) == "#ff6b6b"
    assert uview.severity_color("warning", 60) == "#ffc48a"
    assert uview.severity_color("normal", 15) == "#6ee7a8"
    assert uview.severity_color("", None) == "#8b8b99"                      # kein Wert -> grau
    assert uview.severity_color("", 30) == "#6ee7a8"                        # Fallback per Schwelle
    assert uview.severity_color("", 70) == "#ffc48a"
    assert uview.severity_color("", 95) == "#ff6b6b"


def test_usage_parse_limits():
    p = uview.parse_usage(_USAGE_SAMPLE)
    assert p["session"]["percent"] == 91
    assert p["session"]["severity"] == "critical"
    assert p["session"]["group"] == "session"
    assert [lim["label"] for lim in p["limits"]] == ["Session", "Woche", "Fable (Woche)"]


def test_usage_parse_fallback():
    # Aeltere Antwort ohne 'limits' -> aus five_hour/seven_day rekonstruiert.
    p = uview.parse_usage({"five_hour": {"utilization": 91.0, "resets_at": "x"},
                        "seven_day": {"utilization": 15.0, "resets_at": "y"}})
    assert p["session"]["percent"] == 91
    assert [lim["label"] for lim in p["limits"]] == ["Session", "Woche"]


def test_usage_parse_empty():
    p = uview.parse_usage({})
    assert p["session"] is None and p["limits"] == []


def test_usage_tooltip_text():
    base = datetime(2026, 7, 21, 21, 55, tzinfo=UTC)
    snap = {"state": "ok", "limits": uview.parse_usage(_USAGE_SAMPLE)["limits"], "error": None}
    txt = uview.tooltip_text(snap, base)
    assert "Claude – Nutzung" in txt
    assert "Session: 91 %" in txt
    assert "Woche: 15 %" in txt
    assert "Reset in 1 Std. 5 Min." in txt          # Session-Reset relativ zu base
    assert "Fable" not in txt                        # 0 %/kein Reset/inaktiv -> ausgefiltert


def test_usage_tooltip_error():
    txt = uview.tooltip_text({"state": "error", "limits": [], "error": "nicht angemeldet"})
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
    assert utok.tokens_from_credentials(_creds("tok-a", soon)) == ["tok-a"]


def test_cli_token_abgelaufen_wird_nicht_gesendet():
    past = (time.time() - 60) * 1000
    assert utok.tokens_from_credentials(_creds("tok-alt", past)) == []


def test_cli_token_ohne_ablaufzeit_gilt_als_gueltig():
    """Ein totes Token kostet nur einen 401 – fetch_usage nimmt dann das naechste.
    Es wegzuwerfen, nur weil das Feld fehlt, waere der teurere Fehler."""
    assert utok.tokens_from_credentials(_creds("tok-b")) == ["tok-b"]


def test_cli_token_auch_ohne_bekannten_container():
    """Das Dateiformat gehoert der CLI und ist nicht dokumentiert. Liegt das Token
    flach oder in snake_case, darf die Anzeige trotzdem nicht ausfallen."""
    assert utok.tokens_from_credentials({"accessToken": "flach"}) == ["flach"]
    assert utok.tokens_from_credentials(_creds("snake", key="claude_ai_oauth")) == ["snake"]
    assert utok.tokens_from_credentials({"claudeAiOauth": {"token": "alt"}}) == ["alt"]


def test_cli_token_muell_gibt_leere_liste():
    for muell in (None, {}, [], "text", {"claudeAiOauth": {}},
                  {"claudeAiOauth": {"accessToken": ""}}, {"claudeAiOauth": None}):
        assert utok.tokens_from_credentials(muell) == [], muell


def test_cli_credentials_pfad_folgt_der_umgebungsvariable():
    alt = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join("X:", "woanders")
        assert utok.cli_credentials_path() == os.path.join("X:", "woanders",
                                                         ".credentials.json")
        os.environ.pop("CLAUDE_CONFIG_DIR")
        assert utok.cli_credentials_path().endswith(
            os.path.join(".claude", ".credentials.json"))
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        if alt is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = alt


def test_beide_tokenquellen_werden_zusammengelegt():
    """Der Kern des Fallbacks: faellt EINE Quelle aus, traegt die andere. Erst wenn
    beide nichts liefern, ist es ein Fehler – und der nennt beide Quellen."""
    ruhe = utok._token_cache
    cli, desk = utok._read_tokens_from_cli, utok._read_tokens_from_disk
    try:
        utok._token_cache = []
        utok._read_tokens_from_cli = lambda: ["cli"]
        utok._read_tokens_from_disk = lambda: ["desk"]
        assert utok.read_oauth_token(force=True) == ["cli", "desk"]   # CLI zuerst

        def weg():
            raise FileNotFoundError("kein Claude Desktop")

        utok._read_tokens_from_disk = weg
        assert utok.read_oauth_token(force=True) == ["cli"]           # Desktop fehlt -> egal

        utok._read_tokens_from_cli = weg
        try:
            utok.read_oauth_token(force=True)
            raise AssertionError("ohne jede Quelle muss NoTokenError fliegen")
        except utok.NoTokenError as e:
            assert "CLI" in str(e) and "Desktop" in str(e), str(e)
    finally:
        utok._read_tokens_from_cli, utok._read_tokens_from_disk = cli, desk
        utok._token_cache = ruhe
