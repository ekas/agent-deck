using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using AgentDeck.Core;

namespace AgentDeck.App;

/// <summary>
/// Das Deck-Fenster. Hält das ViewModel, den Griff und die Andock-Steuerung; alles
/// Sichtbare steckt in MainWindow.xaml, alles Fachliche in AgentDeck.Core.
/// </summary>
public partial class MainWindow : Window
{
    /// <summary>Ab so vielen Punkten gilt es als Ziehen statt als Klick.</summary>
    private const double DragThreshold = 4;

    private readonly DeckViewModel _vm = new();
    private readonly HandleWindow _handle = new();
    private EdgeDockController? _dock;
    private DeckSettings _settings = DeckSettings.Load();

    private Point _pressPos;
    private string? _pressedSlot;
    private bool _dragging;

    public MainWindow()
    {
        InitializeComponent();
        DataContext = _vm;

        Loaded += OnLoaded;
        Closed += OnClosed;
    }

    private void OnLoaded(object? sender, RoutedEventArgs e)
    {
        _vm.Start();

        _dock = new EdgeDockController(this, _handle, _settings.Edge, _settings.DockAlong);
        _dock.AlongChanged += (_, along) =>
        {
            _settings = _settings with { DockAlong = along };
            _settings.Save();
        };
        _dock.Attach();

        // Der Griff leuchtet in der Farbe des dringlichsten Status und atmet im selben
        // Takt - dafür hängt er am selben ViewModel wie die Kacheln.
        _vm.PropertyChanged += (_, args) =>
        {
            switch (args.PropertyName)
            {
                case nameof(DeckViewModel.DominantStatus):
                    _handle.ViewModel.StatusKey = _vm.DominantStatus;
                    break;
                case nameof(DeckViewModel.PulseLevel):
                    _handle.ViewModel.Pulse = _vm.PulseLevel;
                    break;
            }
        };

        // Den aktuellen Stand EINMAL nachziehen: Start() lief oben schon durch, seine
        // Meldung hat der Griff also verpasst - sonst bliebe er grau, bis sich der
        // dominante Status zufällig das nächste Mal ändert.
        _handle.ViewModel.StatusKey = _vm.DominantStatus;
    }

    private void OnClosed(object? sender, EventArgs e)
    {
        _dock?.Dispose();
        _handle.Close();
        _vm.Dispose();
    }

    // ── Kachel: Klick fokussiert, Ziehen sortiert um ────────────────────
    private void Tile_MouseDown(object sender, MouseButtonEventArgs e)
    {
        _pressPos = e.GetPosition(this);
        _pressedSlot = SlotOf(sender);
        _dragging = false;
    }

    private void Tile_MouseMove(object sender, MouseEventArgs e)
    {
        if (_pressedSlot is null || e.LeftButton != MouseButtonState.Pressed || _dragging)
            return;

        var now = e.GetPosition(this);
        if (Math.Abs(now.X - _pressPos.X) + Math.Abs(now.Y - _pressPos.Y) < DragThreshold)
            return;

        _dragging = true;
        if (sender is DependencyObject src)
            DragDrop.DoDragDrop(src, _pressedSlot, DragDropEffects.Move);
    }

    private void Tile_MouseUp(object sender, MouseButtonEventArgs e)
    {
        // Nur ein echter Klick (ohne Ziehen) fokussiert das Terminal.
        if (!_dragging && _pressedSlot is not null && SlotOf(sender) == _pressedSlot)
            _vm.FocusSlot(_pressedSlot);

        _pressedSlot = null;
        _dragging = false;
    }

    private void Tile_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.StringFormat)
            ? DragDropEffects.Move
            : DragDropEffects.None;
        e.Handled = true;
    }

    private void Tile_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(DataFormats.StringFormat) is not string gezogen)
            return;
        if (SlotOf(sender) is not { } ziel || ziel == gezogen)
            return;

        _vm.ReorderTiles(gezogen, ziel);
        e.Handled = true;
    }

    /// <summary>Der Slot steckt als Tag am Kachel-Border (siehe DataTemplate).</summary>
    private static string? SlotOf(object sender) =>
        (sender as FrameworkElement)?.Tag as string;

    // ── Kontextmenü ─────────────────────────────────────────────────────
    /// <summary>
    /// Slot des Menüeintrags: das ContextMenu trägt ihn als Tag, der Eintrag selbst
    /// seinen Wert. Bei Untermenüs ist der logische Elternteil wieder ein MenuItem –
    /// deshalb wird so lange hochgelaufen, bis das ContextMenu erreicht ist.
    /// </summary>
    private static (string? Slot, string? Value) MenuContext(object sender)
    {
        if (sender is not MenuItem item)
            return (null, null);

        DependencyObject? knoten = item;
        while (knoten is MenuItem mi)
            knoten = mi.Parent;

        return ((knoten as ContextMenu)?.Tag as string, item.Tag as string);
    }

    private void Mode_Click(object sender, RoutedEventArgs e)
    {
        var (slot, modus) = MenuContext(sender);
        if (slot is not null && modus is not null)
            _vm.SetMode(slot, modus);
    }

    private void Model_Click(object sender, RoutedEventArgs e)
    {
        var (slot, model) = MenuContext(sender);
        if (slot is not null && model is not null)
            _vm.SetModel(slot, model);
    }

    private void Send_Click(object sender, RoutedEventArgs e)
    {
        var (slot, text) = MenuContext(sender);
        if (slot is not null && text is not null)
            _vm.Send(slot, text);
    }

    private void CloseAgent_Click(object sender, RoutedEventArgs e)
    {
        var (slot, _) = MenuContext(sender);
        if (slot is not null)
            _vm.CloseAgent(slot);
    }

    /// <summary>＋-Kachel: einen weiteren Chat in diesem Fenster öffnen.</summary>
    private void AddTile_Click(object sender, MouseButtonEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is string window)
            _vm.CreateAgent(window);
    }

    // ── Einstellungen ───────────────────────────────────────────────────
    private void Settings_Click(object sender, MouseButtonEventArgs e)
    {
        // Klein gehalten: die Kante durchschalten. Der vollständige Einstellungs-Dialog
        // der Python-Fassung (Modell, Sprache, Präfix, Neustart) fehlt noch.
        var naechste = _settings.Edge switch
        {
            DockEdge.Off => DockEdge.Left,
            DockEdge.Left => DockEdge.Top,
            DockEdge.Top => DockEdge.Right,
            _ => DockEdge.Off,
        };

        _settings = _settings.WithEdge(naechste);
        _settings.Save();
        _dock?.SetEdge(naechste);
    }
}
