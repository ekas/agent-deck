using System.Text.Json;
using AgentDeck.Core;

namespace AgentDeck.Claude.Tests;

/// <summary>
/// Die Fallunterscheidungen der Hooks. In der Python-Fassung stecken sie in privaten
/// Funktionen hinter <c>sys.argv</c>/stdin und sind darum ungetestet – hier sind sie es
/// nicht. Jeder Test entspricht einer Regel, die im Betrieb schon wehgetan hat.
/// </summary>
public class HookLogicTests
{
    private static JsonElement Json(string s) => JsonDocument.Parse(s).RootElement.Clone();

    // ── Notification: nur echte Rückfragen dürfen "wartet" setzen ────────
    [Theory]
    [InlineData("permission_prompt", true)]
    [InlineData("elicitation_dialog", true)]
    [InlineData("agent_needs_input", true)]
    [InlineData("idle_prompt", false)]        // Eingabe lag nur brach
    [InlineData("auth_success", false)]
    [InlineData("agent_completed", false)]
    [InlineData("elicitation_complete", false)]
    public void IsRealQuery_folgt_dem_notification_type(string typ, bool erwartet)
    {
        Assert.Equal(erwartet, ReportHook.IsRealQuery(Json($"{{\"notification_type\":\"{typ}\"}}")));
    }

    [Fact]
    public void IsRealQuery_faellt_ohne_Typ_auf_die_Meldung_zurueck()
    {
        // Ältere Claude-Code-Versionen liefern kein notification_type.
        Assert.True(ReportHook.IsRealQuery(Json("""{"message":"Claude needs your permission"}""")));
        Assert.True(ReportHook.IsRealQuery(Json("""{"message":"Allow Bash to run?"}""")));
        Assert.False(ReportHook.IsRealQuery(Json("""{"message":"Agent finished"}""")));
        Assert.False(ReportHook.IsRealQuery(Json("{}")));
    }

    [Fact]
    public void ShouldSkip_laesst_unechte_waiting_Meldungen_liegen()
    {
        // Genau der Fall, der eine fertige Kachel fälschlich auf "Rückfrage" kippen ließ.
        Assert.True(ReportHook.ShouldSkip("waiting", Json("""{"notification_type":"idle_prompt"}""")));
        Assert.False(ReportHook.ShouldSkip("waiting", Json("""{"notification_type":"permission_prompt"}""")));
        // Andere Status sind von der Regel nicht betroffen.
        Assert.False(ReportHook.ShouldSkip("done", Json("""{"notification_type":"idle_prompt"}""")));
    }

    [Theory]
    [InlineData("startup", false)]   // echter Start -> Kachel darf zurückgesetzt werden
    [InlineData("resume", true)]      // fortgesetzt -> grünes "ungelesen" erhalten
    [InlineData("clear", true)]
    [InlineData("compact", true)]
    public void ShouldSkip_setzt_nur_beim_echten_SessionStart_zurueck(string source, bool skip)
    {
        var data = Json($"{{\"hook_event_name\":\"SessionStart\",\"source\":\"{source}\"}}");
        Assert.Equal(skip, ReportHook.ShouldSkip("idle", data));
    }

    [Fact]
    public void ShouldSkip_behandelt_fehlendes_source_als_echten_Start()
    {
        // Ältere Claude-Version ohne `source`.
        Assert.False(ReportHook.ShouldSkip("idle", Json("""{"hook_event_name":"SessionStart"}""")));
    }

    // ── Modus / Effort ──────────────────────────────────────────────────
    [Theory]
    [InlineData("default", "manual")]
    [InlineData("acceptEdits", "accept")]     // Claude Code schreibt gemischt
    [InlineData("plan", "plan")]
    [InlineData("bypassPermissions", "bypass")]
    [InlineData("unbekannt", "unbekannt")]    // durchreichen, nicht verschlucken
    public void ModeOf_bildet_permission_mode_auf_MODE_CYCLE_Namen_ab(string pm, string erwartet)
    {
        Assert.Equal(erwartet, ReportHook.ModeOf(Json($"{{\"permission_mode\":\"{pm}\"}}")));
    }

    [Fact]
    public void EffortOf_nimmt_String_wie_Objekt()
    {
        Assert.Equal("high", ReportHook.EffortOf(Json("""{"effort":"high"}""")));
        Assert.Equal("xhigh", ReportHook.EffortOf(Json("""{"effort":{"level":"xhigh"}}""")));
        Assert.Null(ReportHook.EffortOf(Json("{}")));
    }

