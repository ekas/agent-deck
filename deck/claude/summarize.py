"""KI-Kurzzusammenfassung ("worum geht's") eines Agenten-Chats fuer den Hover-Tooltip.

Beim Hover ueber eine Agent-Kachel soll nicht mehr die zuletzt gestellte Frage
stehen, sondern EIN knapper Satz, worum es in dem Chat geht. Datenweg:

  1) report.py merkt sich je Slot `session_id` + `cwd` in state/<slot>.json.
  2) Daraus finden wir das Claude-Code-Transcript
     ~/.claude/projects/<enc-cwd>/<session-id>.jsonl (Fallback: per Glob ueber die
     eindeutige session-id, deckt auch worktree-cwd ab).
  3) `extract_turns` + `build_digest` ziehen daraus einen kompakten Gespraechs-
     Auszug (erster User-Zug = Thema + juengste Zuege = aktueller Fokus).
  4) `_run_claude` ruft `claude` HEADLESS im `--safe-mode` auf (CLAUDE.md, Skills,
     Hooks, MCP, Plugins AUS -> schnell und ohne agentisches Tool-Loopen; OAuth-Auth
     + Modellwahl bleiben, kein API-Key noetig). Ergebnis wird pro Session gecacht
     (state/summaries/<session-id>.json) und nur bei echtem Transcript-Zuwachs neu
     erzeugt -> Hover ist danach sofort und kostet fast nichts.

Aus demselben Transcript zieht `ensure_refs` ausserdem die Bezuege des Chats – die
TICKET-ID (`find_ticket`) und die PULL-REQUEST-Nummer (`find_pr`), reine Regex, kein
Modell. Sie stehen im Hover-Tooltip ueber der Zusammenfassung und auf der Karte. Das
laeuft unabhaengig davon, ob dem Agenten per Rechtsklick ein Ticket zugewiesen wurde:
erkannt wird, was im Gespraech steht. Ergebnis liegt im selben Session-Cache (Felder
"ticket"/"pr").

Reine stdlib. Der Aufruf blockiert (subprocess) und gehoert NICHT auf den Tk-Thread;
agent_deck ruft `generate()` aus einem Daemon-Thread. `extract_turns`/`build_digest`/
`clean_summary`/`find_ticket`/`find_pr` sind pur und unit-getestet (tests/test_claude_summarize.py).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time

from deck.claude.refs import find_refs
from deck.domain import paths

# Auf Windows (pythonw) kein kurz aufblitzendes Konsolenfenster fuer claude.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Ein claude-Aufruf ist schwer (~260 MB Node-Bundle, ~8 s Startup). Beim proaktiven
# Vorab-Erzeugen koennten sonst fuer alle offenen Agenten gleichzeitig Prozesse
# hochfahren -> global auf wenige parallele Laeufe deckeln (der Rest wartet im Thread).
MAX_CONCURRENT = 2
_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)

# Claude-Code-Transcripts: ~/.claude/projects/<enc-cwd>/<session-id>.jsonl
PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# Cache der erzeugten Zusammenfassungen (je Session eine winzige JSON-Datei).
SUMMARY_DIR = os.path.join(paths.STATE_DIR, "summaries")

# Anweisung an das Modell (je Sprache eine Fassung). Der Digest wird direkt
# angehaengt. Die verlangte Ausgabesprache folgt der Deck-Sprache (i18n) -> die
# Kurzzusammenfassung im Hover-Tooltip ist deutsch bzw. englisch wie der Rest der
# Oberflaeche.
_INSTRUCTION_DE = (
    "Unten steht der Verlauf eines Coding-Chats zwischen einem Nutzer und einem "
    "KI-Agenten. Fasse in EINEM knappen deutschen Satz (hoechstens 12 Woerter) "
    "zusammen, WORUM es in dem Chat geht bzw. woran gerade gearbeitet wird - nur das "
    "Thema/die Aufgabe, keine Meta-Kommentare ueber den Chat selbst. Antworte "
    "ausschliesslich mit diesem einen Satz: ohne Anfuehrungszeichen, ohne Praefix, "
    "ohne Aufzaehlung, und benutze KEINE Tools.\n\n--- Chat ---\n"
)
_INSTRUCTION_EN = (
    "Below is the history of a coding chat between a user and an AI agent. "
    "Summarize in ONE concise English sentence (at most 12 words) WHAT the chat is "
    "about / what is currently being worked on - just the topic/task, no "
    "meta-commentary about the chat itself. Reply with only that single sentence: "
    "no quotation marks, no prefix, no bullet list, and do NOT use any tools."
    "\n\n--- Chat ---\n"
)


def instruction(lang):
    """Modell-Anweisung fuer die gewuenschte Sprache ("english" -> englisch, sonst
    deutsch). Pur -> unit-testbar, kein i18n-Import noetig."""
    return _INSTRUCTION_EN if lang == "english" else _INSTRUCTION_DE

_ROLE = {"user": "User", "assistant": "Assistant"}
_QUOTES = "\"'“”„»«‚‘’"


# ── pure Helfer (unit-getestet) ──────────────────────────────────────────
def extract_turns(lines):
    """Aus den JSONL-Zeilen eines Transcripts die echten Gespraechs-Zuege ziehen:
    getippte User-Nachrichten (message.content ist ein STRING) und die Text-Bloecke
    der Assistant-Antworten. Uebersprungen werden Tool-Aufrufe/-Ergebnisse (User-
    content ist dann eine Liste), Thinking, System-/Attachment-Zeilen und einge-
    schobene Slash-Kommando-/Caveat-Bloecke (beginnen mit '<' bzw. 'Caveat:').
    `lines` darf Roh-Strings ODER schon geparste dicts liefern."""
    turns = []
    for ln in lines:
        if isinstance(ln, dict):
            rec = ln
        else:
            s = (ln or "").strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except Exception:
                continue
        if not isinstance(rec, dict):
            continue
        t = rec.get("type")
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if t == "user":
            if not isinstance(content, str):
                continue                       # Liste = Tool-Ergebnis -> kein echter Text
            text = content.strip()
            if not text or text[0] == "<" or text.startswith("Caveat:"):
                continue                       # System-/Command-/Caveat-Einschub
            turns.append(("user", text))
        elif t == "assistant":
            if not isinstance(content, list):
                continue
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(p.strip() for p in parts if p and p.strip()).strip()
            if text:
                turns.append(("assistant", text))
    return turns


def build_digest(turns, max_chars=3500, per_turn=600):
    """Aus den Zuegen einen kompakten Digest bauen: den ERSTEN Zug (setzt das Thema)
    plus die JUENGSTEN Zuege, bis das Zeichenbudget voll ist -> das Modell bekommt
    Thema UND aktuellen Fokus, ohne das (evtl. riesige) ganze Transcript. Jeder Zug
    wird whitespace-geglaettet und auf per_turn Zeichen gekuerzt."""
    if not turns:
        return ""

    def fmt(role, text):
        text = " ".join(text.split())
        if len(text) > per_turn:
            text = text[:per_turn].rstrip() + "…"
        return f"{_ROLE.get(role, role)}: {text}"

    first = fmt(*turns[0])
    picked, total = [], len(first)
    for role, text in reversed(turns[1:]):
        line = fmt(role, text)
        if total + len(line) + 1 > max_chars:
            break
        picked.append(line)
        total += len(line) + 1
    picked.reverse()
    lines = [first]
    if len(picked) < len(turns) - 1:
        lines.append("…")                      # Luecke zwischen Thema und Fokus
    lines.extend(picked)
    return "\n".join(lines)


def clean_summary(raw, max_len=200):
    """Modell-Ausgabe zu einer sauberen Tooltip-Zeile normalisieren: ersten nicht-
    leeren Absatz nehmen, umschliessende Anfuehrungszeichen und ein evtl. Praefix
    ('Zusammenfassung:'/'Thema:'/…) weg, Whitespace glaetten, hart auf max_len
    kuerzen. Kein/kein-String -> ''."""
    if not isinstance(raw, str):
        return ""
    text = ""
    for ln in raw.splitlines():
        if ln.strip():
            text = ln.strip()
            break
    if not text:
        return ""
    if len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text[1:-1].strip()
    low = text.lower()
    for pre in ("zusammenfassung:", "thema:", "summary:", "worum es geht:", "topic:"):
        if low.startswith(pre):
            text = text[len(pre):].strip()
            break
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def enc_cwd(cwd):
    """cwd -> Ordnername, wie Claude Code ihn unter ~/.claude/projects nutzt: jedes
    Zeichen ausser [A-Za-z0-9] wird zu '-' (also auch ':', '\\', Umlaute)."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


