using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Media;
using AgentDeck.Core;

namespace AgentDeck.App;

/// <summary>
/// Der Griff leuchtet in der Farbe des DRINGLICHSTEN Status. Das ist sein eigentlicher
/// Zweck: man sieht bei zugeklapptem Deck, ob jemand etwas von einem will.
/// </summary>
public sealed class HandleViewModel : INotifyPropertyChanged
{
    /// <summary>Griff-Grundton (wie die Frost-Titelleiste), <c>HANDLE_BG</c>.</summary>
    private const string HandleBg = "#15151c";

    /// <summary>Kern zusätzlich Richtung Weiß mischen -> Röhren-Look (<c>NEON_CORE_WHITE</c>).</summary>
    private const double CoreWhite = 0.30;

    /// <summary>Dito, solange der Zeiger auf dem Griff steht (<c>NEON_HOT_WHITE</c>).</summary>
    private const double HotWhite = 0.65;

    /// <summary>
    /// Mindesthelligkeit. Ohne sie wäre der Griff bei <c>idle</c> (Glow 0,22)
    /// praktisch unsichtbar – und damit unauffindbar (<c>NEON_FLOOR</c>).
    /// </summary>
    private const double NeonFloor = 0.45;

    /// <summary>Abstand zwischen Kapsel und Fensterrand, damit der Schein Platz hat.</summary>
    public const double Pad = 13;

    private string _statusKey = StatusStyle.None;
    private bool _hot;
    private double _pulse = 1.0;
    private DockEdge _edge = DockEdge.Left;

    /// <summary>Dominanter Deck-Status.</summary>
    public string StatusKey
    {
        get => _statusKey;
        set { if (_statusKey != value) { _statusKey = value; NotifyLook(); } }
    }

    /// <summary>Zeiger steht auf dem Griff – er wird heller (Rückmeldung ohne Bewegung).</summary>
    public bool Hot
    {
        get => _hot;
        set { if (_hot != value) { _hot = value; NotifyLook(); } }
    }

    /// <summary>Atem-Faktor, wie bei den Kacheln.</summary>
    public double Pulse
    {
        get => _pulse;
        set { if (Math.Abs(_pulse - value) > 0.001) { _pulse = value; NotifyLook(); } }
    }

    public DockEdge Edge
    {
        get => _edge;
        set { if (_edge != value) { _edge = value; Notify(nameof(CapsuleRadius)); } }
    }

    private StatusStyle Style => StatusStyle.For(_statusKey);

    /// <summary>Leuchtstärke: nie unter <see cref="NeonFloor"/>, beim Atmen moduliert.</summary>
    private double Level
    {
        get
        {
            var basis = Math.Max(NeonFloor, Style.Glow);
            return Style.Breathes ? basis * _pulse : basis;
        }
    }

    /// <summary>Farbe des Kerns: Statusfarbe Richtung Weiß, in den Grundton eingeblendet.</summary>
    public Color NeonColor
    {
        get
        {
            var kern = ColorMath.Mix(Style.Color, "#ffffff", _hot ? HotWhite : CoreWhite);
            return TileViewModel.Hex(ColorMath.Mix(HandleBg, kern, Math.Clamp(Level, 0, 1)));
        }
    }

    public Brush NeonBrush => new SolidColorBrush(NeonColor);

    public double BloomRadius => 10 + 12 * Level;

    public double BloomOpacity => Math.Clamp(Level, 0, 1);

    /// <summary>Kapsel = halbrunde Enden, also Radius = halbe Dicke.</summary>
    public CornerRadius CapsuleRadius => new(DockGeometry.HandleThick / 2.0);

    /// <summary>Der Rand, den der Schein braucht.</summary>
    public Thickness CapsuleMargin => new(Pad);

    private void NotifyLook()
    {
        Notify(nameof(NeonColor));
        Notify(nameof(NeonBrush));
        Notify(nameof(BloomRadius));
        Notify(nameof(BloomOpacity));
    }



    public event PropertyChangedEventHandler? PropertyChanged;

    private void Notify([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
