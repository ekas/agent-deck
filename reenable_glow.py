"""Einsprungpunkt fuer den VS-Code-CSS-Patch. Der Code liegt in
deck/ops/vscode_glow.py.

Dieser Name steht als Handaufruf in README.md und docs/SETUP.md
(`python reenable_glow.py`, `--off` nimmt den Patch zurueck) - er wird nach jedem
VS-Code-Update gebraucht, also von Menschen getippt und nicht von Code gerufen.
"""
import runpy

runpy.run_module("deck.ops.vscode_glow", run_name="__main__", alter_sys=True)
