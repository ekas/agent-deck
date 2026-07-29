"""Claude-Nutzung (Session + Wochenlimits) fuer den Header – Datenschicht.

Fragt
    https://api.anthropic.com/api/oauth/usage
mit dem OAuth-Token des angemeldeten Kontos ab. Keine Browser-Extension noetig.

ZWEI QUELLEN fuer das Token, beide werden gelesen und der Reihe nach probiert
(read_oauth_token sammelt sie, fetch_usage nimmt das erste, das 200 liefert):

  1. Claude Code CLI – `~/.claude/.credentials.json`, KLARTEXT-JSON unter
     "claudeAiOauth". Der Normalfall: wer das Deck benutzt, hat die CLI zwingend
     installiert, Claude Desktop dagegen oft nicht.
  2. Claude Desktop – dessen config.json, VERSCHLUESSELT. Der Token-Blob ist ein
     Chromium-"v10"-Paket: der AES-256-Schluessel steckt (per Windows-DPAPI
     geschuetzt) in "Local State", das Token selbst ist AES-256-GCM. Damit das
     Deck ABHAENGIGKEITSFREI bleibt (nur stdlib + ctypes, wie der Rest der App),
     entschluesselt _aesgcm_decrypt ueber Windows CNG (bcrypt.dll); klappt das
     aus irgendeinem Grund nicht, faellt es auf das 'cryptography'-Paket zurueck.

Warum ueberhaupt beide: die Tokens haben unterschiedliche Laufzeiten und Scopes.
Ist eins abgelaufen oder wird es mit 401 abgewiesen, traegt das andere weiter —
ohne dass der Nutzer etwas merkt.

Gelesen werden die Dateien NICHT bei jedem Poll: read_oauth_token haelt die Tokens
in _token_cache und liest erst neu, wenn die API eins abweist (401/403 -> force).
Ein erneuertes Token kostet also genau einen fehlgeschlagenen Abruf, kein Polling
auf der Platte.

Alles ist defensiv: fehlt jede Quelle / ein Token / das Netz, liefern die
Funktionen definierte Fehler, die der UsagePoller abfaengt und als Fehlertext in
den Snapshot legt. Das Deck laeuft ungestoert weiter; das Badge zeigt dann "—".

Bewusst OHNE tkinter-Import -> die puren Parser (parse_usage / fmt_reset /
severity_color / tooltip_text) sind headless testbar (tests/test_claude_usage.py).
"""
import os
import glob
import json
import base64
import ctypes
import random
import threading
import time
import urllib.request
import urllib.error

from deck import i18n
from ctypes import wintypes
from datetime import datetime, timezone

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Ampelfarben (identisch zur Deck-Palette: done-gruen / waiting-amber / lost-rot),
# damit das Badge sich nahtlos einfuegt. severity kommt direkt aus der API.
_GREEN, _AMBER, _RED, _GRAY = "#6ee7a8", "#ffc48a", "#ff6b6b", "#8b8b99"
_SEVERITY_COLORS = {"normal": _GREEN, "warning": _AMBER, "critical": _RED}


# ── Optionaler gemeinsamer Ein-Poller ────────────────────
# NICHT Teil dieses Repos und fuer nichts noetig: wer NEBEN dem Deck noch einen
# zweiten Usage-Anzeiger laufen laesst, kann beide ueber ein Cache-/Mutex-Modul
# denselben Abruf teilen lassen, statt den rate-limitierten Endpoint doppelt zu
# pollen (das erschoepfte sonst das Account-Limit -> HTTP 429). Gesucht wird es
# unter CLAUDE_USAGE_SHARED_DIR bzw. im Nachbarordner 'claude-usage-shared'.
# Fehlt es — der Normalfall —, ruft UsagePoller unten einfach selbst ab.
_shared_mod = "unset"


def _shared():
    global _shared_mod
    if _shared_mod != "unset":
        return _shared_mod
    import importlib
    import sys
    from deck.domain import paths
    here = paths.REPO_ROOT
    for p in (os.environ.get("CLAUDE_USAGE_SHARED_DIR"),
              os.path.join(here, "..", "claude-usage-shared")):
        if p and os.path.isfile(os.path.join(p, "usage_poller.py")):
            p = os.path.abspath(p)
            if p not in sys.path:
                sys.path.insert(0, p)
            try:
                _shared_mod = importlib.import_module("usage_poller")
            except Exception:
                _shared_mod = None
            return _shared_mod
    _shared_mod = None
    return _shared_mod


