using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using AgentDeck.Core;
using AgentDeck.Windows;

namespace AgentDeck.App;

/// <summary>
/// Andocken am Bildschirmrand mit Auto-Hide – die Steuerung hinter <c>edge_dock.py</c>.
///
/// Der eine Zustand, den es nicht geben darf, ist ein HALB ausgefahrenes Deck: man sieht
/// nichts und kommt an nichts mehr heran, denn angedockt gibt es keine Titelleiste.
/// Dagegen stehen hier drei Vorkehrungen, jede gegen einen im Original beobachteten Weg
/// dorthin:
/// <list type="bullet">
/// <item>ein einziger Ausgang (<see cref="FinishAnimation"/>) – Ankunft, Notbremse und
///   Fehler beim Bewegen laufen alle dort durch;</item>
/// <item>eine Notbremse: dauert ein Slide ungewöhnlich lange, springt er ans Ziel;</item>
/// <item>ein Watchdog im Zeiger-Poll, der eine eingeschlafene Animation bemerkt.</item>
/// </list>
///
/// Der Takt kommt von <see cref="CompositionTarget.Rendering"/>, also EIN Frame je Bild,
/// das der Monitor zeigt. Ein eigener Timer mit festem Intervall war im Original die
/// Ursache für sichtbares Stottern, weil er gegen die Bildrate lief.
/// </summary>
public sealed class EdgeDockController : IDisposable
{
    /// <summary>Takt des Zeiger-Polls (nur fürs Einklappen), <c>POLL_MS</c>.</summary>
    private const int PollMs = 70;

    /// <summary>So lange muss der Zeiger draußen sein, bevor eingeklappt wird.</summary>
    private const int CollapseDelayMs = 320;

    /// <summary>Notbremse: länger darf ein Slide nicht dauern (<c>ANIM_DEADLINE_MS</c>).</summary>
    private const int AnimDeadlineMs = 900;

    private readonly Window _panel;
    private readonly HandleWindow _handle;
    private readonly Spring _spring = new(0.0);
    private readonly DispatcherTimer _poll;

    private DockEdge _edge;
    private double _along;
    private bool _animating;
    private long _lastFrameTicks;
    private long _animStartTicks;
    private long _outsideSinceTicks;
    private bool _targetRevealed;