# ── unreine Helfer (Dateisystem / subprocess) ─────────────────────────────
def _safe_mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _safe_size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def transcript_path(session_id, cwd=None):
    """Pfad zum Transcript einer Session finden. Schneller Direktweg ueber die
    kodierte cwd; sonst per Glob ueber die (eindeutige) session-id, was auch einen
    worktree-cwd abdeckt. None, wenn nichts gefunden."""
    if not session_id:
        return None
    if cwd:
        direct = os.path.join(PROJECTS_DIR, enc_cwd(cwd), session_id + ".jsonl")
        if os.path.isfile(direct):
            return direct
    hits = glob.glob(os.path.join(PROJECTS_DIR, "*", session_id + ".jsonl"))
    if not hits:
        return None
    return max(hits, key=_safe_mtime)


def _read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return []


def _cache_path(session_id):
    return os.path.join(SUMMARY_DIR, session_id + ".json")


def read_cache(session_id):
    if not session_id:
        return None
    return paths.load_json(_cache_path(session_id), None)


def cached_summary(session_id):
    """Nur die schon gecachte Zusammenfassung (schnell, KEIN subprocess). None, wenn
    noch keine erzeugt wurde."""
    return (read_cache(session_id) or {}).get("summary") or None


def cached_refs(session_id):
    """Nur die schon erkannten Bezuege aus dem Cache, ohne Transcript zu lesen:
    {"ticket": …, "pr": …} (leere Strings, wenn nichts gescannt/gefunden wurde)."""
    c = read_cache(session_id) or {}
    return {"ticket": c.get("ticket") or "", "pr": c.get("pr") or ""}


