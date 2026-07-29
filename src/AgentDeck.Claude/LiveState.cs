using System.Text.Json;
using System.Text.Json.Serialization;

namespace AgentDeck.Claude;

/// <summary>
/// Die Live-Werte, die die statusLine nach <c>&lt;slot&gt;.live.json</c> schreibt:
/// Modell, Effort, Kontext-Auslastung, Tokens der aktuellen Nachricht, Kosten.
/// Gegenstück zu <c>statusline.py:_extract</c>.
/// </summary>
public sealed record LiveState
{
    [JsonPropertyName("model")]
    public string? Model { get; init; }

    [JsonPropertyName("effort")]
    public string? Effort { get; init; }

    /// <summary>Genutzter Anteil des Kontextfensters in Prozent.</summary>
    [JsonPropertyName("ctx_pct")]
    public double? CtxPct { get; init; }

    [JsonPropertyName("msg_tokens")]
    public double? MsgTokens { get; init; }

    [JsonPropertyName("cost_usd")]
    public double? CostUsd { get; init; }

    /// <summary>Unix-Sekunden, wie Pythons <c>time.time()</c>.</summary>
    [JsonPropertyName("ts")]
    public double Ts { get; init; }

    /// <summary>
    /// ANDERS als beim Slot-JSON: <c>statusline.py</c> baut ein Dict mit ALLEN Keys und
    /// schreibt fehlende Werte als <c>null</c> mit. Diese Optionen halten das so – damit
    /// die Datei unabhängig davon lesbar bleibt, ob die Gegenseite auf Anwesenheit des
    /// Schlüssels oder auf seinen Wert prüft.
    /// </summary>
    internal static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = false,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        // KEIN DefaultIgnoreCondition: nulls werden mitgeschrieben.
    };
}
