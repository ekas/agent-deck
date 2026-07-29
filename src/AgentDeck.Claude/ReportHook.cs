using System.Text.Json;

using AgentDeck.Core;

namespace AgentDeck.Claude;

/// <summary>
/// Logik des Status-Hooks (<c>report.py</c>), bewusst frei von IO: der Aufrufer liest
/// stdin und schreibt die Datei, hier wird nur entschieden. Damit ist die knifflige
/// Fallunterscheidung (welches Event darf welchen Zustand überschreiben) testbar, was
/// sie in der Python-Fassung nicht ist.
/// </summary>
public static class ReportHook
{
    /// <summary>
    /// Claude-Code-<c>permission_mode</c> -> Namen wie in <c>config.MODE_CYCLE</c>.
    /// </summary>
    private static readonly Dictionary<string, string> ModeMap = new()
    {
        ["default"] = "manual",
        ["acceptedits"] = "accept",
        ["plan"] = "plan",
        ["auto"] = "auto",
        ["bypasspermissions"] = "bypass",
        ["dontask"] = "dontask",
    };

    /// <summary>
    /// Notification-Typen, die WIRKLICH eine Entscheidung verlangen -> "Rückfrage".
    /// Der Notification-Hook feuert für viele Fälle (idle_prompt, auth_success,
    /// agent_completed …) und liefert den Fall in <c>notification_type</c>.
    /// </summary>
    private static readonly HashSet<string> WaitNotify =
        ["permission_prompt", "elicitation_dialog", "agent_needs_input"];

    public static string? ModeOf(JsonElement data)
    {
        var pm = data.StrOrNested("permission_mode", "mode");
        if (string.IsNullOrEmpty(pm))
            return null;
        var low = pm.ToLowerInvariant();
        return ModeMap.GetValueOrDefault(low, low);
    }

    public static string? EffortOf(JsonElement data) => data.StrOrNested("effort", "level");

    /// <summary>
    /// Zuletzt abgeschickte Frage – nur der <c>UserPromptSubmit</c>-Event liefert sie.
    /// Getrimmt und auf 500 Zeichen gekürzt, damit State-Datei und Tooltip klein bleiben.
    /// </summary>
    public static string? PromptOf(JsonElement data)
    {
        var p = data.Str("prompt")?.Trim();
        if (string.IsNullOrEmpty(p))
            return null;
        return p.Length > 500 ? Truncate(p, 500).TrimEnd() + "…" : p;
    }

    /// <summary>Kurzbeschreibung des genutzten Tools (nur bei Pre-/PostToolUse gesetzt).</summary>
    public static string? ActivityOf(JsonElement data)
    {
        var tn = data.Str("tool_name");
        if (string.IsNullOrEmpty(tn))
            return null;

        var ti = data.Obj("tool_input");
        var detail = ti.Str("command") ?? ti.Str("file_path") ?? ti.Str("path")
                     ?? ti.Str("pattern") ?? ti.Str("url") ?? ti.Str("description") ?? "";

        // Nur die erste Zeile, damit ein mehrzeiliges Kommando die Kachel nicht sprengt.
        var firstLine = detail.ReplaceLineEndings("\n").Split('\n')[0].Trim();
        if (firstLine.Length > 42)
            firstLine = Truncate(firstLine, 42) + "…";

        return firstLine.Length > 0 ? $"{tn}: {firstLine}" : tn;
    }

    /// <summary>
    /// Ist ein Notification-Event eine echte Rückfrage? Bevorzugt das dokumentierte
    /// <c>notification_type</c>; fehlt es (ältere Claude-Code-Version), Rückfall auf die
    /// Permission-Meldung. So kippt eine fertige/idle Kachel nicht mehr fälschlich auf
    /// "wartet", bloß weil die Eingabe kurz brach lag.
    /// </summary>
    public static bool IsRealQuery(JsonElement data)
    {
        if (data.Str("notification_type") is { Length: > 0 } nt)
            return WaitNotify.Contains(nt);

        var low = (data.Str("message") ?? "").ToLowerInvariant();
        return low.Contains("allow") || low.Contains("permission");
    }

    /// <summary>
    /// Soll dieser Hook-Aufruf den Zustand UNANGETASTET lassen? Zwei Fälle:
    /// <list type="bullet">
    /// <item>Der Notification-Hook meldet stur "waiting", feuert aber für mehrere Fälle –
    ///   ist es keine echte Rückfrage, behält die Kachel done/idle.</item>
    /// <item><c>SessionStart</c> feuert auch bei resume/clear/compact. Nur der echte Start
    ///   darf die Kachel zurücksetzen, sonst geht ein grünes "ungelesen" verloren. Fehlt
    ///   <c>source</c> (ältere Version), gilt es als echter Start.</item>
    /// </list>
    /// </summary>
    public static bool ShouldSkip(string status, JsonElement data)
    {
        if (status == "waiting" && !IsRealQuery(data))
            return true;

        if (data.Str("hook_event_name") == "SessionStart")
        {
            var source = data.Str("source");
            if (!string.IsNullOrEmpty(source) && source != "startup")
                return true;
        }
        return false;
    }

    /// <summary>
    /// Den neuen Slot-Zustand aus Event und Vorzustand bilden. Felder, die nur manche
    /// Events liefern, werden ERHALTEN statt überschrieben – sonst verlöre der Tooltip
    /// bei jedem Tool-Aufruf die zuletzt gestellte Frage.
    /// </summary>
    public static SlotState Merge(
        string slot, string status, JsonElement data, SlotState? prev, double now, string? cwd)
    {
        var ev = data.Str("hook_event_name");

        // Aktivität: bei Tool-Nutzung setzen, bei Stop/done leeren, sonst beibehalten.
        var act = ActivityOf(data);
        if (ev == "Stop" || status == "done")
            act = "";
        else
            act ??= prev?.Activity ?? "";

        return new SlotState
        {
            Slot = slot,
            Status = status,
            Ts = now,
            Mode = Coalesce(ModeOf(data), prev?.Mode),
            Effort = Coalesce(EffortOf(data), prev?.Effort),
            Activity = act,
            SessionId = Coalesce(data.Str("session_id"), prev?.SessionId),
            Cwd = Coalesce(cwd, prev?.Cwd),
            Prompt = Coalesce(PromptOf(data), prev?.Prompt),
        };
    }

    /// <summary>Wie Pythons <c>a or b</c> für Zeichenketten: leer zählt als nicht gesetzt.</summary>
    private static string? Coalesce(string? a, string? b) =>
        !string.IsNullOrEmpty(a) ? a : (!string.IsNullOrEmpty(b) ? b : null);

    /// <summary>
    /// Auf <paramref name="max"/> Zeichen kürzen, ohne ein Surrogatpaar zu zerschneiden –
    /// ein halbes Emoji wäre ungültiges UTF-16 und würde beim Schreiben die JSON-Datei
    /// beschädigen.
    /// </summary>
    private static string Truncate(string s, int max)
    {
        if (s.Length <= max)
            return s;
        var cut = max;
        if (char.IsHighSurrogate(s[cut - 1]))
            cut--;
        return s[..cut];
    }
}