# ── Windows-API defensiv laden ───────────────────────────
# Schlaegt ein Laden fehl (z.B. Nicht-Windows beim Test-Import), bleibt _WINOK
# False; fetch_usage wirft dann sauber, statt beim Import zu crashen.
try:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
    _WINOK = True
except Exception:
    _k32 = None
    _WINOK = False


# ── Speicherorte von Claude Desktops Nutzerdaten ─────────
def _known_folder(csidl):
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return ""


def _candidate_dirs():
    """Moegliche Speicherorte: Claude Desktop ist eine MSIX-/Store-App -> AppData ist
    virtualisiert; je nach Start-Kontext ist nur EINER dieser Pfade sichtbar."""
    dirs = []
    local = _known_folder(0x001C) or os.environ.get("LOCALAPPDATA", "")   # CSIDL_LOCAL_APPDATA
    if local:
        dirs += sorted(glob.glob(os.path.join(
            local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude")))
    roaming = _known_folder(0x001A) or os.environ.get("APPDATA", "")       # CSIDL_APPDATA
    if roaming:
        dirs.append(os.path.join(roaming, "Claude"))
    return dirs


def claude_dir():
    """Der Ordner, in dem aktuell wirklich eine config.json liegt (sonst der erste
    Kandidat als Fallback fuer die Fehlermeldung)."""
    cands = _candidate_dirs()
    for d in cands:
        if os.path.exists(os.path.join(d, "config.json")):
            return d
    return cands[0] if cands else ""


# ── Dateien lesen, ohne Claude Desktop zu blockieren ─────
# Pythons open() haelt ein Handle OHNE FILE_SHARE_DELETE -> Claude koennte beim
# Start seine Dateien nicht atomar ersetzen. Daher mit voller Freigabe lesen.
_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x1 | 0x2 | 0x4          # READ | WRITE | DELETE
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _read_shared(path, retries=5):
    """Liest eine Datei mit voller Freigabe (blockiert keinen anderen Prozess).
    Retries fangen das kurze Fenster ab, in dem Claude die Datei beim Start
    atomar ersetzt (dann ist sie 1-2 ms 'nicht vorhanden')."""
    if not _WINOK:
        with open(path, "rb") as f:                # Fallback (Test/Nicht-Windows)
            return f.read()
    last = None
    for _ in range(retries):
        h = _k32.CreateFileW(path, _GENERIC_READ, _SHARE_ALL, None,
                             _OPEN_EXISTING, 0x80, None)
        if h == _INVALID_HANDLE:
            last = ctypes.WinError(ctypes.get_last_error())
            time.sleep(0.2)
            continue
        try:
            chunks, buf, n = [], ctypes.create_string_buffer(65536), wintypes.DWORD(0)
            while True:
                if not _k32.ReadFile(h, buf, 65536, ctypes.byref(n), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if n.value == 0:
                    break
                chunks.append(buf.raw[:n.value])
            return b"".join(chunks)
        finally:
            _k32.CloseHandle(h)
    raise last if last else OSError(f"Konnte {path} nicht lesen")


# ── DPAPI: Local-State-Schluessel entschluesseln ─────────
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_unprotect(data):
    """Windows DPAPI CryptUnprotectData (kein Drittpaket noetig)."""
    blob_in = _DATA_BLOB(len(data),
                         ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                     ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ── AES-256-GCM ueber Windows CNG (bcrypt.dll) ───────────
# Dependency-frei; gegen cryptography.AESGCM mit 200 Round-Trips validiert.
class _CNG_AUTH_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG), ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)), ("cbNonce", wintypes.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)), ("cbAuthData", wintypes.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)), ("cbTag", wintypes.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)), ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG), ("cbData", ctypes.c_ulonglong), ("dwFlags", wintypes.ULONG),
    ]