    // ── Aktivität ───────────────────────────────────────────────────────
    [Fact]
    public void ActivityOf_baut_Tool_plus_Detail()
    {
        Assert.Equal("Bash: npm test",
            ReportHook.ActivityOf(Json("""{"tool_name":"Bash","tool_input":{"command":"npm test"}}""")));
        // Ohne Detail nur der Tool-Name.
        Assert.Equal("Read", ReportHook.ActivityOf(Json("""{"tool_name":"Read"}""")));
        // Ohne Tool gar nichts (Unterschied zu "" - der Aufrufer behält dann den Vorwert).
        Assert.Null(ReportHook.ActivityOf(Json("{}")));
    }

    [Fact]
    public void ActivityOf_nimmt_nur_die_erste_Zeile_und_kuerzt()
    {
        var mehrzeilig = ReportHook.ActivityOf(
            Json("""{"tool_name":"Bash","tool_input":{"command":"erste Zeile\nzweite Zeile"}}"""));
        Assert.Equal("Bash: erste Zeile", mehrzeilig);

        var lang = ReportHook.ActivityOf(Json(
            """{"tool_name":"Bash","tool_input":{"command":"0123456789012345678901234567890123456789012345"}}"""));
        Assert.NotNull(lang);
        Assert.EndsWith("…", lang);
        Assert.Equal("Bash: ".Length + 42 + 1, lang!.Length);   // Präfix + 42 Zeichen + Ellipse
    }

    // ── Prompt ──────────────────────────────────────────────────────────
    [Fact]
    public void PromptOf_trimmt_und_kuerzt_auf_500()
    {
        Assert.Equal("Was ist kaputt?", ReportHook.PromptOf(Json("""{"prompt":"  Was ist kaputt?  "}""")));
        Assert.Null(ReportHook.PromptOf(Json("""{"prompt":"   "}""")));
        Assert.Null(ReportHook.PromptOf(Json("{}")));

        var lang = new string('x', 600);
        var gekuerzt = ReportHook.PromptOf(Json($"{{\"prompt\":\"{lang}\"}}"));
        Assert.NotNull(gekuerzt);
        Assert.Equal(501, gekuerzt!.Length);      // 500 + Ellipse
        Assert.EndsWith("…", gekuerzt);
    }

    [Fact]
    public void PromptOf_zerschneidet_kein_Surrogatpaar()
    {
        // 500 Zeichen, sodass genau an einer Emoji-Grenze geschnitten wird: ein halbes
        // Surrogatpaar wäre ungültiges UTF-16 und würde die JSON-Datei beschädigen.
        var prompt = new string('x', 499) + "😀" + new string('y', 100);
        var gekuerzt = ReportHook.PromptOf(Json(JsonSerializer.Serialize(new { prompt })));

        Assert.NotNull(gekuerzt);
        Assert.False(char.IsHighSurrogate(gekuerzt![^2]),
                     "am Ende steht ein einzelnes High-Surrogate -> kaputtes Zeichen");
        // Muss sich fehlerfrei nach UTF-8 und zurück übersetzen lassen.
        var bytes = System.Text.Encoding.UTF8.GetBytes(gekuerzt);
        Assert.Equal(gekuerzt, System.Text.Encoding.UTF8.GetString(bytes));
    }

    // ── Merge: was erhalten bleibt und was überschrieben wird ───────────
    [Fact]
    public void Merge_erhaelt_Felder_die_das_Event_nicht_liefert()
    {
        var prev = new SlotState
        {
            Slot = "A1",
            Status = "thinking",
            Ts = 100,
            Mode = "auto",
            Effort = "high",
            SessionId = "sid-1",
            Prompt = "Die alte Frage",
            Cwd = @"C:\repo",
            Activity = "Read: x.cs",
        };

        // PostToolUse liefert weder mode/effort noch prompt/session_id.
        var neu = ReportHook.Merge("A1", "thinking",
            Json("""{"hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}"""),
            prev, now: 200, cwd: @"C:\repo");

        Assert.Equal("auto", neu.Mode);
        Assert.Equal("high", neu.Effort);
        Assert.Equal("sid-1", neu.SessionId);
        Assert.Equal("Die alte Frage", neu.Prompt);      // Tooltip behält die Frage
        Assert.Equal("Bash: ls", neu.Activity);          // Aktivität wird ersetzt
        Assert.Equal(200, neu.Ts);
    }

