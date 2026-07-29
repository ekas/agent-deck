using System.Globalization;
using System.Text.Json;

using AgentDeck.Core;

namespace AgentDeck.Claude;

/// <summary>
/// Logik der statusLine (<c>statusline.py</c>): die Live-Werte aus dem Payload ziehen und
/// die Terminal-Zeile bauen. IO macht der Aufrufer.
/// </summary>
public static class StatusLineHook
{
    /// <summary>
    /// Tokens der aktuellen Nachricht (best effort über wechselnde Feldnamen).
    /// </summary>
    public static double? MessageTokens(JsonElement contextWindow)
    {
        var cur = contextWindow.Obj("current_usage");
        var i = cur.FirstNum("input_tokens", "input");
        var o = cur.FirstNum("output_tokens", "output");
        if (i is not null || o is not null)
            return (i ?? 0) + (o ?? 0);
        return contextWindow.Num("total_output_tokens");
    }

    public static LiveState Extract(JsonElement data, double now)
    {
        var cw = data.Obj("context_window");
        var cost = data.Obj("cost");

        return new LiveState
        {
            Model = data.StrOrNested("model", "display_name", "id"),
            Effort = data.StrOrNested("effort", "level"),
            CtxPct = cw.Num("used_percentage"),
            MsgTokens = MessageTokens(cw),
            CostUsd = cost.Num("total_cost_usd"),
            Ts = now,
        };
    }

    /// <summary>
    /// Kompakte Statuszeile fürs Terminal. Zahlen BEWUSST mit
    /// <see cref="CultureInfo.InvariantCulture"/>: auf einem deutschen System würde
    /// <c>$0,15</c> statt <c>$0.15</c> erscheinen.
    /// </summary>
    public static string Line(LiveState rec)
    {
        var parts = new List<string>();

        if (!string.IsNullOrEmpty(rec.Model))
            parts.Add(rec.Model);
        if (!string.IsNullOrEmpty(rec.Effort))
            parts.Add($"effort {rec.Effort}");
        if (rec.CtxPct is { } pct)
            parts.Add($"ctx {Math.Round(pct).ToString("0", CultureInfo.InvariantCulture)}%");
        if (rec.CostUsd is { } cost)
            parts.Add("$" + cost.ToString("F2", CultureInfo.InvariantCulture));

        return string.Join("  ·  ", parts);
    }

    /// <summary>Pfad der Live-Datei eines Slots (<c>&lt;slot&gt;.live.json</c>).</summary>
    public static string LivePath(string slot) =>
        Path.Combine(DeckPaths.StateDir, slot + ".live.json");

    /// <summary>
    /// Live-Werte atomar schreiben. Eigene Methode, weil das JSON hier – anders als beim
    /// Slot-Zustand – auch <c>null</c>-Felder enthalten muss (siehe
    /// <see cref="LiveState.WriteOptions"/>).
    /// </summary>
    public static void SaveLive(string slot, LiveState rec)
    {
        var path = LivePath(slot);
        Directory.CreateDirectory(DeckPaths.StateDir);
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(rec, LiveState.WriteOptions),
                          new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        File.Move(tmp, path, overwrite: true);
    }
}
