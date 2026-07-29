@echo off
cd /d "%~dp0"
rem Startet Agent Deck ohne Konsolenfenster (pythonw).
start "" pythonw "%~dp0agent_deck.py"
