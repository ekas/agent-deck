using AgentDeck.Testing;

namespace AgentDeck.Core.Tests;

/// <summary>
/// Differenztests gegen die Python-Fassung: dieselben Eingaben, dasselbe Ergebnis.
/// Die Erwartungen stammen aus <c>tools/gen_golden.py</c> und sind damit nicht von mir
/// "passend" hingeschrieben, sondern von der Vorlage berechnet.
/// </summary>
public class GoldenCoreTests
{
    private static readonly IReadOnlySet<string> Glow =
        new HashSet<string> { "idle", "done", "thinking", "running", "waiting", "none" };

    private static readonly string[] Cycle = ["manual", "accept", "plan", "auto"];

    [Fact]
    public void StatusModel_verhaelt_sich_wie_status_model_py()
    {
        var diff = new Golden.Diff();

        foreach (var f in Golden.Load("status_model"))
        {
            var fn = f.Str("fn");
            switch (fn)
            {
                case "is_fresh":
                    {
                        var st = f.IsNull("st") ? null : new SlotState { Ts = f.GetProperty("st").Dbl("ts") };
                        var ist = StatusModel.IsFresh(st, f.Dbl("now"), f.Dbl("stale"));
                        diff.Check(ist == f.Bool("out"),
                                   $"is_fresh(ts={(st is null ? "null" : st.Ts)}, now={f.Dbl("now")}, stale={f.Dbl("stale")})",
                                   f.Bool("out"), ist);
                        break;
                    }
                case "normalize_status":
                    {
                        var ist = StatusModel.NormalizeStatus(f.Str("status"), f.Bool("fresh"), Glow);
                        diff.Check(ist == f.Str("out"),
                                   $"normalize_status({f.Str("status") ?? "null"}, fresh={f.Bool("fresh")})",
                                   f.Str("out"), ist);
                        break;
                    }
                case "is_lost":
                    {
                        var ist = StatusModel.IsLost(f.Str("status"), f.Bool("fresh"), f.Bool("connected"));
                        diff.Check(ist == f.Bool("out"),
                                   $"is_lost({f.Str("status")}, fresh={f.Bool("fresh")}, conn={f.Bool("connected")})",
                                   f.Bool("out"), ist);
                        break;
                    }
                case "dominant_status":
                    {
                        var keys = f.GetProperty("keys").EnumerateArray().Select(k => k.GetString()).ToList();
                        var ist = StatusModel.DominantStatus(keys);
                        diff.Check(ist == f.Str("out"),
                                   $"dominant_status([{string.Join(",", keys)}])", f.Str("out"), ist);
                        break;
                    }
                case "escalated":
                    {
                        var ist = StatusModel.Escalated(f.Str("prev"), f.Str("key"));
                        diff.Check(ist == f.Bool("out"),
                                   $"escalated({f.Str("prev")} -> {f.Str("key")})", f.Bool("out"), ist);
                        break;
                    }
                case "resolve_effort":
                    {
                        var rem = f.IsNull("remembered") ? null : f.Str("remembered");
                        var ist = StatusModel.ResolveEffort(f.Str("live"), rem);
                        var soll = f.IsNull("out") ? null : f.Str("out");
                        diff.Check(ist == soll,
                                   $"resolve_effort(live=\"{f.Str("live")}\", remembered={rem ?? "null"})",
                                   soll, ist);
                        break;
                    }
                case "mode_steps":
                    {
                        var ist = StatusModel.ModeSteps(f.IntOrNull("remembered"), f.Str("target")!,
                                                        Cycle, f.Str("start")!);
                        if (f.IsNull("out"))
                        {
                            diff.Check(ist is null,
                                       $"mode_steps({f.IntOrNull("remembered")?.ToString() ?? "null"}, {f.Str("target")}, start={f.Str("start")})",
                                       "null", ist?.ToString());
                        }
                        else
                        {
                            var soll = f.GetProperty("out").EnumerateArray().Select(x => x.GetInt32()).ToArray();
                            diff.Check(ist is not null && ist.Value.Steps == soll[0] && ist.Value.Target == soll[1],
                                       $"mode_steps({f.IntOrNull("remembered")?.ToString() ?? "null"}, {f.Str("target")}, start={f.Str("start")})",
                                       $"({soll[0]},{soll[1]})",
                                       ist is null ? "null" : $"({ist.Value.Steps},{ist.Value.Target})");
                        }
                        break;
                    }
                case "adopt_hook_mode":
                    {
                        var st = new SlotState { Mode = f.IsNull("mode") ? null : f.Str("mode"), Ts = f.Dbl("ts") };
                        var ist = StatusModel.AdoptHookMode(f.Dbl("prev_ts"), st, Cycle);
                        if (f.IsNull("out"))
                        {
                            diff.Check(ist is null,
                                       $"adopt_hook_mode(prev={f.Dbl("prev_ts")}, mode={f.Str("mode") ?? "null"}, ts={f.Dbl("ts")})",
                                       "null", ist?.ToString());
                        }
                        else
                        {
                            var soll = f.GetProperty("out").EnumerateArray().Select(x => x.GetDouble()).ToArray();
                            diff.Check(ist is not null && ist.Value.ModeIndex == (int)soll[0] && Math.Abs(ist.Value.Ts - soll[1]) < 1e-9,
                                       $"adopt_hook_mode(prev={f.Dbl("prev_ts")}, mode={f.Str("mode")}, ts={f.Dbl("ts")})",
                                       $"({soll[0]},{soll[1]})",
                                       ist is null ? "null" : $"({ist.Value.ModeIndex},{ist.Value.Ts})");
                        }
                        break;
                    }
                default:
                    Assert.Fail($"Unbekannte Funktion in der Golden-Datei: {fn}");
                    break;
            }
        }

        diff.Assert("StatusModel");
    }

    [Fact]
    public void ColorMath_mischt_wie_canvas_kit_py()
    {
        var diff = new Golden.Diff();

        foreach (var f in Golden.Load("color_mix"))
        {
            var ist = ColorMath.Mix(f.Str("c1")!, f.Str("c2")!, f.Dbl("t"));
            diff.Check(ist == f.Str("out"),
                       $"mix({f.Str("c1")}, {f.Str("c2")}, t={f.Dbl("t")})", f.Str("out"), ist);
        }

        diff.Assert("ColorMath.Mix");
    }

    [Fact]
    public void Spring_rechnet_wie_edge_dock_spring_at()
    {
        // Gleitkomma: die Formel ist identisch, aber die Auswertungsreihenfolge kann
        // im letzten Bit abweichen. 1e-9 ist weit unter jedem sichtbaren Pixel.
        const double Toleranz = 1e-9;
        var diff = new Golden.Diff();

        foreach (var f in Golden.Load("spring"))
        {
            var response = f.Dbl("response");
            var d0 = f.Dbl("d0");
            var v0 = f.Dbl("v0");
            var dt = f.Dbl("dt");

            var (d, v) = Spring.Step(d0, v0, Spring.OmegaFor(response), dt);

            diff.Check(Math.Abs(d - f.Dbl("d")) < Toleranz,
                       $"spring d (response={response}, d0={d0}, v0={v0}, dt={dt})",
                       f.Dbl("d"), d);
            diff.Check(Math.Abs(v - f.Dbl("v")) < Toleranz,
                       $"spring v (response={response}, d0={d0}, v0={v0}, dt={dt})",
                       f.Dbl("v"), v);
        }

        diff.Assert("Spring");
    }
}
