using System.Text.Json;

namespace AgentDeck.Core;

/// <summary>
/// Ein Ort für den State-Ordner und atomares JSON-Lesen/Schreiben – die .NET-Fassung
/// von <c>deck_paths.py</c>.
///
/// WICHTIG: Der Ordner MUSS derselbe sein wie in der Python-Fassung. Solange beide
/// Fassungen koexistieren, lesen und schreiben sie dieselben Dateien; ein abweichender
/// Pfad würde bedeuten, dass das .NET-Panel die Hook-Meldungen der Python-Hooks nicht
/// sieht (und umgekehrt).
/// </summary>
public static class DeckPaths
{
    /// <summary>
    /// Slot-Zustände liegen als kleine JSON-Dateien in diesem Ordner.
    /// Entspricht: <c>%LOCALAPPDATA%\claude-agent-deck\state</c>, mit dem
    /// Benutzerprofil als Rückfallebene (wie <c>os.path.expanduser("~")</c>).
    /// </summary>
    public static string StateDir { get; } = Path.Combine(
        Environment.GetEnvironmentVariable("LOCALAPPDATA")
            is { Length: > 0 } localAppData
            ? localAppData
            : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        "claude-agent-deck", "state");

    /// <summary>Zustandsdatei eines Slots (<c>A1.json</c>).</summary>
    public static string StatePath(string slot) => Path.Combine(StateDir, slot + ".json");

    /// <summary>
    /// Marker-Datei, in die ein Agent bei der "Im Chat suchen"-Zuweisung die selbst
    /// gefundene Ticket-ID schreibt (Klartext, eine Zeile).
    /// </summary>
    public static string FoundTicketPath(string slot) => Path.Combine(StateDir, slot + ".ticket");

    /// <summary>
    /// Marker-Datei mit dem absoluten Pfad des für ein Ticket angelegten git worktree.
    /// Beim Schließen des Agenten räumt das Deck genau diesen worktree wieder auf.
    /// </summary>
    public static string WorktreeMarkerPath(string slot) => Path.Combine(StateDir, slot + ".worktree");

    /// <summary>
    /// Kompatibel zu Pythons <c>json.dump</c>: keine Einrückung, Umlaute als echte
    /// UTF-8-Zeichen (nicht \u-escaped), damit die Dateien für beide Seiten gleich
    /// aussehen und im Editor lesbar bleiben.
    /// </summary>
    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// JSON aus <paramref name="path"/> lesen. Fehlt die Datei, ist sie halb geschrieben
    /// oder kaputt, kommt <c>null</c> zurück – NIE eine Exception. Das ist wichtig, weil
    /// der Leser (Panel) und der Schreiber (Hook) ohne Sperre arbeiten.
    /// </summary>
    public static T? LoadJson<T>(string path) where T : class
    {
        try
        {
            using var stream = File.OpenRead(path);
            return JsonSerializer.Deserialize<T>(stream, JsonOptions);
        }
        catch
        {
            return null;   // fehlt / halb geschrieben / kaputt
        }
    }

    /// <summary>
    /// Atomar schreiben: erst <c>&lt;path&gt;.tmp</c>, dann ersetzen – so sieht ein
    /// gleichzeitig lesendes Panel nie eine halbe Datei. Legt den Zielordner bei
    /// Bedarf an.
    /// </summary>
    public static void SaveJson<T>(string path, T data)
    {
        var dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir))
            Directory.CreateDirectory(dir);

        var tmp = path + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(data, JsonOptions),
                          new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

        // File.Move mit overwrite entspricht os.replace: atomar auf demselben Volume.
        File.Move(tmp, path, overwrite: true);
    }

    /// <summary>Zustand eines Slots lesen; nicht vorhanden/kaputt -> <c>null</c>.</summary>
    public static SlotState? LoadSlot(string slot) => LoadJson<SlotState>(StatePath(slot));
}
