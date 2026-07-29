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

Der Code liegt im Paket `deck/`. Abhängigkeiten zeigen **nur nach unten** — wer eine
Datei anlegt, entscheidet zuerst, in welche Schicht sie gehört:

| Schicht | Enthält | Darf NICHT importieren |
|---|---|---|
| `deck/domain/` | Anzeigefreie Domäne: Statusmodell, Pfade, Protokoll, Slot-Zustand, Zuordnung, Konfiguration | alles andere |
| `deck/platform/` | Win32: Fokus, DPI, Monitor-Arbeitsbereich | `render`, `ui`, `dock`, `claude` |
| `deck/render/` | Zeichnerei (Pillow/Canvas): Kachel, Kapsel, Welle, Glow, Bottom-Bar | `ui`, `dock`, `claude` |
| `deck/claude/` | Claude-Code-Spezifisches: Usage, Zusammenfassung, Settings, **Hooks** | `ui`, `dock`, `render` |
| `deck/net/` | Broker (TCP) und Kommando-Vokabular zur Extension | `ui`, `dock`, `render` |
| `deck/dock/` | Andocken am Rand, Griff-Fenster, Slide-Animation | `ui` |
| `deck/ui/` | Panel-Fenster, Kacheln, Interaktion — die oberste Schicht | — |
| `deck/ops/` | Betrieb: Log, Zweitstart-Guard, Wächter, Worktrees, VS-Code-Patch | `ui`, `dock`, `render` |

**Faustregel:** Rechnen gehört nach `domain/` und wird getestet; Zeichnen gehört nach
`render/` oder `ui/` und wird angeschaut. Wenn eine Methode in `ui/` etwas ausrechnet,
das man auf Papier nachprüfen könnte, gehört sie nach `domain/`.

### Die Einsprungpunkte im Wurzelverzeichnis sind Verträge

Fünf Dateien liegen bewusst **außerhalb** von `deck/` und enthalten nur einen
`runpy.run_module`-Aufruf. Ihre Namen dürfen sich nicht ändern:

| Datei | Wer nagelt den Namen fest |
|---|---|
| `report.py` · `statusline.py` | `~/.claude/settings.json` — **mit absolutem Pfad, auf jedem Rechner** |
| `agent_deck.py` | `start.bat`, `start_debug.bat`, `deck/ops/watchdog.py` (`PANEL`) |
| `watchdog.py` | `start_watchdog.bat` und die **Windows-Aufgabenplanung** (`install_watchdog.ps1`) |
| `reenable_glow.py` | dokumentierter Handaufruf in README und SETUP |

`run_module` statt eines Funktionsaufrufs, weil in den `__main__`-Blöcken Logik sitzt
(das Fangnetz der Hooks, die Log-Installation des Panels).

## Verträge, die man nicht raten kann

1. **Das Wire-Protokoll existiert doppelt** — `deck/domain/protocol.py` und
   `extension/extension.js`. Es gibt bewusst keinen Build-Step, der sie koppelt (reines
   JS/Python, die Extension kann die Python-Datei nicht importieren). Wer einen String
   ändert, ändert **beide**.

2. **Das Slot-JSON-Format ist ein Vertrag.** `%LOCALAPPDATA%\claude-agent-deck\state\<slot>.json`
   wird von den Hooks geschrieben und vom Panel gelesen — zwei getrennte Prozesse.
   Feldnamen sind snake_case, `ts` sind Unix-Sekunden als Fließkommazahl. Immer atomar
   schreiben (`.tmp` + ersetzen), nie mit Sperre lesen.

3. **Ein Hook darf NIEMALS mit Fehler enden.** Er blockiert sonst den Agenten: bei
   `UserPromptSubmit` und `PreToolUse` liest Claude Code Exit ≠ 0 als Veto gegen Prompt
   bzw. Tool-Aufruf. Jeder Pfad in `deck/claude/hooks/` hat ein Fangnetz und Exit-Code 0.

   Das reicht aber nicht: **ein Hook, der nicht startet, kommt an sein Fangnetz nicht
   heran.** Fehlt die Datei, urteilt der Prozessstarter. Darum steht in `settings.json`
   zusätzlich `cmd /c … || exit 0` — die äußere Schale, die auch einen fehlenden
   Einsprungpunkt in Exit 0 verwandelt. Diese Härtung nicht entfernen; sie ist der
   Unterschied zwischen „Kachel bleibt grau" und „Claude Code lässt sich nicht mehr
   bedienen". Beim Umbenennen gilt: **erst den neuen Pfad beweisen, dann den alten
   löschen** — nie umgekehrt.