def _bcrypt_gcm_decrypt(key, nonce, ct_with_tag, aad=None, tag_len=16):
    bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
    ct, tag = ct_with_tag[:-tag_len], ct_with_tag[-tag_len:]

    def chk(status, what):
        if status != 0:
            raise OSError(f"BCrypt {what}: 0x{status & 0xffffffff:08x}")

    hAlg = wintypes.HANDLE()
    chk(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(hAlg), "AES", None, 0),
        "OpenAlgorithmProvider")
    try:
        gcm = "ChainingModeGCM".encode("utf-16-le") + b"\x00\x00"
        chk(bcrypt.BCryptSetProperty(hAlg, "ChainingMode", gcm, len(gcm), 0),
            "SetProperty")
        hKey = wintypes.HANDLE()
        keybuf = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
        chk(bcrypt.BCryptGenerateSymmetricKey(
            hAlg, ctypes.byref(hKey), None, 0, keybuf, len(key), 0), "GenerateKey")
        try:
            nonce_buf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
            tag_buf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
            info = _CNG_AUTH_INFO()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = 1
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbTag = len(tag)
            if aad:
                aad_buf = (ctypes.c_ubyte * len(aad)).from_buffer_copy(aad)
                info.pbAuthData = ctypes.cast(aad_buf, ctypes.POINTER(ctypes.c_ubyte))
                info.cbAuthData = len(aad)
            ct_buf = (ctypes.c_ubyte * len(ct)).from_buffer_copy(ct) if ct else None
            out = (ctypes.c_ubyte * len(ct))()
            out_len = wintypes.ULONG(0)
            chk(bcrypt.BCryptDecrypt(hKey, ct_buf, len(ct), ctypes.byref(info),
                                     None, 0, out, len(ct), ctypes.byref(out_len), 0),
                "Decrypt")
            return bytes(out[:out_len.value])
        finally:
            bcrypt.BCryptDestroyKey(hKey)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(hAlg, 0)


def _aesgcm_decrypt(key, nonce, ct_with_tag, aad=None):
    """Zuerst Windows CNG (dependency-frei); scheitert das, 'cryptography'."""
    try:
        return _bcrypt_gcm_decrypt(key, nonce, ct_with_tag, aad)
    except Exception:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ct_with_tag, aad)


def _decrypt_v10(blob, key):
    """Chromium 'v10'-Blob: [3B 'v10'][12B nonce][ciphertext+16B tag]."""
    return _aesgcm_decrypt(key, blob[3:15], blob[15:], None)


# ── Token aus der Claude-Code-CLI lesen ──────────────────
# Die CLI legt ihr OAuth-Token als KLARTEXT-JSON ab (kein DPAPI, kein v10-Blob) —
# unter macOS im Keychain, unter Windows und Linux in einer Datei. Das Deck ist
# Windows-only, der Dateiweg reicht also.
_CLI_TOKEN_KEYS = ("accessToken", "access_token", "token")
_CLI_EXPIRY_KEYS = ("expiresAt", "expires_at")


