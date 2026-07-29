# Agent Deck

Dashboard für parallel laufende Claude-Code-Agents in VS Code. Eine Kachel je Chat,
Farbe = Zustand; dockt am Bildschirmrand an. Windows-only, .NET 9 / WPF.

## Kommandos

```powershell
dotnet build                  # muss warnungsfrei sein (TreatWarningsAsErrors)
dotnet test                   # < 2 s, immer vor dem Commit
dotnet format                 # Stil nach .editorconfig
dotnet run --project src/AgentDeck.App
```

## Schichten

Abhängigkeiten zeigen **nur nach unten**. Wer eine Datei anlegt, entscheidet zuerst,
in welche Schicht sie gehört:

| Projekt | Enthält | Darf NICHT kennen |
|---|---|---|
| `AgentDeck.Core` | Domäne: Protokoll, Slot-Zustand, Statusmodell, Andock-Rechnerei, Broker | Windows, WPF, Claude-Vokabular |
| `AgentDeck.Windows` | Win32-P/Invoke: Monitore, Zeiger, Fenster | WPF, Claude |
| `AgentDeck.Claude` | Claude-Code-Spezifisches: Hooks, Payload, Transcript, Usage | WPF |
| `AgentDeck.App` | WPF: Fenster, ViewModels, Andock-Steuerung | — |
| `AgentDeck.Hooks` | Exe, das Claude Code als Hook aufruft | — |

`AgentDeck.Core` ist `net9.0` und bleibt plattformneutral — dort liegt alles, was ohne
Bildschirm testbar sein soll. Der Rest ist `net9.0-windows`.

**Faustregel:** Rechnen gehört in `Core` und wird getestet; Zeichnen gehört in `App`
und wird angeschaut. Wenn eine Methode in `App` etwas ausrechnet, das man auf Papier
nachprüfen könnte, gehört sie nach `Core`.

## Verträge, die man nicht raten kann

1. **Das Wire-Protokoll existiert dreifach** — `Core/Protocol.cs`, `protocol.py`,
   `extension/extension.js`. Es gibt bewusst keinen Build-Step, der sie koppelt. Wer
   einen String ändert, ändert **alle drei**. `PythonCompatibilityTests` liest die
   Python-Datei zur Testzeit und schlägt bei Drift fehl.

2. **Das Slot-JSON-Format ist ein Vertrag.** `%LOCALAPPDATA%\claude-agent-deck\state\<slot>.json`
   wird von Hooks geschrieben und vom Panel gelesen — solange beide Fassungen
   existieren, auch über Sprachgrenzen hinweg. Feldnamen sind snake_case, `ts` sind
   Unix-Sekunden als `double` (nie `DateTime`). Immer atomar schreiben (`.tmp` +
   ersetzen), nie mit Sperre lesen.

3. **Ein Hook darf NIEMALS mit Fehler enden.** Er blockiert sonst den Agenten. Jeder
   Pfad in `AgentDeck.Hooks` hat ein Fangnetz und Exit-Code 0.

4. **Zahlen für Menschen brauchen `InvariantCulture`.** Die Statuszeile zeigt sonst
   auf deutschen Systemen `$0,15` statt `$0.15`.

5. **Die VS-Code-Extension bleibt JavaScript.** VS Code lädt keine .NET-Extensions.
   Das ist keine offene Aufgabe.

## Fallen, die schon einmal wehgetan haben

- **`SO_REUSEADDR` nicht setzen.** Unter Windows erlaubt die Option zwei Listener auf
  demselben Port; „Port belegt → still deaktiviert" greift dann nicht, und Extensions
  landen beim toten Panel.
- **Kachelliste in place aktualisieren**, nie neu aufbauen — sonst blitzen bei jedem
  Auf-/Zuklappen alle Kacheln neu auf.
- **Animationen an `CompositionTarget.Rendering` hängen**, nicht an einen Timer mit
  festem Intervall. Ein Timer läuft gegen die Bildrate und stottert sichtbar.
- **Ein halb ausgefahrenes Deck ist der eine unzulässige Zustand** (angedockt gibt es
  keine Titelleiste, man kommt an nichts mehr heran). Deshalb hat
  `EdgeDockController` genau einen Ausgang aus der Animation, eine Notbremse und
  einen Watchdog. Diese drei nicht wegoptimieren.
- **Der „gesehen"-Merker muss über den Poll hinaus halten** — in der State-Datei steht
  weiterhin `done`.

## Konventionen

- **Eine Datei = ein Konzept, Ziel < 300 Zeilen.** Der Vorgänger hatte eine Datei mit
  2.778 Zeilen; jede Änderung daran war ein Risiko.
- **Kommentare auf Deutsch**, wie der Rest des Repos. Sie erklären das *Warum* — das
  *Was* steht im Code.
- **Tests neben der Schicht**, eine Testdatei je Quelldatei. Ein Testname beschreibt
  die Regel, nicht die Methode (`Explizites_window_null_loescht_die_Zuordnung`).
- **Keine neuen Abhängigkeiten** ohne Not. Das Deck kommt mit der Standardbibliothek
  aus; das ist Absicht und soll so bleiben.

## Golden-Master: der Port wird gegen Python geprüft

Unter `tests/golden/*.json` liegen ~1.900 Fälle, die die **Python-Fassung** berechnet
hat: Eingabe plus erwartetes Ergebnis. Die C#-Tests jagen dieselben Eingaben durch den
Port und vergleichen. Damit ist „verhaltensgleich" gemessen statt behauptet.

```powershell
python tools/gen_golden.py      # Dateien neu erzeugen (nur solange Python noch da ist)
```

Abgedeckt: `StatusModel`, `ColorMath`, `Spring`, `ReportHook`, `StatusLineHook`.

Zwei Regeln dazu:

- **Golden-Dateien nie von Hand anpassen, damit ein Test grün wird.** Sie sind die
  Vorlage; weicht der Port ab, ist der Port falsch. Neu erzeugen nur, wenn sich die
  *Python-Seite* absichtlich geändert hat.
- Ein neu portiertes Modul bekommt seinen Fall **in `tools/gen_golden.py`**, bevor es
  als fertig gilt.

Nach dem Löschen der Python-Fassung bleiben die Dateien als eingefrorene Vorlage
liegen – sie laufen ohne Python.

## Migration (läuft noch)

Die Python-Fassung im Repo-Wurzelverzeichnis ist **noch produktiv** und wird Modul für
Modul ersetzt. Sie darf nicht brechen, bevor ihr Gegenstück fertig ist. Der aktuelle
Stand steht in [README.md](README.md#portierung-nach-net-in-arbeit).

Beim Portieren gilt: **verhaltensgleich, nicht wörtlich.** Wo Windows sich anders
verhält als die Python-Annahme, folgt der Port der *dokumentierten Absicht* — und der
Unterschied wird im Code kommentiert.
