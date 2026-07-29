using System.Text.Json;
using AgentDeck.Testing;

namespace AgentDeck.Claude.Tests;

/// <summary>
/// Differenztests der Hook-Logik gegen <c>report.py</c> und <c>statusline.py</c>.
/// Die Erwartungen stammen aus <c>tools/gen_golden.py</c>.
/// </summary>
public class GoldenClaudeTests
{
    private static JsonElement Payload(JsonElement fall) => fall.GetProperty("payload");

    [Fact]
    public void ReportHook_verhaelt_sich_wie_report_py()
    {
        var diff = new Golden.Diff();

        foreach (var f in Golden.Load("report_hook"))
        {
            var p = Payload(f);
            var kurz = JsonSerializer.Serialize(p);

            var mode = ReportHook.ModeOf(p);
            diff.Check(mode == (f.IsNull("mode") ? null : f.Str("mode")),
                       $"_mode_of({kurz})", f.IsNull("mode") ? null : f.Str("mode"), mode);

            var effort = ReportHook.EffortOf(p);
            diff.Check(effort == (f.IsNull("effort") ? null : f.Str("effort")),
                       $"_effort_of({kurz})", f.IsNull("effort") ? null : f.Str("effort"), effort);

            var prompt = ReportHook.PromptOf(p);
            diff.Check(prompt == (f.IsNull("prompt") ? null : f.Str("prompt")),
                       $"_prompt_of({kurz})", f.IsNull("prompt") ? null : f.Str("prompt"), prompt);

            var act = ReportHook.ActivityOf(p);
            diff.Check(act == (f.IsNull("activity") ? null : f.Str("activity")),
                       $"_activity_of({kurz})", f.IsNull("activity") ? null : f.Str("activity"), act);

            var real = ReportHook.IsRealQuery(p);
            diff.Check(real == f.Bool("real_query"),
                       $"_is_real_query({kurz})", f.Bool("real_query"), real);
        }

        diff.Assert("ReportHook");
    }

    [Fact]
    public void StatusLine_verhaelt_sich_wie_statusline_py()
    {
        var diff = new Golden.Diff();

        foreach (var f in Golden.Load("statusline"))
        {
            var p = Payload(f);
            var kurz = JsonSerializer.Serialize(p);
            var soll = f.GetProperty("rec");

            var rec = StatusLineHook.Extract(p, now: 0);

            diff.Check(rec.Model == (soll.IsNull("model") ? null : soll.Str("model")),
                       $"_extract.model({kurz})", soll.IsNull("model") ? null : soll.Str("model"), rec.Model);

            diff.Check(rec.Effort == (soll.IsNull("effort") ? null : soll.Str("effort")),
                       $"_extract.effort({kurz})", soll.IsNull("effort") ? null : soll.Str("effort"), rec.Effort);

            diff.Check(Gleich(rec.CtxPct, soll.DblOrNull("ctx_pct")),
                       $"_extract.ctx_pct({kurz})", soll.DblOrNull("ctx_pct"), rec.CtxPct);

            diff.Check(Gleich(rec.MsgTokens, soll.DblOrNull("msg_tokens")),
                       $"_extract.msg_tokens({kurz})", soll.DblOrNull("msg_tokens"), rec.MsgTokens);

            diff.Check(Gleich(rec.CostUsd, soll.DblOrNull("cost_usd")),
                       $"_extract.cost_usd({kurz})", soll.DblOrNull("cost_usd"), rec.CostUsd);

            // Die Zeile ist der eigentliche Prüfstein: hier schlagen Kultur- und
            // Rundungsunterschiede durch (Komma statt Punkt, .5 auf/ab).
            var zeile = StatusLineHook.Line(rec);
            diff.Check(zeile == f.Str("line"), $"_line({kurz})", f.Str("line"), zeile);
        }

        diff.Assert("StatusLine");
    }

    private static bool Gleich(double? a, double? b) =>
        (a is null && b is null) || (a is not null && b is not null && Math.Abs(a.Value - b.Value) < 1e-9);
}
