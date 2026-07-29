"""Einsprungpunkt fuer die Claude-Code-Statuszeile. Der Code liegt in
deck/claude/hooks/statusline.py.

Wie report.py ein VERTRAG: der Pfad steht in ~/.claude/settings.json unter
"statusLine". Nicht verschieben - siehe report.py.
"""
import runpy

runpy.run_module("deck.claude.hooks.statusline", run_name="__main__", alter_sys=True)
