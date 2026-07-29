# Agent Deck

Dashboard für parallel laufende Claude-Code-Agents in VS Code. Eine Kachel je Chat,
Farbe = Zustand; dockt am Bildschirmrand an. Windows-only, Python 3.12+ / tkinter.

## Kommandos

```powershell
python tests/test_pure.py     # Unit-Tests der anzeigefreien Logik, immer vor dem Commit
python -m compileall -q .      # Syntaxprüfung aller Module
start.bat                      # Panel leise starten (pythonw, keine Konsole)
start_debug.bat                # Panel mit Konsole — für den ersten Start und bei Fehlersuche
```

Einzige Pflicht-Abhängigkeit ist **Pillow** (`requirements.txt`); alles andere kommt aus
der Standardbibliothek.

## Aufbau

Das Deck besteht aus **flachen Modulen** — kein Package, keine `__init__.py`. Die
Trennlinie verläuft nicht über Ordner, sondern über eine Frage: *braucht das Modul einen
Bildschirm?*

| Zuständigkeit | Module |
|---|---|
| Panel-Fenster, Kacheln, Interaktion | `agent_deck.py` |
| Andocken am Rand, Griff-Kapsel | `edge_dock.py`, `handle_render.py`, `handle_wave.py` |
| Zeichnen (Pillow/Canvas) | `card_render.py`, `glow_animator.py`, `canvas_kit.py`, `bottom_bar.py` |
| Brücke zur VS-Code-Extension | `broker.py`, `broker_commands.py`, `protocol.py` |
| Claude-Code-Hooks | `report.py`, `statusline.py`, `hookstate.py` |
| Statuslogik, Pfade, Log | `status_model.py`, `deck_common.py`, `deck_paths.py`, `deck_log.py` |
| Claude-Spezifisches | `claude_usage.py`, `chat_summary.py`, `claude_settings.py` |
| Windows/Bildschirm | `hidpi.py`, `screen_fit.py`, `win_focus.py` |
| Zuordnung, Einstellungen, Sprache | `bindstore.py`, `config.py`, `i18n.py` |
| Betrieb | `single_instance.py`, `watchdog.py`, `worktree_cleanup.py` |

**Faustregel:** Rechnen gehört in ein anzeigefreies Modul und wird getestet; Zeichnen
gehört ins Canvas und wird angeschaut. Wenn eine Methode in `agent_deck.py` etwas
ausrechnet, das man auf Papier nachprüfen könnte, gehört sie in ein eigenes Modul —
`status_model.py` und `canvas_kit.py` sind genau dafür entstanden.

## Verträge, die man nicht raten kann

1. **Das Wire-Protokoll existiert doppelt** — `protocol.py` und
   `extension/extension.js`. Es gibt bewusst keinen Build-Step, der sie koppelt (reines
   JS/Python, die Extension kann die Python-Datei nicht importieren). Wer einen String
   ändert, ändert **beide**.

2. **Das Slot-JSON-Format ist ein Vertrag.** `%LOCALAPPDATA%\claude-agent-deck\state\<slot>.json`
   wird von den Hooks geschrieben und vom Panel gelesen — zwei getrennte Prozesse.
   Feldnamen sind snake_case, `ts` sind Unix-Sekunden als Fließkommazahl. Immer atomar
   schreiben (`.tmp` + ersetzen), nie mit Sperre lesen.

3. **Ein Hook darf NIEMALS mit Fehler enden.** Er blockiert sonst den Agenten: bei
   `UserPromptSubmit` und `PreToolUse` liest Claude Code Exit ≠ 0 als Veto gegen Prompt
   bzw. Tool-Aufruf. Jeder Pfad in `report.py` und `statusline.py` hat ein Fangnetz und
   Exit-Code 0.

   Das reicht aber nicht: **ein Hook, der nicht startet, kommt an sein Fangnetz nicht
   heran.** Fehlt `python` auf dem PATH oder wurde die Datei verschoben, urteilt der
   Prozessstarter. Wer die Hook-Einträge in `~/.claude/settings.json` anfasst, prüft
   danach, dass eine Kachel beim Tippen wirklich reagiert.