def cli_credentials_path():
    """Pfad zu .credentials.json. CLAUDE_CONFIG_DIR verlegt den Ordner der CLI —
    wer das gesetzt hat, soll nicht durchs Raster fallen."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(base, ".credentials.json")


def tokens_from_credentials(data, now_ms=None):
    """Pur (headless testbar): geparstes .credentials.json -> Liste noch gueltiger
    Tokens, langlebigstes zuerst.

    Bewusst nachsichtig gegenueber dem Dateiaufbau. Das Format ist NICHT
    dokumentiert und gehoert der CLI, nicht uns; ein Umbau dort darf die Anzeige
    hoechstens verstummen lassen, nie das Deck kosten. Darum: bekannter Container
    zuerst, sonst das Objekt selbst, und mehrere Schreibweisen je Feld.

    Fehlt die Ablaufzeit, gilt das Token als gueltig — ein totes Token kostet nur
    einen 401, und fetch_usage nimmt danach das naechste."""
    if not isinstance(data, dict):
        return []
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    blocks = [data.get("claudeAiOauth"), data.get("claude_ai_oauth"), data]
    out = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        tok = next((blk[k] for k in _CLI_TOKEN_KEYS
                    if isinstance(blk.get(k), str) and blk[k]), None)
        if not tok:
            continue
        exp = next((blk[k] for k in _CLI_EXPIRY_KEYS
                    if isinstance(blk.get(k), (int, float))), None)
        if exp is not None and exp <= now_ms:
            continue                                 # abgelaufen -> gar nicht erst senden
        out.append((exp if exp is not None else float("inf"), tok))
        break                                        # erster Treffer gewinnt, kein Doppel
    return [t for _, t in sorted(out, key=lambda p: p[0], reverse=True)]


def _read_tokens_from_cli():
    """Tokens der CLI. Wirft, wenn die Datei fehlt oder unlesbar ist — der Aufrufer
    faellt dann auf Claude Desktop zurueck."""
    return tokens_from_credentials(json.loads(
        _read_shared(cli_credentials_path()).decode("utf-8")))


# ── Token aus Claude Desktop lesen (mit Cache) ───────────
_token_cache = []


def _read_tokens_from_disk():
    cdir = claude_dir()
    if not cdir:
        raise FileNotFoundError("Claude-Desktop-Ordner nicht gefunden")
    blob = base64.b64decode(json.loads(_read_shared(
        os.path.join(cdir, "config.json")))["oauth:tokenCache"])
    if blob[:3] != b"v10":
        raise RuntimeError("Unerwartetes Token-Format (App-Bound?)")
    key = _dpapi_unprotect(base64.b64decode(json.loads(_read_shared(
        os.path.join(cdir, "Local State")))["os_crypt"]["encrypted_key"])[5:])
    cache = json.loads(_decrypt_v10(blob, key))
    # Claude legt mehrere Eintraege an (Scopes/Ablaufzeiten). Alle noch gueltigen
    # sammeln, langlebigstes zuerst, der Rest als Fallback bei 401/403.
    now_ms = time.time() * 1000
    tokens = sorted(
        ((e["expiresAt"], e["token"]) for e in cache.values()
         if e.get("token") and e.get("expiresAt", 0) > now_ms),
        reverse=True)
    if not tokens:
        raise RuntimeError("Token abgelaufen – Claude Desktop oeffnen")
    return [tok for _, tok in tokens]


class NoTokenError(RuntimeError):
    """Keine der beiden Quellen hat ein brauchbares Token hergegeben."""


def read_oauth_token(force=False):
    """Alle verfuegbaren Tokens, CLI zuerst.

    Die Reihenfolge ist Absicht: die CLI ist die Quelle, die JEDER Deck-Nutzer hat
    (ohne sie gaebe es keine Agenten), Claude Desktop ist die Zusatzquelle. Faellt
    eine aus, traegt die andere — beide Fehler zusammen sind erst ein Fehler."""
    global _token_cache
    if not force and _token_cache:
        return _token_cache
    tokens, errors = [], []
    for label, src in (("CLI", _read_tokens_from_cli), ("Desktop", _read_tokens_from_disk)):
        try:
            tokens += src() or []
        except Exception as e:                       # Quelle fehlt/kaputt -> die andere zaehlt
            errors.append(f"{label}: {type(e).__name__}")
    if not tokens:
        raise NoTokenError("; ".join(errors) or "kein Token gefunden")
    _token_cache = tokens
    return _token_cache


# ── Usage-API abfragen ───────────────────────────────────
def _fetch_one(token):
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "agent-deck-usage/1",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_usage(tokens):
    """Probiert die Tokens der Reihe nach; nimmt das erste, das 200 liefert.
    401/403 -> naechstes Token; alles andere (429, 5xx, Timeout) fliegt hoch."""
    if isinstance(tokens, str):
        tokens = [tokens]
    last_err = None
    for tok in tokens:
        try:
            return _fetch_one(tok)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (401, 403):
                continue
            raise
    raise last_err if last_err else RuntimeError("Kein Token verfuegbar")


# ── Pure Parser (headless testbar) ───────────────────────
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


# ── Hintergrund-Poller ───────────────────────────────────
def _empty_snapshot():
    return {"state": "pending", "session_percent": None, "session_severity": "",
            "session_resets_at": None, "limits": [], "error": None, "ts": None}


class UsagePoller:
    """Fragt die Claude-Nutzung in einem Daemon-Thread ab und haelt den letzten
    Snapshot thread-sicher. Die UI liest ihn per snapshot() aus ihrem eigenen
    after()-Timer (kein Tk-Zugriff aus dem Thread).

    Bis zum ersten Erfolg wird schnell gepollt (die frisch startende Claude-App
    schreibt ihre Dateien staendig neu). Danach im ruhigen poll_seconds-Takt mit
    etwas Jitter – Nutzung aendert sich langsam, und ein groesserer Takt schont das
    API-Rate-Limit (v.a. wenn parallel der Usage-Monitor pollt). Auf 429/Netzfehler
    bleibt der letzte Wert stehen (nur der Fehlertext wird vermerkt)."""

    def __init__(self, poll_seconds=120):
        self.poll_seconds = max(30, int(poll_seconds))
        self._snap = _empty_snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="usage-poll", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def _set(self, **kw):
        with self._lock:
            self._snap.update(kw)

    def poll_once(self):
        """Ein Abruf ueber den gemeinsamen Poller (Fallback: lokaler Direktabruf);
        aktualisiert den Snapshot. Fehler landen im 'error'-Feld, harte Fehler
        (Token/Ordner weg) blenden zusaetzlich die Zahl aus."""
        sh = _shared()
        if sh is not None:
            try:
                snap = sh.get_usage()
            except Exception as e:
                self._fail(f"{type(e).__name__}", hard=False)
                return
            data, err = snap.get("data"), snap.get("error")
            if data is None:
                el = (err or "").lower()
                if "ungueltig" in el:
                    self._fail(i18n.L("Token ungueltig – 'claude auth login'",
                                      "Token invalid – run 'claude auth login'"), hard=True)
                elif "rate" in el:
                    self._fail(i18n.L("Rate-Limit – kurz warten",
                                      "Rate limit – wait a moment"), hard=False)
                else:
                    self._fail(err or i18n.L("warte auf Daten…", "waiting for data…"),
                               hard=False)
                return
            # Gueltige Zahl aus dem gemeinsamen Cache (bleibt bis zum Reset korrekt,
            # auch wenn der letzte Abruf ein 429 war).
            parsed = parse_usage(data)
            sess = parsed["session"]
            self._set(state="ok",
                      session_percent=(sess["percent"] if sess else None),
                      session_severity=(sess["severity"] if sess else ""),
                      session_resets_at=(sess["resets_at"] if sess else None),
                      limits=parsed["limits"], error=None, ts=time.time())
            return

        # ── Fallback: lokaler Direktabruf (shared-Modul nicht gefunden) ──
        try:
            try:
                data = fetch_usage(read_oauth_token())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):                 # gecachtes Token rotiert?
                    data = fetch_usage(read_oauth_token(force=True))
                else:
                    raise
            parsed = parse_usage(data)
            sess = parsed["session"]
            self._set(state="ok",
                      session_percent=(sess["percent"] if sess else None),
                      session_severity=(sess["severity"] if sess else ""),
                      session_resets_at=(sess["resets_at"] if sess else None),
                      limits=parsed["limits"], error=None, ts=time.time())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self._fail(i18n.L("Token ungueltig – 'claude auth login'",
                                  "Token invalid – run 'claude auth login'"), hard=True)
            elif e.code == 429:
                self._fail(i18n.L("Rate-Limit – kurz warten",
                                  "Rate limit – wait a moment"), hard=False)
            else:
                self._fail(f"HTTP {e.code}", hard=False)
        except (NoTokenError, FileNotFoundError):
            # Weder CLI noch Desktop haben ein Token. Der Hinweis zeigt auf die CLI:
            # die hat jeder Deck-Nutzer, und ein Login dort ist der kuerzere Weg.
            self._fail(i18n.L("Nicht angemeldet – 'claude auth login'",
                              "Not signed in – run 'claude auth login'"), hard=True)
        except Exception as e:
            self._fail(f"{type(e).__name__}", hard=False)

    def _fail(self, msg, hard):
        with self._lock:
            self._snap["state"] = "error"
            self._snap["error"] = msg
            if hard:
                self._snap["session_percent"] = None
                self._snap["session_severity"] = ""
                self._snap["limits"] = []

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass                                     # die Schleife darf nie sterben
            with self._lock:
                have = self._snap["session_percent"] is not None
                errored = self._snap["error"] is not None
            # Mit gemeinsamem Poller ist poll_once nur ein guenstiger Cache-Read
            # (der API-Takt + Backoff steckt zentral im shared-Modul). Dann darf
            # das Badge oefter spiegeln, ohne das Rate-Limit zu belasten.
            base = 30 if _shared() is not None else self.poll_seconds
            if have:
                delay = base + random.uniform(-8, 8)     # Jitter gegen Lockstep
            elif errored:
                delay = min(30, self.poll_seconds)       # 429/Token/kein Claude -> zurueckfallen,
                                                         # NICHT alle 5 s weiterhaemmern
            else:
                delay = 5                                # frischer Start: bis zur ersten Zahl fix
            self._stop.wait(max(3.0, delay))
