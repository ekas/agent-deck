"""Wire-Vokabular des Broker-Protokolls – die EINE Quelle der Wahrheit (Python-Seite).

Zwischen Panel und VS-Code-Extension laufen newline-getrennte JSON-Zeilen. Die
Kommando- und Typ-Strings unten sind der Vertrag: hier ein Wert geaendert, muss
er in extension/extension.js MITGEZOGEN werden. Es gibt bewusst keinen Build-
Step (reines JS/Python), also kann die Extension diese Datei nicht importieren –
sie spiegelt die Strings von Hand. Diese Datei buendelt wenigstens die Python-
Seite (broker.py + agent_deck.py), damit ein Tippfehler ein NameError wird statt
eines still verpuffenden Kommandos.
"""

# ── Kommandos: Panel -> Extension (JSON-Feld "cmd") ──────────────────────
CMD_ASSIGN       = "assign"        # der Extension ihren Fenster-Buchstaben zuweisen
CMD_UNASSIGN     = "unassign"      # Buchstaben wieder vergessen (loest Phantomkachel)
CMD_FOCUS_PANE   = "focusPane"     # ein bestimmtes Terminal (Slot) fokussieren
CMD_SEND         = "send"          # Text/Slash-Kommando an den Agent schicken
#   Feld "submit": true -> Extension schreibt den Text und schickt ihn per SEPARATEM
#   Enter ab (langer Prompt = Paste -> ein mitgeschicktes \r wuerde verschluckt).
CMD_KEY          = "key"           # einzelne Taste (enter/esc/shift-tab, ggf. repeat)
CMD_CREATE_AGENT = "createAgent"   # ein weiteres Claude-Terminal oeffnen
CMD_RELOAD       = "reload"        # "Developer: Reload Window" ausloesen
CMD_CLOSE_AGENT  = "closeAgent"    # ein einzelnes Terminal/Agent schliessen
CMD_CLOSE_WINDOW = "closeWindow"   # das ganze VS-Code-Fenster schliessen

# ── Nachrichten-Typen: Extension -> Panel (JSON-Feld "type") ─────────────
TYPE_HELLO     = "hello"           # Erstmeldung: Workspace-Name (+ evtl. window/slots)
TYPE_TERMINALS = "terminals"       # aktualisierte Terminal-/Slot-Liste des Fensters
TYPE_SEEN      = "seen"            # Pane in VS Code direkt fokussiert -> Slot als gelesen (done->idle)