4. **Hook-stdin roh als UTF-8 dekodieren** (`sys.stdin.buffer`), nie über `sys.stdin`.
   Sonst kommen Umlaute unter Windows als cp1252-Mojibake an.

5. **Die VS-Code-Extension ist JavaScript** — VS Code lädt nur JS-Extensions. Das ist
   keine offene Aufgabe.

## Fallen, die schon einmal wehgetan haben

- **`SO_REUSEADDR` ist in `broker.py` schädlich.** Unter Windows erlaubt die Option zwei
  Listener auf demselben Port; „Port belegt → still deaktiviert" greift dann nicht, und
  Extensions landen beim toten Panel. Der Guard dagegen ist `single_instance.py`
  (Lockfile + Handoff), nicht der Port.
- **Kachelliste in place aktualisieren**, nie neu aufbauen — ein `delete('all')`-Vollneubau
  setzt Farbe und Statuswert zurück, und dann blitzen beim Auf-/Zuklappen alle Kacheln neu
  auf. `_carry_tile_anim` vererbt den Animationszustand überlebender Kacheln.
- **Animationen an die Bildperiode hängen**, nicht an ein festes Timer-Intervall. Ein
  Timer läuft gegen die Bildrate und stottert sichtbar; dazu gehören
  `timeBeginPeriod(1)` und `perf_counter` statt der grob getakteten Tk-Uhr.
- **Ein halb ausgefahrenes Deck ist der eine unzulässige Zustand** (angedockt gibt es
  keine Titelleiste, man kommt an nichts mehr heran). Deshalb hat `edge_dock.py` genau
  einen Ausgang aus der Animation (`_anim_finish`), eine Deadline als Notbremse und einen
  Watchdog. Diese drei nicht wegoptimieren.
- **Der „gesehen"-Merker muss über den Poll hinaus halten** — in der State-Datei steht
  weiterhin `done`.
- **Deko-Effekte fliegen auf Nachfrage ganz raus**, nicht „nur leiser gestellt". Und ein
  Effekt-Timer, der einen Redraw überlebt, verschiebt Kachel-Text dauerhaft.

## Konventionen

- **Eine Datei = ein Konzept, Ziel < 300 Zeilen.** Zwei Altlasten reißen das Ziel:
  `agent_deck.py` (~2.900 Zeilen) und `edge_dock.py` (~1.900). Neue Konzepte kommen
  darum in eigene Module, statt dort anzuwachsen.
- **Kommentare auf Deutsch**, wie der Rest des Repos. Sie erklären das *Warum* — das
  *Was* steht im Code.
- **Tests in `tests/test_pure.py`.** Die Suite läuft mit pytest ODER direkt
  (`python tests/test_pure.py`, eigener Mini-Runner am Dateiende) und fasst nur
  anzeigefreie Logik an. Ein Testname beschreibt die Regel, nicht die Methode
  (`test_explizites_window_null_loescht_die_zuordnung`).
- **Keine neuen Abhängigkeiten** ohne Not. Außer Pillow kommt das Deck mit der
  Standardbibliothek aus; das ist Absicht und soll so bleiben.

## Der .NET-Port wurde verworfen

Es gab einen Portierungsversuch nach C#/.NET 9 mit WPF. Er ist am **2026-07-29**
vollständig verworfen und aus dem Arbeitsverzeichnis entfernt worden: die Rechen-Schicht
war portiert und gegen Python golden-getestet, aber ausgerechnet die Module, die das
Aussehen machen (`handle_render`, `handle_wave`, `card_render`, `glow_animator`,
`edge_dock`), fehlten — das Ergebnis sah entsprechend aus.

Der Code liegt weiterhin im Commit `3fcddbc` unter `src/`, `tests/AgentDeck.*` und
`tests/golden/`; ein weiterentwickelter Stand hängt als referenzloser Commit `96c9cae`
in der Objektdatenbank. **Python ist die einzige produktive Fassung.** Wer den Port
wiederbeleben will, fängt bei der Zeichnerei an, nicht bei der Mathematik.