def _merge_cache(session_id, **fields):
    """Cache-Datei der Session frisch lesen und nur die uebergebenen Felder ersetzen.
    Wichtig, weil Ticket-Scan und Zusammenfassung dieselbe Datei benutzen: die
    Zusammenfassung braucht ~10 s, in der Zeit darf ein Ticket-Scan nicht verloren
    gehen (und umgekehrt)."""
    data = read_cache(session_id) or {}
    data.update(fields)
    paths.save_json(_cache_path(session_id), data)
    return data


def ensure_refs(session_id, cwd=None, project=None):
    """Ticket-ID + PR-Nummer der Session bestimmen, cachen und zurueckgeben
    ({"ticket": …, "pr": …}, leer = nichts im Chat).

    Rein lokal: Transcript lesen + Regex (find_refs), KEIN claude-Aufruf. Das Lesen
    kann bei langen Chats ein paar MB sein -> aus einem Daemon-Thread aufrufen, nicht
    vom Tk-Thread. Neu gescannt wird nur, wenn das Transcript seit dem letzten Scan
    gewachsen/geschrumpft ist; sonst kommt der gecachte Stand sofort zurueck."""
    if not session_id:
        return {"ticket": "", "pr": ""}
    cache = read_cache(session_id) or {}
    old = {"ticket": cache.get("ticket") or "", "pr": cache.get("pr") or ""}
    path = transcript_path(session_id, cwd)
    if not path:
        return old
    size = _safe_size(path)
    if cache.get("refs_size") == size:
        return old                             # Transcript unveraendert -> kein Scan
    refs = find_refs(extract_turns(_read_lines(path)), project)
    # Ein leeres Ergebnis wird MIT der Groesse gemerkt -> ein Chat ohne Ticket/PR wird
    # nicht bei jedem Poll neu durchsucht. Schon gefundene Werte bleiben dabei stehen
    # (ein gekuerztes/rotiertes Transcript soll sie nicht loeschen).
    refs = {k: (refs.get(k) or old[k]) for k in old}
    _merge_cache(session_id, refs_size=size, **refs)
    return refs