    [Fact]
    public void Merge_leert_die_Aktivitaet_bei_Stop_und_done()
    {
        var prev = new SlotState { Activity = "Bash: npm test" };

        Assert.Equal("", ReportHook.Merge("A1", "done", Json("{}"), prev, 200, null).Activity);
        Assert.Equal("", ReportHook.Merge("A1", "thinking",
            Json("""{"hook_event_name":"Stop"}"""), prev, 200, null).Activity);
    }

    [Fact]
    public void Merge_ohne_Vorzustand_funktioniert()
    {
        var neu = ReportHook.Merge("B2", "idle", Json("{}"), prev: null, now: 42, cwd: null);

        Assert.Equal("B2", neu.Slot);
        Assert.Equal("idle", neu.Status);
        Assert.Equal(42, neu.Ts);
        Assert.Equal("", neu.Activity);
        Assert.Null(neu.Mode);
        Assert.Null(neu.Prompt);
    }

    // ── statusLine ──────────────────────────────────────────────────────
    [Fact]
    public void Extract_liest_die_Live_Werte()
    {
        var rec = StatusLineHook.Extract(Json("""
            {"model":{"display_name":"Opus 5"},"effort":{"level":"xhigh"},
             "context_window":{"used_percentage":42.7,
                               "current_usage":{"input_tokens":1200,"output_tokens":300}},
             "cost":{"total_cost_usd":0.1534}}
            """), now: 7);

        Assert.Equal("Opus 5", rec.Model);
        Assert.Equal("xhigh", rec.Effort);
        Assert.Equal(42.7, rec.CtxPct);
        Assert.Equal(1500, rec.MsgTokens);
        Assert.Equal(0.1534, rec.CostUsd);
        Assert.Equal(7, rec.Ts);
    }

    [Fact]
    public void Extract_nimmt_Modell_auch_als_String_und_faellt_auf_id_zurueck()
    {
        Assert.Equal("claude-opus-5", StatusLineHook.Extract(Json("""{"model":"claude-opus-5"}"""), 0).Model);
        Assert.Equal("claude-opus-5", StatusLineHook.Extract(Json("""{"model":{"id":"claude-opus-5"}}"""), 0).Model);
    }

    [Fact]
    public void MessageTokens_kommt_mit_wechselnden_Feldnamen_zurecht()
    {
        Assert.Equal(1500, StatusLineHook.MessageTokens(
            Json("""{"current_usage":{"input_tokens":1200,"output_tokens":300}}""")));
        // Kurzform
        Assert.Equal(1500, StatusLineHook.MessageTokens(
            Json("""{"current_usage":{"input":1200,"output":300}}""")));
        // Nur eine Richtung vorhanden
        Assert.Equal(1200, StatusLineHook.MessageTokens(
            Json("""{"current_usage":{"input_tokens":1200}}""")));
        // Gar keine current_usage -> Rückfall
        Assert.Equal(900, StatusLineHook.MessageTokens(Json("""{"total_output_tokens":900}""")));
        Assert.Null(StatusLineHook.MessageTokens(Json("{}")));
    }

    [Fact]
    public void Line_schreibt_Zahlen_unabhaengig_von_der_Systemsprache()
    {
        // Auf einem deutschen System würde ohne InvariantCulture "$0,15" herauskommen.
        var vorher = Thread.CurrentThread.CurrentCulture;
        try
        {
            Thread.CurrentThread.CurrentCulture = new System.Globalization.CultureInfo("de-DE");

            var zeile = StatusLineHook.Line(new LiveState
            {
                Model = "Opus 5",
                Effort = "xhigh",
                CtxPct = 42.7,
                CostUsd = 0.1534,
            });

            Assert.Equal("Opus 5  ·  effort xhigh  ·  ctx 43%  ·  $0.15", zeile);
        }
        finally
        {
            Thread.CurrentThread.CurrentCulture = vorher;
        }
    }

    [Fact]
    public void Line_laesst_fehlende_Werte_einfach_weg()
    {
        Assert.Equal("", StatusLineHook.Line(new LiveState()));
        Assert.Equal("Opus 5", StatusLineHook.Line(new LiveState { Model = "Opus 5" }));
    }
}
