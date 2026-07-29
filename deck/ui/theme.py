"""Farben, Timings und Anzeigetexte des Panels.

Diese Werte lagen früher auf der Modulebene von panel.py. Sie stehen hier, weil
die ui-Mixins sie brauchen: würden sie aus panel.py importieren, das die Mixins
selbst einbindet, entstünde ein Zirkelbezug.

Die Fallback-Werte in getattr(cfg, ...) greifen nur, wenn ein config-Eintrag fehlt.
"""

from deck import i18n
from deck.domain import config as cfg
from deck.render.kit import BG, CARD_BORDER, CARD_FILL, INK_3
from deck.render.kit import mix as _mix

# ── FROSTPANE-Palette (BG/CARD_FILL/INK…) lebt jetzt in canvas_kit ───────
# Aus der schwarzen Kiste wird eine dunkel getönte Scheibe; jede Karte glüht ruhig
# in ihrer Statusfarbe. Bewusst WEGGELASSEN (nach Vorgabe): der Leucht-Streifen an
# der linken Kante und der Status-Punkt in der Ecke. Die Grundfarben sind oben aus
# canvas_kit importiert; die Status-/Glow-Farben bleiben unten deck-spezifisch.

# Status -> Anzeigetext, je (deutsch, englisch) – i18n.L() waehlt beim Zeichnen die
# aktuelle Sprache (interne Namen aus Hooks/report.py bleiben unveraendert;
# running+thinking teilen sich "denkt"/"thinking", done = ungelesene Antwort).
STATUS_LABEL = {
    "idle": ("idle", "idle"), "done": ("ungelesen", "unread"),
    "thinking": ("denkt", "thinking"), "running": ("denkt", "thinking"),
    "waiting": ("Rückfrage", "needs input"), "none": ("—", "—"),
}


def status_label(status):
    """Lokalisierter Status-Anzeigetext (schaltet mit der Deck-Sprache)."""
    return i18n.L(*STATUS_LABEL.get(status, ("idle", "idle")))
# Status -> (Glow-Farbe, Glow-Intensität 0..1, atmet?, Füll-Tönung 0..1). Die Karte
# wird um die Füll-Tönung in ihre Statusfarbe eingefärbt (dunkles Glas), der Halo
# trägt den Status zusätzlich; "denkt"/"Rückfrage" pulsieren langsam, der Rest ruhig.
GLOW_STYLE = {
    "idle":     ("#8b8b99", 0.22, False, 0.06),  # grau  – fast neutral, "nichts zu tun"
    "done":     ("#6ee7a8", 0.85, False, 0.28),  # grün  – ungelesene Antwort
    "thinking": ("#7ecbff", 1.00, True,  0.28),  # cyan  – denkt (atmet)
    "running":  ("#7ecbff", 1.00, True,  0.28),  # cyan  – arbeitet (atmet)
    "waiting":  ("#ffc48a", 1.00, True,  0.30),  # amber – braucht dich (atmet)
    "none":     ("#8b8b99", 0.00, False, 0.00),  # kein Glow, kein Farbton
}
LOST_FILL = 0.30   # Füll-Tönung für "getrennt" (rot)
# Rot ist KEIN gemeldeter Agent-Status, sondern wird im Panel berechnet: die
# Extension des Fensters haengt nicht (mehr) am Broker -> Verbindung verloren.
# Anzeigetext lokalisiert (i18n.L) direkt am Zeichen-Aufruf, damit er mit der
# Deck-Sprache umschaltet.
LOST_GLOW  = "#ff6b6b"

# Border-Akzente: heben Auswahl / Rückfrage / Verlust zusätzlich zum Glow hervor.
SEL_BORDER  = "#ffffff"
WAIT_BORDER = "#ffc48a"
# Ticket-ID auf der Karte (Zeile zwischen Modell und Status): dezentes Violett –
# klar von den Statusfarben (cyan/grün/amber/rot) unterscheidbar.
TICKET_INK  = "#b7a6ff"
# Selbst aus dem Chat GELESENE Ticket-ID (nicht zugewiesen -> kein worktree dahinter):
# dasselbe Violett, aber gedimmt – so ist auf einen Blick klar, ob die ID nur erkannt
# oder wirklich an den Agenten gebunden ist.
TICKET_AUTO_INK = _mix(TICKET_INK, CARD_FILL, 0.45)
# So viele Zeichen passen in die Zeile, bevor sie dem Effort rechts ins Gehege kommt
# ("PROJ-2691 #62" = 13 – Ticket UND PR gehen sich damit gerade noch aus).
TICKET_MAX_CHARS = 14
# (Frueher stand hier ein "Ticket"-Platzhalter als Klick-Aufforderung auf der Karte.
# Ticket zuweisen laeuft jetzt ausschliesslich ueber das Rechtsklick-Menue -> ohne
# Ticket bleibt die Zeile leer; ein Platzhalter waere eine Button-Attrappe ohne Funktion.)