def _run_claude(prompt, model, timeout):
    """`claude` HEADLESS im safe-mode aufrufen und die reine Textantwort liefern.
    safe-mode schaltet CLAUDE.md/Skills/Hooks/MCP/Plugins ab (schnell, kein
    agentisches Tool-Loopen), OAuth-Auth + Modellwahl bleiben. None bei jedem
    Fehler (claude fehlt, Timeout, rc!=0) - der Aufrufer behaelt dann den Cache."""
    exe = shutil.which("claude")
    if not exe:
        return None
    args = [exe, "-p", "--safe-mode", "--no-session-persistence", "--model", model]
    try:
        os.makedirs(SUMMARY_DIR, exist_ok=True)
        p = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW, cwd=SUMMARY_DIR)
        return p.stdout if p.returncode == 0 else None
    except Exception:
        return None


def generate(session_id, cwd=None, model="haiku", lang="german", min_growth=8000,
             cooldown=45.0, timeout=60, max_chars=2400):
    """Zusammenfassung fuer eine Session sicherstellen und zurueckgeben (BLOCKIEREND
    -> nur aus einem Daemon-Thread aufrufen). claude wird NUR neu aufgerufen, wenn es
    noch keinen Cache gibt, die gecachte Fassung in einer ANDEREN Sprache ist ODER das
    Transcript seit dem letzten Mal um >= min_growth Bytes gewachsen ist UND der letzte
    Lauf >= cooldown Sekunden her ist; sonst kommt der gecachte Satz sofort zurueck.
    Die Gates sind bewusst grob: das Thema aendert sich kaum turn-zu-turn, und der
    Cooldown deckelt die Neu-Erzeugung bei einem gerade schnell wachsenden
    (arbeitenden) Chat -> ein 'fertiger' Agent bleibt beim Hover sofort, ein busy Agent
    floodet nicht. Ein Sprachwechsel setzt sich sofort durch (Cache-Sprache != lang ->
    neu erzeugen). Bei jedem Fehlschlag bleibt der bisherige Cache-Wert erhalten. Der
    eigentliche claude-Lauf ist global auf MAX_CONCURRENT gedeckelt (_SEM)."""
    if not session_id:
        return None
    path = transcript_path(session_id, cwd)
    cache = read_cache(session_id) or {}
    cached = cache.get("summary") or ""
    if not path:
        return cached or None                  # nichts zu lesen -> was (falls) da ist
    size = _safe_size(path)
    # Alt-Caches ohne "lang" gelten als deutsch (bisheriges Verhalten).
    same_lang = cache.get("lang", "german") == lang
    if cached and same_lang:
        grown = size - int(cache.get("size", 0))
        age = time.time() - float(cache.get("ts", 0))
        if grown < min_growth or age < cooldown:
            return cached                      # frisch genug / zu bald -> kein neuer Aufruf
    digest = build_digest(extract_turns(_read_lines(path)), max_chars=max_chars)
    if not digest:
        return cached or None
    with _SEM:                                 # nur wenige claude-Prozesse gleichzeitig
        out = _run_claude(instruction(lang) + digest, model, timeout)
    summary = clean_summary(out)
    if not summary:
        return cached or None                  # Fehlschlag -> alten Wert behalten
    # Nur die Summary-Felder ersetzen: ein waehrenddessen gelaufener Ticket-Scan
    # (ensure_refs schreibt dieselbe Datei) soll nicht ueberbuegelt werden.
    _merge_cache(session_id, summary=summary, size=size, ts=time.time(), lang=lang)
    return summary


def prune(max_age_days=14):
    """Alte Cache-Dateien laengst geschlossener Sessions entfernen (best effort;
    einmal beim Deck-Start aufgerufen)."""
    try:
        cutoff = time.time() - max_age_days * 86400
        for f in glob.glob(os.path.join(SUMMARY_DIR, "*.json")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except OSError:
                pass
    except Exception:
        pass
