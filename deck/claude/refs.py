"""Ticket- und PR-Nummer aus einem Chat lesen - reine Regex, kein Modell.

Das Problem sind nicht die Treffer, sondern die Falsch-Positiven: eine Zahl mit Raute
ist noch kein Pull Request, und ein Wort mit Bindestrich noch kein Jira-Key. Darum
zaehlt jeder Fund Punkte (Kontextwoerter, URL-Form, Wiederholung) und muss eine
Mindestschwelle reissen.
"""
import re


# ── Ticketnummer aus dem Chat ziehen (pur, unit-getestet) ────────────────
# Jira-Key-Form PROJEKT-123. BEWUSST case-sensitiv gross: sonst wuerde jedes
# "python-3" / "top-10" / "schritt-2" aus dem Fliesstext als Ticket durchgehen. Die
# ID des KONFIGURIERTEN Projekts (project=, z.B. "PROJ") wird zusaetzlich case-
# insensitiv erkannt – die steht oft klein in Branch-/Pfadnamen ("ticket/proj-2691").
# Das Lookbehind sperrt auch '-'/'_': sonst wird aus Regel-/Normkennungen wie
# "CIS-DI-0006" der Teil "DI-0006" herausgeschnitten und als Ticket gemeldet.
_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]{1,9})-(\d{1,6})(?!\d)")

# Technik-Kuerzel, die zufaellig wie ein Jira-Key aussehen (UTF-8, SHA-256, CVE-2021…).
# Ein Treffer auf das konfigurierte Projekt wird NIE gefiltert, die Liste kann also
# ruhig grob sein.
# Bewusst NICHT drin: Kuerzel, die gut ein echtes Jira-Projekt sein koennen (DEV,
# TEST, PROD, QA, BUILD …) – die tauchen im Fliesstext kaum GROSS mit "-<Zahl>" auf.
_KEY_STOP = frozenset("""
UTF UTF8 ISO RFC CVE CWE SHA MD5 AES RSA TLS SSL HTTP HTTPS IP IPV EN DIN IEC IEEE
ANSI ASCII CP WIN MACOS OSX GPT LLAMA NODE PY ES PEP PHP NET
UUID URL URI SQL XML HTML CSS JSON YAML TOML CSV PDF PNG JPG JPEG SVG GIF MP3 MP4
RGB RGBA ARGB HSL HSV CMYK DPI PPI PX EM REM VW VH FPS HZ KHZ MHZ GHZ
KB MB GB TB MS NS SEC MIN UTC GMT CET CEST
CPU GPU RAM SSD HDD USB PCI SATA LTS EOL EOF LF CRLF BOM
REV VER V N X Y Z K M PART STEP PHASE TIER TOP LEVEL COVID SARS RC
""".split())

# Woerter, nach denen eine BLOSSE Nummer als Ticket zaehlt ("Ticket 2701", "Issue #42")
# bzw. die einen Key-Treffer in ihrer Naehe glaubwuerdiger machen.
_CTX_WORDS = ("ticket", "issue", "jira", "bug", "story", "vorgang", "karte")
_NUM_CTX_RE = re.compile(
    r"(?:%s)s?\b[\s:#/-]*#?(\d{2,6})(?!\d)" % "|".join(_CTX_WORDS), re.I)


def _ctx_bonus(text, pos, span=24):
    """Steht kurz VOR der Fundstelle ein Wort wie 'Ticket'/'Jira'? -> Bonuspunkte."""
    before = text[max(0, pos - span):pos].lower()
    return 2 if any(w in before for w in _CTX_WORDS) else 0


def _iter_turns(turns):
    """Ueber (rolle, text, gewicht, index) laufen und Muell (None, halbe Tupel, Nicht-
    Strings) ueberspringen. Gewicht: was DU sagst zaehlt doppelt – der Agent echot IDs
    nur nach, du nennst sie, weil es darum geht."""
    if isinstance(turns, str):
        turns = [("user", turns)]
    for idx, turn in enumerate(turns or ()):
        if not isinstance(turn, (tuple, list)) or len(turn) != 2:
            continue
        role, text = turn
        if not isinstance(text, str) or not text:
            continue
        yield role, text, (2 if role == "user" else 1), idx


