using System.Text.Json;

namespace AgentDeck.Claude;

/// <summary>
/// Lesehilfen für das JSON, das Claude Code den Hooks per stdin übergibt. Die Felder
/// wechseln je Event und je Claude-Code-Version, darum ist hier alles "best effort":
/// falscher Typ oder fehlendes Feld heißt <c>null</c>, nie eine Exception.
/// </summary>
public static class HookPayload
{
    /// <summary>Leeres Objekt als Rückfall, wenn stdin fehlt oder kaputt ist.</summary>
    public static JsonElement Empty { get; } = JsonDocument.Parse("{}").RootElement.Clone();

    /// <summary>
    /// stdin als UTF-8 lesen und parsen. Claude Code liefert IMMER UTF-8; die Kodierung
    /// wird darum explizit gesetzt, statt sich auf die Konsolen-Codepage zu verlassen
    /// (auf deutschem Windows wäre das cp1252 und Umlaute würden zu Mojibake – derselbe
    /// Grund, aus dem <c>report.py</c> <c>sys.stdin.buffer</c> selbst dekodiert).
    /// </summary>
    public static JsonElement ReadStdin()
    {
        try
        {
            if (!Console.IsInputRedirected)
                return Empty;                     // interaktiv gestartet, kein Payload

            using var stdin = Console.OpenStandardInput();
            using var reader = new StreamReader(stdin, new System.Text.UTF8Encoding(false));
            var raw = reader.ReadToEnd();
            if (string.IsNullOrWhiteSpace(raw))
                return Empty;

            using var doc = JsonDocument.Parse(raw);
            return doc.RootElement.Clone();       // Clone: das Document wird verworfen
        }
        catch
        {
            return Empty;
        }
    }

    /// <summary>Zeichenkette eines Feldes; fehlt es oder ist es kein String -> <c>null</c>.</summary>
    public static string? Str(this JsonElement data, string name) =>
        data.ValueKind == JsonValueKind.Object
        && data.TryGetProperty(name, out var v)
        && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    /// <summary>Zahl eines Feldes; fehlt es oder ist es keine Zahl -> <c>null</c>.</summary>
    public static double? Num(this JsonElement data, string name) =>
        data.ValueKind == JsonValueKind.Object
        && data.TryGetProperty(name, out var v)
        && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble()
            : null;

    /// <summary>Unterobjekt; fehlt es oder ist es kein Objekt -> leeres Objekt.</summary>
    public static JsonElement Obj(this JsonElement data, string name) =>
        data.ValueKind == JsonValueKind.Object
        && data.TryGetProperty(name, out var v)
        && v.ValueKind == JsonValueKind.Object
            ? v
            : Empty;

    /// <summary>
    /// Erster vorhandener Zahlenwert aus mehreren Feldnamen – Pythons <c>_num(*vals)</c>
    /// für wechselnde Feldbenennungen (<c>input_tokens</c> vs. <c>input</c>).
    /// </summary>
    public static double? FirstNum(this JsonElement data, params string[] names)
    {
        foreach (var n in names)
            if (data.Num(n) is { } v)
                return v;
        return null;
    }

    /// <summary>
    /// Feld, das entweder ein String oder ein Objekt mit Unterschlüssel ist – so meldet
    /// Claude Code <c>effort</c> (mal <c>"high"</c>, mal <c>{"level":"high"}</c>) und
    /// <c>model</c> (mal String, mal <c>{"display_name":…}</c>).
    /// </summary>
    public static string? StrOrNested(this JsonElement data, string name, params string[] innerNames)
    {
        if (data.ValueKind != JsonValueKind.Object || !data.TryGetProperty(name, out var v))
            return null;
        if (v.ValueKind == JsonValueKind.String)
            return v.GetString();
        if (v.ValueKind == JsonValueKind.Object)
            foreach (var inner in innerNames)
                if (v.Str(inner) is { Length: > 0 } s)
                    return s;
        return null;
    }

}
