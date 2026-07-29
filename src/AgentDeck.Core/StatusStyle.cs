namespace AgentDeck.Core;

/// <summary>
/// Wie ein Status aussieht: Farbe, Leuchtstärke, ob er atmet, wie stark er die
/// Kachelfläche tönt. Portiert aus <c>GLOW_STYLE</c> in <c>agent_deck.py</c>.
///
/// Liegt im Core und nicht in der App, weil zwei Dinge daran hängen, die keine
/// Oberfläche brauchen: die Menge der gültigen Status für
/// <see cref="StatusModel.NormalizeStatus"/> und die Farbe des Griff-Balkens.
/// </summary>
/// <param name="Color">Grundfarbe als Hex (<c>#rrggbb</c>).</param>
/// <param name="Glow">Leuchtstärke 0…1.</param>
/// <param name="Breathes">Pulsiert die Kachel (nur aktive Zustände)?</param>
/// <param name="Tint">Wie stark die Statusfarbe die Kachelfläche tönt, 0…1.</param>
public sealed record StatusStyle(string Color, double Glow, bool Breathes, double Tint)
{
    /// <summary>Grau – fast neutral, "nichts zu tun".</summary>
    public const string Idle = "idle";

    /// <summary>Grün – ungelesene Antwort.</summary>
    public const string Done = "done";

    /// <summary>Cyan – denkt (atmet).</summary>
    public const string Thinking = "thinking";

    /// <summary>Cyan – arbeitet an einem Tool (atmet); gleiche Optik wie <see cref="Thinking"/>.</summary>
    public const string Running = "running";

    /// <summary>Amber – braucht dich (atmet).</summary>
    public const string Waiting = "waiting";

    /// <summary>Kein Agent: kein Glow, kein Farbton.</summary>
    public const string None = "none";

    /// <summary>
    /// Rot ist KEIN gemeldeter Agent-Status, sondern wird im Panel berechnet: die
    /// Extension des Fensters hängt nicht (mehr) am Broker.
    /// </summary>
    public const string Lost = "lost";

    /// <summary>Füll-Tönung für "getrennt" (<c>LOST_FILL</c>).</summary>
    public const double LostFill = 0.30;

    /// <summary>Farbe für "getrennt".</summary>
    public const string LostColor = "#ff6b6b";

    /// <summary>
    /// Die Werte aus <c>GLOW_STYLE</c>, unverändert übernommen – bis auf ihre
    /// Herkunft ist das dieselbe Tabelle.
    /// </summary>
    public static readonly IReadOnlyDictionary<string, StatusStyle> All =
        new Dictionary<string, StatusStyle>
        {
            [Idle] = new("#8b8b99", 0.22, false, 0.06),
            [Done] = new("#6ee7a8", 0.85, false, 0.28),
            [Thinking] = new("#7ecbff", 1.00, true, 0.28),
            [Running] = new("#7ecbff", 1.00, true, 0.28),
            [Waiting] = new("#ffc48a", 1.00, true, 0.30),
            [None] = new("#8b8b99", 0.00, false, 0.00),
        };

    /// <summary>
    /// Die gültigen Status – genau die Schlüssel von <c>GLOW_STYLE</c>, wie
    /// <see cref="StatusModel.NormalizeStatus"/> sie als <c>valid</c> erwartet.
    /// </summary>
    public static readonly IReadOnlySet<string> ValidStatus = All.Keys.ToHashSet();

    /// <summary>
    /// Stil eines Status. <c>lost</c> ist eigens behandelt (im Panel berechnet, nicht
    /// gemeldet); Unbekanntes fällt auf <see cref="Idle"/> zurück.
    /// </summary>
    public static StatusStyle For(string? status) => status switch
    {
        Lost => new StatusStyle(LostColor, 1.00, false, LostFill),
        not null when All.TryGetValue(status, out var s) => s,
        _ => All[Idle],
    };
}