4. **Dateien neben dem Code werden über `paths.REPO_ROOT` gefunden**, nie über
   `__file__` des eigenen Moduls. Betroffen sind `bindings.json` und die übrigen
   Laufzeit-JSONs, `assets/robot.ico` und `agent-deck-glow.css`. Rechnet ein Modul selbst
   mit `__file__`, zeigt jede Verschiebung ins Leere — und das fällt nicht auf: die
   Laufzeitdateien entstehen einfach neu am falschen Ort, während die alten mit allen
   Fenster-Zuordnungen unsichtbar liegenbleiben.

5. **Hook-stdin roh als UTF-8 dekodieren** (`sys.stdin.buffer`), nie über `sys.stdin`.
   Sonst kommen Umlaute unter Windows als cp1252-Mojibake an.

6. **Die VS-Code-Extension ist JavaScript** — VS Code lädt nur JS-Extensions. Das ist
   keine offene Aufgabe.

## Fallen, die schon einmal wehgetan haben

- **`SO_REUSEADDR` ist in `deck/net/broker.py` schädlich.** Unter Windows erlaubt die
  Option zwei Listener auf demselben Port; „Port belegt → still deaktiviert" greift dann
  nicht, und Extensions landen beim toten Panel. Der Guard dagegen ist
  `deck/ops/instance.py` (Lockfile + Handoff), nicht der Port.
- **Kachelliste in place aktualisieren**, nie neu aufbauen — ein `delete('all')`-Vollneubau
  setzt Farbe und Statuswert zurück, und dann blitzen beim Auf-/Zuklappen alle Kacheln neu
  auf. `_carry_tile_anim` vererbt den Animationszustand überlebender Kacheln.
- **Animationen an die Bildperiode hängen**, nicht an ein festes Timer-Intervall. Ein
  Timer läuft gegen die Bildrate und stottert sichtbar; dazu gehören
  `timeBeginPeriod(1)` und `perf_counter` statt der grob getakteten Tk-Uhr.
- **Ein halb ausgefahrenes Deck ist der eine unzulässige Zustand** (angedockt gibt es
  keine Titelleiste, man kommt an nichts mehr heran). Deshalb hat `deck/dock/` genau
  einen Ausgang aus der Animation (`_anim_finish`), eine Deadline als Notbremse und einen
  Watchdog. Diese drei nicht wegoptimieren.
- **Der „gesehen"-Merker muss über den Poll hinaus halten** — in der State-Datei steht
  weiterhin `done`.
- **Deko-Effekte fliegen auf Nachfrage ganz raus**, nicht „nur leiser gestellt". Und ein
  Effekt-Timer, der einen Redraw überlebt, verschiebt Kachel-Text dauerhaft.

## Konventionen

- **Eine Datei = ein Konzept, Ziel < 400 Zeilen.** Zwei Altlasten reißen das Ziel noch:
  `deck/ui/panel.py` (~2.900 Zeilen, eine Klasse mit 103 Methoden) und
  `deck/dock/controller.py` (~1.900). Beide werden entlang ihrer Abschnittsgrenzen
  aufgeteilt; neue Konzepte kommen in eigene Module, statt dort anzuwachsen.
- **Kommentare auf Deutsch**, wie der Rest des Repos. Sie erklären das *Warum* — das
  *Was* steht im Code.
- **Tests spiegeln `deck/`.** Die Suite läuft mit pytest ODER direkt
  (`python tests/test_pure.py`, eigener Mini-Runner am Dateiende) und fasst nur
  anzeigefreie Logik an. Ein Testname beschreibt die Regel, nicht die Methode
  (`test_explizites_window_null_loescht_die_zuordnung`).
- **Keine neuen Abhängigkeiten** ohne Not. Außer Pillow kommt das Deck mit der
  Standardbibliothek aus; das ist Absicht und soll so bleiben.

## Der .NET-Port wurde verworfen

Es gab einen Portierungsversuch nach C#/.NET 9 mit WPF. Er ist am **2026-07-29**
vollständig verworfen worden: die Rechen-Schicht war portiert und gegen Python
golden-getestet, aber ausgerechnet die Module, die das Aussehen machen, fehlten — das
Ergebnis sah entsprechend aus.

Der Code liegt weiterhin im Commit `3fcddbc` unter `src/`. **Python ist die einzige
produktive Fassung.** Wer den Port wiederbeleben will, fängt bei der Zeichnerei an,
nicht bei der Mathematik.
