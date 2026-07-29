using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows.Threading;
using AgentDeck.Core;

namespace AgentDeck.App;

/// <summary>
/// Der Refresh-Loop des Decks: liest im Takt die Slot-Zustände und pflegt die
/// Kachelliste. Entspricht <c>agent_deck.refresh()</c>, aber ohne Zeichnen – die
/// Darstellung hängt per Binding daran.
///
/// Wichtig (Lehre aus der Python-Fassung): die Kachelliste wird IN PLACE aktualisiert,
/// nie neu aufgebaut. Ein Neuaufbau setzte dort Farbe und Animationszustand zurück und
/// ließ bei jedem Auf- und Zuklappen alle Kacheln neu aufblitzen.
/// </summary>
public sealed class DeckViewModel : INotifyPropertyChanged, IDisposable
{
    /// <summary>Takt, in dem Status/Verbindungen neu eingelesen werden (config.POLL_MS).</summary>
    private const int PollMs = 400;

    /// <summary>Frame-Takt der Atem-Animation (config.ANIM_MS).</summary>
    private const int AnimMs = 55;

    private readonly SlotStore _store = new();
    private readonly DispatcherTimer _poll;
    private readonly DispatcherTimer _anim;
    private readonly SlotOrder _order;
    private readonly string _orderPath;

    /// <summary>
    /// Slots, deren Pane in VS Code direkt angeklickt wurde. Der Merker MUSS über den
    /// Poll hinaus halten: in der State-Datei steht weiterhin "done", die Kachel soll
    /// aber ruhig sein. Er verfällt erst, wenn ein anderer Status gemeldet wird.
    /// </summary>
    private readonly HashSet<string> _seen = new(StringComparer.OrdinalIgnoreCase);

    private double _phase;
    private string _dominant = StatusStyle.None;

    public DeckViewModel()
    {
        _orderPath = SlotOrder.PathFor(
            Path.GetDirectoryName(DeckSettings.FindPath()) ?? AppContext.BaseDirectory);
        _order = SlotOrder.Load(_orderPath);

        _poll = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromMilliseconds(PollMs),
        };
        _poll.Tick += (_, _) => Refresh();

