using System.Text.Json.Serialization;

namespace AgentDeck.Core;

/// <summary>
/// Die Deck-eigenen Einstellungen aus <c>deck_settings.json</c> – dieselbe Datei wie in
/// der Python-Fassung, damit beide dasselbe Andock-Verhalten zeigen.
///
/// Warum das Deck ein eigenes Modell führt statt <c>~/.claude/settings.json</c> zu
/// nutzen: das dortige <c>model</c>-Feld ist der schwächste Hebel (es verwirft
/// <c>[1m]</c> und verliert gegen den <c>/model</c>-Merker), darum steht das Modell hier.
/// </summary>
public sealed record DeckSettings
{
    [JsonPropertyName("slim")]
    public bool Slim { get; init; } = true;

    [JsonPropertyName("glow")]
    public bool Glow { get; init; } = true;

    /// <summary>Jira-Präfix für die Ticket-Erkennung, z. B. <c>PROJ</c>.</summary>
    [JsonPropertyName("jira_prefix")]
    public string? JiraPrefix { get; init; }

    /// <summary>An welchem Rand das Deck klebt: <c>off</c>, <c>left</c>, <c>right</c>, <c>top</c>.</summary>
    [JsonPropertyName("dock_edge")]
    public string DockEdgeName { get; init; } = "off";

    /// <summary>Position entlang des Rands (y bei links/rechts, x bei oben).</summary>
    [JsonPropertyName("dock_along")]
    public double DockAlong { get; init; }

    [JsonPropertyName("model")]
    public string? Model { get; init; }

    [JsonPropertyName("language")]
    public string? Language { get; init; }

    /// <summary><see cref="DockEdgeName"/> als Enum; Unbekanntes gilt als "nicht angedockt".</summary>
    [JsonIgnore]
    public DockEdge Edge => DockEdgeName?.ToLowerInvariant() switch
    {
        "left" => DockEdge.Left,
        "right" => DockEdge.Right,
        "top" => DockEdge.Top,
        _ => DockEdge.Off,
    };

    public DeckSettings WithEdge(DockEdge edge) =>
        this with { DockEdgeName = edge.ToString().ToLowerInvariant() };

    /// <summary>
    /// Ablageort finden: bevorzugt die Datei neben dem Python-Code (dann teilen sich
    /// beide Fassungen die Einstellung), sonst im State-Ordner. Gesucht wird vom
    /// Startverzeichnis aus nach oben – im Entwicklungsbetrieb liegt das Exe tief in
    /// <c>bin/Debug/…</c>.
    /// </summary>
    public static string FindPath(string? startDir = null)
    {
        // Ausdrücklich gesetztes Zuhause gewinnt. Nötig, um eine zweite Instanz (oder
        // einen Testlauf) laufen zu lassen, ohne die produktive Einstellung zu
        // überschreiben.
        if (Environment.GetEnvironmentVariable("AGENT_DECK_HOME") is { Length: > 0 } home)
            return Path.Combine(home, "deck_settings.json");

        var dir = new DirectoryInfo(startDir ?? AppContext.BaseDirectory);
        while (dir is not null)
        {
            // config.py als Merkmal der Repo-Wurzel - deck_settings.json selbst taugt
            // nicht, weil sie beim ersten Start noch fehlt.
            if (File.Exists(Path.Combine(dir.FullName, "config.py")))
                return Path.Combine(dir.FullName, "deck_settings.json");
            dir = dir.Parent;
        }
        return Path.Combine(Path.GetDirectoryName(DeckPaths.StateDir)!, "deck_settings.json");
    }

    public static DeckSettings Load(string? path = null) =>
        DeckPaths.LoadJson<DeckSettings>(path ?? FindPath()) ?? new DeckSettings();

    public void Save(string? path = null)
    {
        try
        {
            DeckPaths.SaveJson(path ?? FindPath(), this);
        }
        catch
        {
            // Einstellungen sind Komfort - ein Schreibfehler darf das Deck nicht aufhalten.
        }
    }
}
