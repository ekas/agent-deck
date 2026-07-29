using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;
using AgentDeck.Core;

namespace AgentDeck.App;

/// <summary>
/// Eine Kachel für die Anzeige. Übersetzt den Core-Zustand (Status-Schlüssel) in das,
/// was XAML binden kann: Farben, Deckkraft, Texte.
/// </summary>
public sealed class TileViewModel : INotifyPropertyChanged
{
    private string _statusKey = StatusStyle.None;
    private string _activity = "";
    private string? _prompt;
    private string? _reference;
    private double _pulse = 1.0;

    public TileViewModel(string slot) => Slot = slot;

    /// <summary>Slot-Name, z. B. <c>A1</c>.</summary>
    public string Slot { get; }

    /// <summary>Normalisierter Anzeige-Status (inkl. <c>lost</c>).</summary>
    public string StatusKey
    {
        get => _statusKey;
        set
        {
            if (_statusKey == value)
                return;
            _statusKey = value;
            // Die abgeleiteten Eigenschaften hängen alle am Status.
            Notify();
            Notify(nameof(GlowColor));
            Notify(nameof(GlowRadius));
            Notify(nameof(GlowOpacity));
            Notify(nameof(Fill));
            Notify(nameof(Breathes));
            Notify(nameof(StatusText));
        }
    }

    public string Activity
    {
        get => _activity;
        set { if (_activity != value) { _activity = value; Notify(); Notify(nameof(SubLine)); } }
    }

    public string? Prompt
    {
        get => _prompt;
        set
        {
            if (_prompt == value)
                return;
            _prompt = value;
            Notify();
            Notify(nameof(SubLine));
            Notify(nameof(TooltipBody));
        }
    }

    /// <summary>
    /// Worauf sich der Chat bezieht – das per Rechtsklick zugewiesene Ticket. Steht
    /// gedimmt auf der Karte und über dem Tooltip.
    /// </summary>
    public string? Reference
    {
        get => _reference;
        set
        {
            if (_reference == value)
                return;
            _reference = value;
            Notify();
            Notify(nameof(HasReference));
        }
    }

    public bool HasReference => !string.IsNullOrEmpty(_reference);

    /// <summary>
    /// Tooltip-Text: die zuletzt gestellte Frage, sonst die laufende Aktivität. Die
    /// KI-Kurzzusammenfassung der Python-Fassung fehlt hier noch.
    /// </summary>
    public string TooltipBody =>
        !string.IsNullOrEmpty(_prompt) ? _prompt!
        : !string.IsNullOrEmpty(_activity) ? _activity
        : "keine Meldung";

    /// <summary>
    /// Atem-Faktor 0…1, den der Animator setzt. Wirkt nur auf die Leuchtstärke, nicht
    /// auf die Größe – eine wandernde Kachel ist ausdrücklich nicht gewünscht.
    /// </summary>
    public double Pulse
    {
        get => _pulse;
        set { if (Math.Abs(_pulse - value) > 0.001) { _pulse = value; Notify(); Notify(nameof(GlowOpacity)); } }
    }

    private StatusStyle Style => StatusStyle.For(_statusKey);

    /// <summary>Farbe des Scheins.</summary>
    public Color GlowColor => Hex(Style.Color);

    /// <summary>Kräftigere Status leuchten weiter hinaus.</summary>
    public double GlowRadius => 8 + 14 * Style.Glow;

    /// <summary>Leuchtstärke, beim Atmen moduliert.</summary>
    public double GlowOpacity => Style.Glow * (Style.Breathes ? _pulse : 1.0);

    /// <summary>
    /// Kachelfläche: Graphit-Basis, in Richtung der Statusfarbe getönt. Gerechnet wird
    /// in <see cref="ColorMath"/> – dieselbe Formel wie <c>canvas_kit.mix</c>, gegen
    /// die Python-Fassung getestet.
    /// </summary>
    public Brush Fill => new SolidColorBrush(Hex(ColorMath.Mix("#23232b", Style.Color, Style.Tint)));

    public bool Breathes => Style.Breathes;

    /// <summary>Lesbare Fassung des Status für die Kachel.</summary>
    public string StatusText => _statusKey switch
    {
        StatusStyle.Idle => "bereit",
        StatusStyle.Done => "ungelesen",
        StatusStyle.Thinking or StatusStyle.Running => "denkt",
        StatusStyle.Waiting => "Rückfrage",
        StatusStyle.Lost => "getrennt",
        _ => "",
    };

    /// <summary>
    /// Zweite Zeile: die laufende Tool-Aktivität, sonst die zuletzt gestellte Frage.
    /// </summary>
    public string SubLine =>
        !string.IsNullOrEmpty(_activity) ? _activity
        : !string.IsNullOrEmpty(_prompt) ? _prompt!
        : StatusText;

    /// <summary>Hex aus <see cref="ColorMath"/> in eine WPF-Farbe wandeln.</summary>
    internal static Color Hex(string hex)
    {
        var (r, g, b) = ColorMath.HexToRgb(hex);
        return Color.FromRgb((byte)r, (byte)g, (byte)b);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Notify([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
