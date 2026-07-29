@echo off
rem Startet den Dauerwaechter ohne Konsolenfenster. Er sieht im Takt nach, ob das
rem Panel noch lebt, und startet es nach einem Absturz neu (watchdog.py --loop).
rem Ein zweiter Start ist harmlos: der Waechter hat sein eigenes Lock und tritt
rem dann sofort wieder ab.
cd /d "%~dp0"
start "" pythonw "%~dp0watchdog.py" --loop 60
