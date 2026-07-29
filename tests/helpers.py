"""Gemeinsame Grundlage aller Testdateien.

Wird von jeder Testdatei importiert - auch wenn sie nichts davon benutzt, denn der
Import erledigt zwei Dinge, die sonst jede Datei wiederholen muesste: die Repo-Wurzel
auf den sys.path legen und die Deck-Sprache festnageln.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from deck import i18n

# Die Usage- und Anzeige-Tests pruefen die deutsche Baseline. Ohne diese Zeile haengen
# sie am realen ~/.claude/settings.json, das schon auf 'english' stehen kann.
i18n._lang = i18n.GERMAN

# Die Modus-Reihenfolge des Rechtsklick-Menues. Von mehreren Testdateien gebraucht,
# darum hier statt in einer davon.
_CYCLE = ["manual", "accept", "plan", "auto"]

# Alle Statuswerte, fuer die es eine Glow-Farbe geben MUSS.
_GLOW = {"idle": 1, "done": 1, "thinking": 1, "running": 1, "waiting": 1, "none": 1}

# Masse der Griff-Kapsel fuer die Bildtests: Fenster 26 px = Roehre 16 + Hof-Luft 10.
# Stehen hier, weil sowohl die Kapsel- als auch die Schwapp-Tests dagegen rechnen.
HR_W, HR_TUBE, HR_LEN = 26, 16, 160
