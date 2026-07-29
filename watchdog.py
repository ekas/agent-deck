"""Einsprungpunkt fuer den Panel-Waechter. Der Code liegt in deck/ops/watchdog.py.

Dieser Name ist in der Windows-Aufgabenplanung registriert (install_watchdog.ps1
schreibt den absoluten Pfad hinein) und wird von start_watchdog.bat aufgerufen.
Ein Umzug bricht eine bereits eingerichtete Aufgabe, ohne dass es auffaellt -
darum bleibt der Name hier.

Argumente (--loop, --status) reicht run_module unveraendert durch.
"""
import runpy

runpy.run_module("deck.ops.watchdog", run_name="__main__", alter_sys=True)
