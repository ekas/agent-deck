"""Woher das OAuth-Token kommt - und wie es entschluesselt wird.

Zwei Quellen: die Claude-Code-CLI (Klartext in ~/.claude/.credentials.json) und Claude
Desktop (verschluesselt). Fuer Desktop braucht es die Kette DPAPI -> AES-256-GCM, beides
ueber Windows-eigene DLLs (crypt32, bcrypt) - deshalb bleibt das Deck auch hier ohne
Fremdpaket. Schlaegt das fehl, dient 'cryptography' als optionaler Rueckfall.

Gelesen wird defensiv: Claude Desktop haelt seine Dateien offen, ein Lesen mit Sperre
wuerde die App blockieren.
"""
from ctypes import wintypes
import base64
import ctypes
import glob
import json
import os
import time


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