def _best(hits, min_score):
    """Aus {kandidat: [punkte, letzter-index]} den Gewinner: meiste Punkte, bei
    Gleichstand der ZULETZT erwaehnte. Unter min_score -> '' (lieber nichts anzeigen
    als etwas Falsches)."""
    if not hits:
        return ""
    key, (score, _idx) = max(hits.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    return key if score >= min_score else ""


def find_ticket(turns, project=None, min_score=2):
    """Aus den Gespraechs-Zuegen die Ticket-ID herausziehen, um die es geht ('' = keine).

    `turns` ist die Liste aus extract_turns (oder ein einzelner String). Gewertet wird
    nach Haeufigkeit, nicht nach dem ersten Treffer: erwaehnt der Chat nebenbei einen
    fremden Key, gewinnt trotzdem der, um den es wirklich geht. Gewichte: was DU sagst
    zaehlt doppelt (der Agent echot Keys nur nach), ein Treffer auf das konfigurierte
    Jira-Projekt dreifach, ein 'Ticket …' direkt davor gibt Bonus. Bei Gleichstand
    gewinnt die ZULETZT erwaehnte ID (das Gespraech ist weitergezogen). min_score haelt
    einmalige Nebenbei-Nennungen des Agenten (z.B. ein Regel-/Normkuerzel im Fliesstext)
    draussen: lieber keine ID im Hover als eine falsche.

    Pur -> unit-getestet; `project` (z.B. "PROJ") kommt vom Aufrufer, damit dieses
    Modul weiter ohne config-Import auskommt."""
    proj = (project or "").strip().upper()
    proj_re = re.compile(r"(?<![A-Za-z0-9_-])%s-(\d{1,6})(?!\d)" % re.escape(proj),
                         re.I) if proj else None
    hits = {}                                  # "ABC-123" -> [Punkte, letzter Zug-Index]

    def bump(key, weight, idx):
        rec = hits.get(key)
        if rec is None:
            hits[key] = [weight, idx]
        else:
            rec[0] += weight
            rec[1] = idx

    for role, text, w, idx in _iter_turns(turns):
        for m in _KEY_RE.finditer(text):
            pre, num = m.group(1), m.group(2)
            if pre == proj:
                continue                       # deckt proj_re unten ab (sonst doppelt)
            if pre in _KEY_STOP:
                continue
            ctx = _ctx_bonus(text, m.start())
            if len(num) < 2 and not ctx:
                continue        # einstellig nur mit 'Ticket …' davor (sonst UTF-8 & Co.)
            bump(pre + "-" + num, w + ctx, idx)
        if proj_re:
            for m in proj_re.finditer(text):
                bump(proj + "-" + m.group(1), w * 3, idx)
            for m in _NUM_CTX_RE.finditer(text):
                bump(proj + "-" + m.group(1), w, idx)   # "Ticket 2701" -> PROJ-2701
    return _best(hits, min_score)


# ── Pull-Request-Nummer aus dem Chat ziehen ──────────────────────────────
# Oft geht es nicht um ein Ticket, sondern um einen PR ("Bugs im Owner-Endpoint aus
# PR #62 fixen"). Drei Quellen, absteigend zuverlaessig:
#  1) die URL des PR/MR (GitHub /pull/62, GitLab /-/merge_requests/62)
#  2) "PR #62" / "pull request 62" / "MR 62" – Nummer MIT Schluesselwort davor
#  3) ein blosses "#62" – nur mit halbem Vertrauen (siehe min_score), weil das genauso
#     eine Issue-/Kommentar-Nummer sein kann.
_PR_URL_RE = re.compile(
    r"(?:github\.com/[^\s/]+/[^\s/]+/pull/|/-/merge_requests/)(\d{1,6})(?!\d)", re.I)
_PR_CTX_RE = re.compile(
    r"\b(?:pull[\s_-]?requests?|merge[\s_-]?requests?|prs?|mrs?)\b[\s:#/_-]*#?(\d{1,6})(?!\d)",
    re.I)
_HASH_RE = re.compile(r"(?<![A-Za-z0-9_#])#(\d{1,6})(?![0-9A-Za-z])")
# Vor einem blossen "#42" heisst das: KEIN Pull Request (Jira-Vorgang, Zeilennummer …).
_HASH_NOT_PR = ("issue", "ticket", "jira", "zeile", "line", "seite", "page",
                "kommentar", "comment", "spalte", "column")


def find_pr(turns, min_score=3):
    """Aus den Gespraechs-Zuegen die Pull-/Merge-Request-Nummer ziehen ('' = keine).
    Rueckgabe ist die reine Nummer als String ("62") – wie sie beschriftet wird,
    entscheidet die Oberflaeche.

    Wertung wie bei find_ticket (Haeufigkeit, User zaehlt doppelt, bei Gleichstand der
    juengste Treffer). Der Default min_score=3 ist bewusst haerter als beim Ticket:
    eine EINZELNE "#62"-Erwaehnung reicht damit nicht, "PR #62" vom Nutzer (2x2) oder
    eine PR-URL schon – sonst landet jede Issue-Nummer als PR im Hover."""
    hits = {}

    def bump(num, weight, idx):
        rec = hits.get(num)
        if rec is None:
            hits[num] = [weight, idx]
        else:
            rec[0] += weight
            rec[1] = idx

    for role, text, w, idx in _iter_turns(turns):
        for m in _PR_URL_RE.finditer(text):
            bump(m.group(1), w * 3, idx)
        for m in _PR_CTX_RE.finditer(text):
            bump(m.group(1), w * 2, idx)
        for m in _HASH_RE.finditer(text):
            before = text[max(0, m.start() - 24):m.start()].lower()
            if any(word in before for word in _HASH_NOT_PR):
                continue                       # "Issue #42"/"Zeile #42" ist kein PR
            bump(m.group(1), w, idx)           # blosses "#62": halbes Vertrauen
    return _best(hits, min_score)


def find_refs(turns, project=None):
    """Ticket UND PR in EINEM Durchgang: {"ticket": "ABC-123"|"", "pr": "62"|""}.
    Beides kann gleichzeitig gelten (ein PR zu einem Ticket) – die Oberflaeche zeigt
    dann beides."""
    return {"ticket": find_ticket(turns, project), "pr": find_pr(turns)}