# Schiene links neben einem Repo-Block: der sichtbare Behaelter, der Kopf (Repo-Name)
# und Kachelreihe zu EINER Gruppe zusammenbindet. Vorher trug die Zuordnung allein der
# Abstand – und der war nach oben (fremde Reihe) genauso gross wie nach unten (eigene
# Reihe), die Gruppierung also reine Auslegungssache.
# Warum eine Schiene und KEINE zweite Farbdimension je Repo: Farbe ist im Deck bereits
# der Status (Halo aussen, leuchtend, atmend). Ein zweiter Farbcode daneben wuerde genau
# den Kanal verwaessern, der die eigentliche Arbeit macht.
# Ruhezustand: leise, aber sichtbar. CARD_BORDER allein (#33333d auf BG #121218) war im
# Render-Vergleich nicht mehr wahrnehmbar – eine Schiene, die man nicht sieht, bindet
# nichts. Ein Schritt in Richtung INK_3 reicht; lauter darf sie nicht werden, sonst
# konkurriert der Behaelter mit seinem Inhalt.
RAIL_IDLE = _mix(CARD_BORDER, INK_3, 0.3)
RAIL_HOT  = "#7ecbff"            # gehoverte Gruppe (dieselbe Aufmerksamkeitsfarbe wie
                                 # angehobene Kachel beim Ziehen und Tooltip-Saum)
RAIL_DIM  = _mix(CARD_BORDER, BG, 0.55)   # fremde Gruppe, waehrend woanders gehovert wird
# GLOW_RINGS (Ring-Fade) lebt jetzt im GlowAnimator; oben importiert, weil _draw_tile
# die Ringe anlegt.

# Statuswechsel-Effekt: refresh() setzt bloom = BLOOM_ON_CHANGE; das eigentliche
# Faden/Atmen/Abklingen (FILL_EASE/BLOOM_DECAY im ANIM_MS-Timer) macht jetzt der
# GlowAnimator. Poll-Takt + Stale-Grenze bleiben hier (refresh gehoert dem Panel);
# die Fallback-Werte greifen nur, falls ein config-Eintrag fehlt.
BLOOM_ON_CHANGE = getattr(cfg, "BLOOM_ON_CHANGE", 0.90)
POLL_MS         = getattr(cfg, "POLL_MS", 400)
# So kurz wird nachgefasst, wenn der Poll wegen einer laufenden Ein-/Ausklapp-Bewegung
# aussetzt (siehe refresh). Klein genug, dass die Anzeige direkt nach dem Aufklappen
# frisch ist, gross genug, dass das Nachfragen selbst keine Frames kostet.
SLIDE_RETRY_MS  = 50
STALE_S         = getattr(cfg, "STALE_S", 900)  # so lange ohne Update -> als "idle" zeigen
STALE_WINDOW_S  = getattr(cfg, "STALE_WINDOW_S", 3.0)  # getrennt+Fenster-zu so lange -> Bindung abraeumen
WT_ORPHAN_GRACE_S = getattr(cfg, "WT_ORPHAN_GRACE_S", 20.0)  # worktree-Marker ohne lebenden Agenten so lange -> abraeumen
WT_DISK_SWEEP_INTERVAL_S = getattr(cfg, "WT_DISK_SWEEP_INTERVAL_S", 60.0)  # so oft (s) die '<repo>.wt'-Ordner direkt auf verwaiste worktrees absuchen
WT_DISK_ORPHAN_GRACE_S = getattr(cfg, "WT_DISK_ORPHAN_GRACE_S", 90.0)  # so lange (s) muss ein '.wt'-worktree ohne zugehoerigen Agenten bestehen, bevor der Disk-Sweep ihn faellt
UI_PUMP_MS      = getattr(cfg, "UI_PUMP_MS", 80)     # Takt, in dem Thread-Ergebnisse abgeholt werden (siehe _post)
HOVER_TIP_MS    = getattr(cfg, "HOVER_TIP_MS", 250)  # Hover-Verzoegerung fuer den Tooltip
TIP_LEAVE_MS    = getattr(cfg, "TIP_LEAVE_MS", 80)   # verzoegertes Ausblenden (ueberbrueckt Tk-Leave+Enter zwischen Kachel-Items)
SUMMARY_ON      = getattr(cfg, "HOVER_SUMMARY", True)      # Hover -> KI-Kurzzusammenfassung statt letzter Frage
SUMMARY_MODEL   = getattr(cfg, "HOVER_SUMMARY_MODEL", "haiku")  # Modell fuer die Zusammenfassung
SUMMARY_PREFETCH = getattr(cfg, "HOVER_SUMMARY_PREFETCH", True)  # Zusammenfassungen vorab erzeugen (Hover sofort)
TICKET_AUTO     = getattr(cfg, "TICKET_AUTODETECT", True)        # Ticket-ID selbst aus dem Chat lesen
TICKET_AUTO_CARD = getattr(cfg, "TICKET_AUTODETECT_ON_CARD", True)  # … und auf der Karte zeigen
TICKET_PROJECT  = getattr(cfg, "JIRA_PROJECT_KEY", "")           # bevorzugtes Jira-Projekt bei der Erkennung
PREFETCH_EVERY_S = 5.0    # so oft (s) den Prefetch-Scan laufen lassen (nicht jeden Poll)
PENDING_AUTO_TTL = getattr(cfg, "PENDING_AUTO_TTL", 300)  # s: so lange auf den 1. Hook eines neuen Agenten warten, dann Auto-Startmodus aufgeben
AUTO_READY_GRACE = getattr(cfg, "AUTO_READY_GRACE", 1.5)  # s: nach dem 1. Hook warten, bevor der Auto-Startmodus getippt wird (TUI-Eingabe warmlaufen lassen)
AUTO_MAX_TRIES  = getattr(cfg, "AUTO_MAX_TRIES", 3)       # so oft den Auto-Startmodus (nach)treiben, dann aufgeben
WINDOWS = getattr(cfg, "WINDOWS", ["A", "B", "C", "D"])  # unterstuetzte Fenster
