# Agent Deck

Dashboard für parallel laufende Claude-Code-Agents in VS Code. Eine Kachel je Chat,
Farbe = Zustand; dockt am Bildschirmrand an. Windows-only, Python 3.12+ / tkinter.

## Kommandos

```powershell
python tests/run.py            # alle Unit-Tests, immer vor dem Commit
python tests/test_dock_animation.py   # eine Datei allein läuft auch
python -m compileall -q .      # Syntaxprüfung aller Module
start.bat                      # Panel leise starten (pythonw, keine Konsole)
start_debug.bat                # Panel mit Konsole — für den ersten Start und bei Fehlersuche
```

Einzige Pflicht-Abhängigkeit ist **Pillow** (`requirements.txt`); alles andere kommt aus
der Standardbibliothek.

## Aufbau

Der Code liegt im Paket `deck/`. Abhängigkeiten zeigen **nur nach unten** — wer eine
Datei anlegt, entscheidet zuerst, in welche Schicht sie gehört:

| Schicht | Enthält | Darf importieren |
|---|---|---|
| `deck/domain/` | Anzeigefreie Domäne: Statusmodell, Pfade, Protokoll, Slot-Zustand, Zuordnung, Konfiguration | — |
| `deck/platform/` | Win32: Fokus, DPI, Monitor-Arbeitsbereich | — |
| `deck/render/` | Zeichnerei (Pillow/Canvas): Kachel, Kapsel, Welle, Glow | `domain`, `platform` |
| `deck/net/` | Broker (TCP) und Kommando-Vokabular zur Extension | `domain` |
| `deck/claude/` | Claude-Code-Spezifisches: Usage, Zusammenfassung, Settings, **Hooks** | `domain`, `i18n` |
| `deck/ops/` | Betrieb: Log, Zweitstart-Guard, Wächter, Worktrees, VS-Code-Patch | `domain`, `platform`, `i18n` |
| `deck/dock/` | Andocken am Rand, Griff-Fenster, Slide-Animation | `domain`, `platform`, `render` |
| `deck/ui/` | Panel-Fenster, Kacheln, Interaktion — die oberste Schicht | alle |
| `deck/i18n.py` | Deutsch/Englisch. Querschnitt, liegt auf der Paketwurzel und ist die **einzige** erlaubte Abhängigkeit nach oben (der Sprachregler steht in Claudes `settings.json`) | `claude` |

**Diese Tabelle ist getestet**, nicht behauptet: `tests/test_architecture.py` liest die
echten Importe und wird rot, sobald einer nach oben zeigt — mit Datei und Zeile. Die
Erlaubnisliste dort ist knapp gehalten und nennt nur, was heute wirklich importiert
wird. Ein neuer Import macht den Test also auch dann rot, wenn er die Ordnung einhält;
dann trägt man ihn ein und hat einmal darüber nachgedacht. Mitgetestet wird außerdem,
dass `domain/` ohne `tkinter`/`PIL`/`ctypes` bleibt und die Hooks nicht die Anzeige
nachziehen — sie starten bei jedem Tool-Aufruf neu.

**Faustregel:** Rechnen gehört nach `domain/` und wird getestet; Zeichnen gehört nach
`render/` oder `ui/` und wird angeschaut. Wenn eine Methode in `ui/` etwas ausrechnet,
das man auf Papier nachprüfen könnte, gehört sie nach `domain/`.

### Wo fasse ich was an?