        _anim = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromMilliseconds(AnimMs),
        };
        _anim.Tick += (_, _) => Breathe();

        // Der Broker bindet Port 8765. Läuft die Python-Fassung schon, schlägt das
        // BEWUSST fehl und der Broker bleibt still deaktiviert - dann zeigt das Deck
        // weiterhin Status an, kann aber keine Terminals fokussieren.
        Broker = new Broker();
        Broker.Start();
    }

    /// <summary>Die Kacheln, in der vom Benutzer festgelegten Reihenfolge.</summary>
    public ObservableCollection<TileViewModel> Tiles { get; } = [];

    public Broker Broker { get; }

    /// <summary>
    /// Dringlichster Gesamtzustand – die Farbe, in der der Griff-Balken leuchtet.
    /// </summary>
    public string DominantStatus
    {
        get => _dominant;
        private set { if (_dominant != value) { _dominant = value; Notify(); } }
    }

    /// <summary>Kurzfassung für die Bottom-Bar.</summary>
    public string SummaryText
    {
        get
        {
            if (Tiles.Count == 0)
                return "keine Agenten";
            var verbindung = Broker.IsListening ? "" : "  ·  ohne Broker";
            return $"{Tiles.Count} Agent{(Tiles.Count == 1 ? "" : "en")}  ·  {DominantStatus}{verbindung}";
        }
    }

    public void Start()
    {
        Refresh();
        _poll.Start();
        _anim.Start();
    }

    public void Dispose()
    {
        _poll.Stop();
        _anim.Stop();
        Broker.Dispose();
    }

    /// <summary>
    /// Zustände neu einlesen und die Kachelliste angleichen: vorhandene Kacheln
    /// aktualisieren, neue anhängen, verschwundene entfernen.
    /// </summary>
    public void Refresh()
    {
        // Verbindungsauskunft nur, wenn der Broker wirklich hört - sonst wäre jede
        // Kachel fälschlich "getrennt".
        Func<string, bool>? connected = Broker.IsListening
            ? slot => Broker.Connected(SlotOrder.WindowOf(slot))
            : null;

        _store.Refresh(Clock.UnixNow(), connected);

        // Pane-Klicks in VS Code abholen, BEVOR die Kacheln gesetzt werden.
        if (Broker.IsListening)
            foreach (var gesehen in Broker.DrainSeen())
                _seen.Add(gesehen);

        // Vom Benutzer festgelegte Reihenfolge anwenden.
        var slots = _order.Apply(_store.StatusKeys.Keys);
        var vorhanden = Tiles.ToDictionary(t => t.Slot, StringComparer.OrdinalIgnoreCase);

        for (var i = 0; i < slots.Count; i++)
        {
            var slot = slots[i];
            if (!vorhanden.TryGetValue(slot, out var tile))
            {
                tile = new TileViewModel(slot);
                Tiles.Insert(Math.Min(i, Tiles.Count), tile);
            }
            else if (Tiles.IndexOf(tile) != i)
            {
                Tiles.Move(Tiles.IndexOf(tile), i);   // Move statt Neuanlage: kein Flackern
            }

            var st = _store.States[slot];
            var key = _store.StatusKeys[slot];

            if (key == StatusStyle.Done)
            {
                // Schon in VS Code angeschaut -> nicht mehr "ungelesen".
                if (_seen.Contains(slot))
                    key = StatusStyle.Idle;
            }
            else
            {
                _seen.Remove(slot);   // neuer Zustand -> der Merker ist verbraucht
            }

            tile.StatusKey = key;
            tile.Activity = st.Activity ?? "";
            tile.Prompt = st.Prompt;
            tile.Reference = _store.Tickets.GetValueOrDefault(slot);
        }

        // Verschwundene Slots entfernen (Agent geschlossen).
        foreach (var tot in Tiles.Where(t => !_store.StatusKeys.ContainsKey(t.Slot)).ToList())
        {
            Tiles.Remove(tot);
            _seen.Remove(tot.Slot);
        }

        // Der Griff soll dasselbe zeigen wie die Kacheln - also die BEREINIGTEN Status,
        // nicht die rohen aus dem Store (sonst leuchtet er "ungelesen", obwohl die
        // Kachel schon ruhig ist).
        DominantStatus = StatusModel.DominantStatus(Tiles.Select(t => t.StatusKey));
        RefreshAddTiles();
        Notify(nameof(SummaryText));
    }

    /// <summary>
    /// Kachel angeklickt: das Fenster nach vorn holen und GENAU dieses Terminal-Pane
    /// fokussieren. Ohne Broker passiert nichts – die Extension ist der einzige Weg,
    /// ein einzelnes Split-Pane zu treffen.
    /// </summary>
    public bool FocusSlot(string slot)
    {
        if (!Broker.IsListening)
            return false;

        return Broker.SendWindow(SlotOrder.WindowOf(slot), new Dictionary<string, object?>
        {
            ["cmd"] = Protocol.CmdFocusPane,
            ["slot"] = slot,
        });
    }

    /// <summary>Reihenfolge der Permission-Modi (<c>config.MODE_CYCLE</c>).</summary>
    private static readonly string[] ModeCycle = ["manual", "accept", "plan", "auto"];

    /// <summary>Modus, in dem ein frischer Chat startet (<c>config.MODE_START</c>).</summary>
    private const string ModeStart = "manual";

    /// <summary>
    /// Permission-Mode umschalten. Claude Code kennt keinen direkten Sprung – der Modus
    /// wird nur ZYKLISCH per Shift+Tab weitergeschaltet. Wie viele Tastendrücke nötig
    /// sind, rechnet <see cref="StatusModel.ModeSteps"/> aus dem zuletzt vom Hook
    /// gemeldeten Ist-Modus aus.
    /// </summary>
    public bool SetMode(string slot, string target)
    {
        var gemeldet = _store.States.GetValueOrDefault(slot)?.Mode;
        int? aktuell = gemeldet is not null && Array.IndexOf(ModeCycle, gemeldet) >= 0
            ? Array.IndexOf(ModeCycle, gemeldet)
            : null;

        if (StatusModel.ModeSteps(aktuell, target, ModeCycle, ModeStart) is not { } schritte)
            return false;
        if (schritte.Steps == 0)
            return true;                      // schon dort

        return Broker.IsListening && Broker.SendWindow(
            SlotOrder.WindowOf(slot), new Dictionary<string, object?>
            {
                ["cmd"] = Protocol.CmdKey,
                ["slot"] = slot,
                ["key"] = "shift-tab",
                ["repeat"] = schritte.Steps,
            });
    }

    /// <summary>Modell wechseln – als Slash-Kommando an den Agenten.</summary>
    public bool SetModel(string slot, string model) => Send(slot, "/model " + model);

    /// <summary>Text oder Slash-Kommando an einen Agenten schicken.</summary>
    public bool Send(string slot, string text)
    {
        if (!Broker.IsListening)
            return false;

        return Broker.SendWindow(SlotOrder.WindowOf(slot), new Dictionary<string, object?>
        {
            ["cmd"] = Protocol.CmdSend,
            ["slot"] = slot,
            ["text"] = text,
            // Getrenntes Enter: ein mitgeschicktes \r würde bei langen Prompts
            // (die als Paste ankommen) verschluckt.
            ["submit"] = true,
        });
    }

    /// <summary>Einen weiteren Claude-Chat im Fenster öffnen (die ＋-Kachel).</summary>
    public bool CreateAgent(string window)
    {
        if (!Broker.IsListening)
            return false;

        return Broker.SendWindow(window, new Dictionary<string, object?>
        {
            ["cmd"] = Protocol.CmdCreateAgent,
            // Modell deck-eigen erzwingen: das Feld in ~/.claude/settings.json ist der
            // schwächste Hebel und verliert gegen den /model-Merker.
            ["model"] = DeckSettings.Load().Model,
        });
    }

    /// <summary>Einen Agenten schließen.</summary>
    public bool CloseAgent(string slot)
    {
        if (!Broker.IsListening)
            return false;

        return Broker.SendWindow(SlotOrder.WindowOf(slot), new Dictionary<string, object?>
        {
            ["cmd"] = Protocol.CmdCloseAgent,
            ["slot"] = slot,
        });
    }

    /// <summary>
    /// Fenster-Buchstaben, für die eine ＋-Kachel angeboten wird: alle, die gerade
    /// Kacheln haben. (Im Vollbetrieb kämen die verbundenen Fenster vom Broker – ohne
    /// ihn sind die vorhandenen Gruppen die beste verfügbare Auskunft.)
    /// </summary>
    public ObservableCollection<AddTileViewModel> AddTiles { get; } = [];

    private void RefreshAddTiles()
    {
        var fenster = Tiles.Select(t => SlotOrder.WindowOf(t.Slot))
                           .Distinct()
                           .OrderBy(w => w, StringComparer.Ordinal)
                           .ToList();

        foreach (var tot in AddTiles.Where(a => !fenster.Contains(a.Window)).ToList())
            AddTiles.Remove(tot);

        for (var i = 0; i < fenster.Count; i++)
            if (AddTiles.All(a => a.Window != fenster[i]))
                AddTiles.Insert(Math.Min(i, AddTiles.Count), new AddTileViewModel(fenster[i]));
    }

    /// <summary>Neue Reihenfolge nach einem Drag &amp; Drop übernehmen und merken.</summary>
    public void ReorderTiles(string dragged, string target)
    {
        var neu = SlotOrder.Move([.. Tiles.Select(t => t.Slot)], dragged, target);

        for (var i = 0; i < neu.Count; i++)
        {
            var tile = Tiles.FirstOrDefault(t => t.Slot == neu[i]);
            if (tile is not null && Tiles.IndexOf(tile) != i)
                Tiles.Move(Tiles.IndexOf(tile), i);
        }

        _order.Remember(neu);
        _order.Save(_orderPath);
    }

    /// <summary>
    /// Atem-Kurve: eine ruhige Sinuswelle, gemeinsam für alle atmenden Kacheln (in der
    /// Python-Fassung dasselbe Prinzip – ein Takt, keine Kachel läuft eigenständig).
    /// </summary>
    private void Breathe()
    {
        _phase += AnimMs / 1400.0;                       // ~1,4 s pro Atemzug
        var level = 0.72 + 0.28 * (0.5 + 0.5 * Math.Sin(_phase * 2 * Math.PI));

        foreach (var tile in Tiles)
            if (tile.Breathes)
                tile.Pulse = level;

        PulseLevel = level;
        Notify(nameof(PulseLevel));
    }

    /// <summary>
    /// Aktueller Atem-Wert. Auch der Griff hängt daran – er meldet sich per
    /// <see cref="PropertyChanged"/> ab, statt einen eigenen Timer zu führen (zwei
    /// Timer auf derselben Kurve laufen unweigerlich auseinander).
    /// </summary>
    public double PulseLevel { get; private set; } = 1.0;

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Notify([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
