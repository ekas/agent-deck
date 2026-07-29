using System.Text;
using System.Text.Json;

namespace AgentDeck.Testing;

/// <summary>
/// Zugriff auf die Golden-Master-Dateien unter <c>tests/golden</c>.
///
/// Die Dateien entstehen aus der PYTHON-Fassung (<c>tools/gen_golden.py</c>) und sind
/// die Messlatte für den Port: dieselbe Eingabe muss dasselbe Ergebnis liefern. Sie
/// liegen als Datei vor und nicht als Live-Aufruf, damit die Tests ohne Python laufen
/// – auch dann noch, wenn die Python-Fassung gelöscht ist.
/// </summary>
public static class Golden
{
    /// <summary>Alle Fälle einer Golden-Datei.</summary>
    public static JsonElement[] Load(string name)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "tests", "golden")))
            dir = dir.Parent;

        Assert.NotNull(dir);   // ohne Golden-Dateien ist der Test wertlos, nicht "grün"

        var path = Path.Combine(dir!.FullName, "tests", "golden", name + ".json");
        Assert.True(File.Exists(path), $"Golden-Datei fehlt: {path}");

        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        return [.. doc.RootElement.EnumerateArray().Select(e => e.Clone())];
    }

    /// <summary>
    /// Sammelt Abweichungen, statt beim ersten Fehler abzubrechen. Bei einer Portierung
    /// will man das GANZE Bild sehen – ein einzelner Fehlschlag verrät nicht, ob eine
    /// Regel oder nur ein Randfall daneben liegt.
    /// </summary>
    public sealed class Diff
    {
        private readonly List<string> _abweichungen = [];
        private int _geprueft;

        public void Check(bool gleich, string fall, object? erwartet, object? bekommen)
        {
            _geprueft++;
            if (gleich)
                return;
            if (_abweichungen.Count < 25)      // die ersten 25 genügen zur Diagnose
                _abweichungen.Add($"  {fall}\n      Python: {Fmt(erwartet)}\n      C#:     {Fmt(bekommen)}");
        }

        private static string Fmt(object? v) => v switch
        {
            null => "null",
            string s => $"\"{s}\"",
            bool b => b ? "true" : "false",
            _ => v.ToString() ?? "?",
        };

        /// <summary>Am Ende aufrufen: schlägt fehl, wenn etwas abweicht.</summary>
        public void Assert(string was)
        {
            Xunit.Assert.True(_geprueft > 0, $"{was}: keine Fälle geprüft – Golden-Datei leer?");

            if (_abweichungen.Count == 0)
                return;

            var sb = new StringBuilder();
            sb.AppendLine($"{was}: {_abweichungen.Count} von {_geprueft} Fällen weichen von der Python-Fassung ab:");
            foreach (var a in _abweichungen)
                sb.AppendLine(a);
            Xunit.Assert.Fail(sb.ToString());
        }
    }

    // ── Lesehilfen für die heterogenen Fälle ────────────────────────────
    public static string? Str(this JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    public static double Dbl(this JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : 0;

    public static double? DblOrNull(this JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;

    public static int? IntOrNull(this JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : null;

    public static bool Bool(this JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    public static bool IsNull(this JsonElement e, string name) =>
        !e.TryGetProperty(name, out var v) || v.ValueKind == JsonValueKind.Null;
}
