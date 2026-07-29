namespace AgentDeck.Core;

/// <summary>
/// Zeit in dem Format, in dem Deck und Hooks sie austauschen: Unix-Sekunden mit
/// Nachkommastellen, genau wie Pythons <c>time.time()</c>.
///
/// Eigene Klasse, damit niemand versehentlich <c>DateTime.Now</c> in eine
/// State-Datei schreibt – das Format ist Teil des Vertrags mit der Python-Fassung.
/// </summary>
public static class Clock
{
    /// <summary>Jetzt als Unix-Sekunden (mit Millisekunden-Anteil).</summary>
    public static double UnixNow() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
}
