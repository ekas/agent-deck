"""Einsprungpunkt fuer das Panel. Der Code liegt in deck/ui/panel.py.

Dieser Name wird von start.bat, start_debug.bat und dem Waechter
(deck/ops/watchdog.py, Konstante PANEL) erwartet.

run_module statt eines Funktionsaufrufs: der __main__-Block von panel.py
installiert zuerst das Logging - unter pythonw gibt es sonst keine Ausgabe, wenn
der Start scheitert.
"""
import runpy

runpy.run_module("deck.ui.panel", run_name="__main__", alter_sys=True)
