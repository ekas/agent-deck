"""Einsprungpunkt fuer den Claude-Code-Status-Hook. Der Code liegt in
deck/claude/hooks/report.py.

DIESER DATEINAME IST EIN VERTRAG. Er steht mit absolutem Pfad in
~/.claude/settings.json - und zwar auf jedem Rechner, auf dem das Deck laeuft.
Wer ihn verschiebt oder umbenennt, laehmt jede Claude-Code-Session, weil ein
Hook, der nicht startet, als Veto gegen Prompt und Tool-Aufruf gilt. Darum
bleibt hier ein Zweizeiler stehen, statt fremde settings.json umzuschreiben.

run_module statt eines Funktionsaufrufs: das Modul soll als __main__ laufen, denn
dort sitzt sein eigenes Fangnetz (Exit-Code 0 auf jedem Pfad).
"""
import runpy

runpy.run_module("deck.claude.hooks.report", run_name="__main__", alter_sys=True)
