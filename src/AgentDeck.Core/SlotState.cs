using System.Text.Json.Serialization;

namespace AgentDeck.Core;

/// <summary>
/// Der von den Claude-Code-Hooks gemeldete Zustand eines Slots – das Gegenstück zu
/// dem Dict, das <c>report.py</c> nach <c>&lt;slot&gt;.json</c> schreibt.
///
/// Die Feldnamen sind FESTGELEGT durch die Python-Fassung (snake_case, deshalb die
/// <see cref="JsonPropertyNameAttribute"/>). Nur manche Hook-Events liefern manche
/// Felder; <c>report.py</c> erhält vorhandene Werte, statt sie zu überschreiben –
/// darum ist hier fast alles nullable.
/// </summary>
public sealed record SlotState
{
    /// <summary>Slot-Name, z. B. <c>A1</c>.</summary>
    [JsonPropertyName("slot")]
    public string? Slot { get; init; }

    /// <summary>
    /// Gemeldeter Zustand: <c>idle</c>, <c>thinking</c>, <c>running</c>,
    /// <c>waiting</c>, <c>done</c>.
    /// </summary>
    [JsonPropertyName("status")]
    public string? Status { get; init; }

    /// <summary>
    /// Zeitpunkt der Meldung als Unix-Sekunden (Pythons <c>time.time()</c>, also mit
    /// Nachkommastellen). Als <see cref="double"/> und NICHT als DateTime, damit die
    /// Datei bit-für-bit dasselbe Format behält wie in der Python-Fassung.
    /// </summary>
    [JsonPropertyName("ts")]
    public double Ts { get; init; }

    /// <summary>Permission-Mode, auf Namen aus <c>config.MODE_CYCLE</c> abgebildet.</summary>
    [JsonPropertyName("mode")]
    public string? Mode { get; init; }

    /// <summary>Reasoning-Effort, wie von der statusLine gemeldet.</summary>
    [JsonPropertyName("effort")]
    public string? Effort { get; init; }

    /// <summary>
    /// Kurzbeschreibung des gerade genutzten Tools ("Bash: npm test"). Bei <c>Stop</c>
    /// bzw. Status <c>done</c> bewusst leer, sonst wird der Vorwert behalten.
    /// </summary>
    [JsonPropertyName("activity")]
    public string? Activity { get; init; }

    /// <summary>Session-ID – Schlüssel für den Zusammenfassungs-Cache.</summary>
    [JsonPropertyName("session_id")]
    public string? SessionId { get; init; }

    /// <summary>
    /// Arbeitsverzeichnis des Agenten (= Repo-Root, stabil über die Session; ein
    /// <c>cd</c> im Bash-Tool ändert das cwd des Hook-Prozesses nicht). Nur damit
    /// findet das Deck beim Schließen den worktree eines Ticket-Branches wieder.
    /// </summary>
    [JsonPropertyName("cwd")]
    public string? Cwd { get; init; }

    /// <summary>
    /// Zuletzt abgeschickte Frage (nur <c>UserPromptSubmit</c> liefert sie), auf 500
    /// Zeichen gekürzt.
    /// </summary>
    [JsonPropertyName("prompt")]
    public string? Prompt { get; init; }
}