    public EdgeDockController(Window panel, HandleWindow handle, DockEdge edge, double along)
    {
        _panel = panel;
        _handle = handle;
        _edge = edge;
        _along = along;

        _handle.ViewModel.Edge = edge;
        _handle.RevealRequested += (_, _) => Reveal();
        _handle.Dragged += (_, along) => MoveAlong(along);
        _handle.DragFinished += (_, _) => AlongChanged?.Invoke(this, _along);

        _poll = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromMilliseconds(PollMs),
        };
        _poll.Tick += (_, _) => PollPointer();
    }

    /// <summary>Der Griff wurde verschoben – der Aufrufer sichert die neue Position.</summary>
    public event EventHandler<double>? AlongChanged;

    /// <summary>Ist das Deck (fast) ausgefahren?</summary>
    public bool IsRevealed => _spring.Position > 0.5;

    public DockEdge Edge => _edge;

    public double Along => _along;

    /// <summary>Andocken einschalten: Panel rahmenlos machen, Griff zeigen, eingeklappt starten.</summary>
    public void Attach()
    {
        if (_edge == DockEdge.Off)
            return;

        _panel.WindowStyle = WindowStyle.None;
        _panel.ResizeMode = ResizeMode.NoResize;
        _panel.Topmost = true;
        _panel.ShowInTaskbar = false;

        _spring.SetTarget(0.0, Spring.CollapseResponseMs);
        _spring.SnapToTarget();

        LayoutNow();
        ShowHandle();
        _panel.Visibility = Visibility.Hidden;
        _poll.Start();
    }

    /// <summary>Andocken abschalten: wieder ein gewöhnliches Fenster.</summary>
    public void Detach()
    {
        _poll.Stop();
        StopAnimation();

        _handle.Hide();
        _panel.Visibility = Visibility.Visible;
        _panel.WindowStyle = WindowStyle.SingleBorderWindow;
        _panel.ResizeMode = ResizeMode.CanResize;
        _panel.Topmost = false;
        _panel.ShowInTaskbar = true;
        _edge = DockEdge.Off;
    }

    public void SetEdge(DockEdge edge)
    {
        _edge = edge;
        _handle.ViewModel.Edge = edge;
        if (edge == DockEdge.Off)
            Detach();
        else
            Attach();
    }

    /// <summary>
    /// Ausfahren. Reihenfolge gegen Löcher am Rand: ERST das Panel an der
    /// Startposition zeigen, DANN den Griff verstecken.
    /// </summary>
    public void Reveal()
    {
        if (_targetRevealed && _animating)
            return;
        _targetRevealed = true;

        LayoutNow();
        _panel.Visibility = Visibility.Visible;
        _handle.Hide();

        _spring.SetTarget(1.0, Spring.RevealResponseMs);
        StartAnimation();
    }

    /// <summary>
    /// Einklappen. Umgekehrte Reihenfolge: ERST den Griff zeigen, DANN das Panel
    /// wegnehmen (das passiert am Ende der Animation).
    /// </summary>
    public void Collapse()
    {
        if (!_targetRevealed && !_animating)
            return;
        _targetRevealed = false;

        ShowHandle();
        _spring.SetTarget(0.0, Spring.CollapseResponseMs);
        StartAnimation();
    }

    /// <summary>Griff entlang des Rands verschieben.</summary>
    public void MoveAlong(double along)
    {
        _along = along;
        LayoutNow();
    }

    // ── Animation ───────────────────────────────────────────────────────
    private void StartAnimation()
    {
        _animStartTicks = Environment.TickCount64;
        _lastFrameTicks = _animStartTicks;
        if (_animating)
            return;
        _animating = true;
        CompositionTarget.Rendering += OnFrame;
    }

    private void StopAnimation()
    {
        if (!_animating)
            return;
        _animating = false;
        CompositionTarget.Rendering -= OnFrame;
    }

    private void OnFrame(object? sender, EventArgs e)
    {
        try
        {
            var now = Environment.TickCount64;
            var dt = Math.Max(1, now - _lastFrameTicks) / 1000.0;
            _lastFrameTicks = now;

            // Notbremse: lieber hart ans Ziel als auf halber Strecke stehen bleiben.
            if (now - _animStartTicks > AnimDeadlineMs)
            {
                _spring.SnapToTarget();
                LayoutNow();
                FinishAnimation();
                return;
            }

            var fertig = _spring.Advance(dt, SpanPx());
            LayoutNow();

            if (fertig)
                FinishAnimation();
        }
        catch
        {
            // Auch der Fehlerfall geht durch den EINEN Ausgang - im Original endete er
            // stillschweigend mitten in der Bewegung.
            _spring.SnapToTarget();
            FinishAnimation();
        }
    }

    /// <summary>
    /// Der einzige Ausgang aus der Animation. Hier wird der Endzustand hergestellt:
    /// eingeklappt heißt Panel weg und Griff da.
    /// </summary>
    private void FinishAnimation()
    {
        StopAnimation();

        if (_spring.Position <= 0.001)
        {
            _panel.Visibility = Visibility.Hidden;
            ShowHandle();
        }
        else
        {
            _handle.Hide();
        }
        LayoutNow();
    }

    /// <summary>Länge des Wegs quer zum Rand – Maßstab für die Ankunftsschwelle.</summary>
    private double SpanPx() =>
        _edge.IsVertical() ? Math.Max(1, _panel.Width) : Math.Max(1, _panel.Height);

    // ── Platzierung ─────────────────────────────────────────────────────
    private void LayoutNow()
    {
        if (_edge == DockEdge.Off)
            return;

        var work = WorkArea();

        var panel = DockGeometry.PanelRect(
            _edge, work, _panel.Width, _panel.Height, _along, _spring.Position);
        _panel.Left = panel.X;
        _panel.Top = panel.Y;

        var handle = DockGeometry.HandleRect(
            _edge, work, _along, _edge.IsVertical() ? _panel.Height : _panel.Width);

        // Das Griff-FENSTER ist rundum um Pad größer als die Kapsel, damit der Schein
        // nicht am Fensterrand abgeschnitten wird.
        _handle.Left = handle.X - HandleViewModel.Pad;
        _handle.Top = handle.Y - HandleViewModel.Pad;
        _handle.Width = handle.Width + 2 * HandleViewModel.Pad;
        _handle.Height = handle.Height + 2 * HandleViewModel.Pad;
    }

    private DockRect WorkArea()
    {
        // Der Monitor unter dem Griff, nicht zwingend der primäre. Solange das Panel
        // versteckt ist, taugt sein eigenes Handle nicht als Anhaltspunkt.
        if (_panel.IsVisible)
            return _panel.WorkArea();

        var (px, py) = Win32Screen.CursorPixel();
        return Win32Screen.WorkAreaAtPixel(px, py, _panel.DpiScale());
    }

    private void ShowHandle()
    {
        LayoutNow();
        if (!_handle.IsVisible)
            _handle.Show();
    }

    // ── Zeiger beobachten (nur fürs Einklappen) ─────────────────────────
    private void PollPointer()
    {
        if (_edge == DockEdge.Off)
            return;

        // Watchdog: eine Animation, die weder läuft noch angekommen ist, gibt es nicht.
        if (!_animating && _spring.Position is > 0.001 and < 0.999)
        {
            _spring.SnapToTarget();
            FinishAnimation();
            return;
        }

        if (!IsRevealed)
            return;

        var (mx, my) = _panel.CursorPosition();
        var panel = new DockRect(_panel.Left, _panel.Top, _panel.Width, _panel.Height);

        if (DockGeometry.PointerInside(panel, mx, my))
        {
            _outsideSinceTicks = 0;
            return;
        }

        // Erst nach einer Weile draußen einklappen - sonst rutscht das Deck bei jedem
        // kurzen Abgleiten weg.
        var now = Environment.TickCount64;
        if (_outsideSinceTicks == 0)
            _outsideSinceTicks = now;
        else if (now - _outsideSinceTicks >= CollapseDelayMs)
        {
            _outsideSinceTicks = 0;
            Collapse();
        }
    }

    public void Dispose()
    {
        _poll.Stop();
        StopAnimation();
    }
}