| Ich will … | … dann hierhin |
|---|---|
| wie eine Kachel aussieht | `ui/tile_draw.py`, Werte in `ui/theme.py` |
| wo Kacheln liegen, Reihenfolge | `ui/tiles.py`, Ziehen in `ui/reorder.py` |
| Statusfarbe oder -text ändern | `ui/theme.py` (`GLOW_STYLE`, `STATUS_LABEL`) |
| wann ein Status kippt | `domain/status_model.py` |
| Poll-Takt, Kacheln nachziehen | `ui/refresh.py` |
| Bindungen, geschlossene Fenster, Auto-Startmodus | `ui/windows.py` |
| Fenstergröße und Skalierung | `ui/layout.py` |
| Tooltip-Inhalt | `ui/hover.py`, Text in `claude/summarize.py`, Ticket/PR in `claude/refs.py` |
| Ein-/Ausklappen, Animation | `dock/animation.py` — **die drei Sicherungen dort lassen** |
| Griff: Aussehen | `render/capsule.py`, Maße und Masken in `render/capsule_masks.py` |
| Griff: Verhalten | `dock/handle.py`, Schwappen in `dock/wave.py` |
| ein neues Kommando an die Extension | `domain/protocol.py` **und** `extension/extension.js` |
| Hook-Verhalten (was gemeldet wird) | `claude/hooks/report.py` |
| Usage: Zahlen holen | `claude/usage.py`, Token in `claude/usage_token.py` |
| Usage: Anzeige und Ampelfarben | `claude/usage_view.py`, Balken in `ui/bottombar.py` |
| Ticket zuweisen | `ui/ticket.py` |
| worktrees abräumen | `ui/worktree_sweep.py`, Git-Teil in `ops/worktree.py` |
| irgendetwas mit Win32 | `platform/` — neue Funktion? Signatur in `platform/win32.py` typisieren |

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
   heran.** Fehlt die Datei, urteilt der Prozessstarter. Darum endet jeder Eintrag in
   `settings.json` auf `|| exit 0` — die äußere Schale, die auch einen fehlenden
   Einsprungpunkt in Exit 0 verwandelt:

   ```
   python "C:\…\agent-deck\report.py" thinking || exit 0
   ```

   **KEIN `cmd /c` davorsetzen.** Claude Code führt Hooks über eine POSIX-Shell aus, und
   deren MSYS-Pfadkonvertierung macht aus `/c` den Pfad `C:\`. `cmd` startet dann ohne
   Schalter, also interaktiv: es gibt seinen Banner aus, liest das Hook-JSON von stdin als
   Befehl — und ruft `python` nie auf. Der Hook endet mit 0 und sieht darum gesund aus,
   meldet aber keinen Status mehr; die Kacheln bleiben stumm grau. Genau das ist am
   2026-07-29 passiert, und es war am Exit-Code nicht zu erkennen, sondern nur daran, dass
   in `state\` keine Datei mehr frisch wurde.

   Beim Umbenennen gilt: **erst den neuen Pfad beweisen, dann den alten löschen** — nie
   umgekehrt. Und „bewiesen" heißt: eine Datei in `state\` ist danach frisch, nicht bloß
   Exit-Code 0.

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

- **Eine Datei = ein Konzept, < 400 Zeilen.** Das gilt inzwischen für **jedes** Modul —
  die größte Datei ist `ui/panel.py` mit 375 Zeilen. Wer eine Datei über die Grenze
  wachsen lässt, hat meist zwei Konzepte darin; der Ausweg ist ein neues Modul, nicht
  eine Ausnahme.
- **Die zwei großen Klassen sind Mixin-Kompositionen.** `AgentDeck` (103 Methoden) setzt
  sich aus 11 Mixins in `deck/ui/` zusammen, `EdgeDock` (97) aus 8 in `deck/dock/`. Eine
  neue Methode gehört in das Mixin ihres Themas — und wenn es keines gibt, in ein neues.
  Der Klassenkopf in `panel.py` bzw. `controller.py` ist die Übersicht.

### Mixin oder eigenes Objekt?

Die Frage entscheidet **eine Zahl**: wie viele `self`-Attribute liest ein Modul, die es
nicht selbst setzt? Gemessen wird das mit demselben AST-Durchlauf wie in
`tests/test_ui_collaborators.py`.

| fremde Attribute | Form | warum |
|---|---|---|
| 0–6 | **eigenes Objekt** mit Konstruktor-Abhängigkeiten | die Liste ist lesbar und das Teil einzeln baubar (`SettingsDialog`: 4 Werte + 3 Rückrufe, `TileRenderer`: 2 + 6) |
| ab ~10 | **Mixin** | `tiles` (20), `actions` (12), `refresh` (11) *orchestrieren* den Panel-Zustand — das ist ihre Aufgabe, nicht ein Mangel. In Objekte gepresst ergäben sie 20 Konstruktor-Argumente und gewönnen nichts |

Wer ein Mixin herauslöst, prüft danach dreierlei: dass die Signatur die Abhängigkeiten
**nennt**, dass sich das Teil **mit Attrappen** bauen lässt (ohne Tk, Broker, BindStore),
und dass `AgentDeck` es nicht mehr einmischt — ein zusätzliches Mixin in einer Liste von
elf sieht sonst niemand.
- **Kommentare auf Deutsch**, wie der Rest des Repos. Sie erklären das *Warum* — das
  *Was* steht im Code.
- **Tests spiegeln `deck/`** — eine Datei je Modulbereich, benannt nach ihm
  (`test_dock_animation.py`, `test_claude_usage.py`). Sie fassen nur anzeigefreie
  Logik an und laufen **ohne pytest**: `tests/run.py` sammelt alle `test_*.py` ein,
  jede Datei ist aber auch einzeln aufrufbar. `tests/helpers.py` legt die Repo-Wurzel
  auf den `sys.path` und nagelt die Deck-Sprache auf Deutsch — ohne das hingen die
  Anzeige-Tests am echten `~/.claude/settings.json`. Darum importiert **jede**
  Testdatei `helpers`, auch wenn sie nichts daraus benutzt.
- Ein Testname beschreibt die Regel, nicht die Methode
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
