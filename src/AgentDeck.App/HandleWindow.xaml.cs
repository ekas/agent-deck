using System.Windows;
using System.Windows.Input;
using AgentDeck.Core;

namespace AgentDeck.App;

/// <summary>
/// Das Griff-Fenster. Es kennt nur sich selbst: melden, wenn der Zeiger kommt oder geht,
/// und wenn es entlang des Rands gezogen wird. Was daraufhin passiert, entscheidet der
/// <see cref="EdgeDockController"/>.
/// </summary>
public partial class HandleWindow : Window
{
    /// <summary>Ab so vielen Punkten gilt es als Ziehen statt als Klick (<c>DRAG_THRESH</c>).</summary>
    private const double DragThreshold = 4;

    private Point _pressPos;
    private bool _pressed;
    private bool _dragging;

    public HandleWindow()
    {
        InitializeComponent();
        ViewModel = new HandleViewModel();
        DataContext = ViewModel;
    }

    public HandleViewModel ViewModel { get; }

    /// <summary>Der Zeiger hat den Griff erreicht – das Deck soll aufklappen.</summary>
    public event EventHandler? RevealRequested;

    /// <summary>Der Griff wurde entlang des Rands gezogen (neue Position in DIPs).</summary>
    public event EventHandler<double>? Dragged;

    /// <summary>Ziehen beendet – der Controller darf die Position sichern.</summary>
    public event EventHandler? DragFinished;

    protected override void OnMouseEnter(MouseEventArgs e)
    {
        base.OnMouseEnter(e);
        ViewModel.Hot = true;
        RevealRequested?.Invoke(this, EventArgs.Empty);
    }

    protected override void OnMouseLeave(MouseEventArgs e)
    {
        base.OnMouseLeave(e);
        ViewModel.Hot = false;
    }

    protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonDown(e);
        _pressPos = PointToScreen(e.GetPosition(this));
        _pressed = true;
        _dragging = false;
        CaptureMouse();
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        if (!_pressed)
            return;

        var now = PointToScreen(e.GetPosition(this));
        var dx = now.X - _pressPos.X;
        var dy = now.Y - _pressPos.Y;

        if (!_dragging && Math.Abs(dx) + Math.Abs(dy) < DragThreshold)
            return;                                  // noch ein Klick, kein Ziehen
        _dragging = true;

        // Nur die Bewegung ENTLANG des Rands zählt; quer dazu klebt der Griff fest.
        var scale = this.DpiScale();
        var along = ViewModel.Edge.IsVertical() ? now.Y / scale : now.X / scale;
        Dragged?.Invoke(this, along);
    }

    protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonUp(e);
        if (_pressed)
        {
            ReleaseMouseCapture();
            _pressed = false;
            if (_dragging)
                DragFinished?.Invoke(this, EventArgs.Empty);
            else
                RevealRequested?.Invoke(this, EventArgs.Empty);   // reiner Klick = aufklappen
        }
        _dragging = false;
    }
}
