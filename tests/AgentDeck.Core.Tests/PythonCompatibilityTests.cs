using System.Text.RegularExpressions;

namespace AgentDeck.Core.Tests;

/// <summary>
/// Solange die Python- und die .NET-Fassung koexistieren, MÜSSEN sie dieselben Dateien
/// und dasselbe Wire-Format sprechen. Diese Tests lesen die Python-Quellen und
/// vergleichen sie gegen den Port – sie schlagen fehl, sobald eine Seite driftet.
/// Damit bleibt der schweigende Bruch aus: ein umbenanntes Kommando würde sonst nur
/// dazu führen, dass Kacheln stumm nichts mehr tun.
/// </summary>
public class PythonCompatibilityTests
{
    /// <summary>Repo-Wurzel finden: von der Test-Assembly aus nach oben, bis protocol.py auftaucht.</summary>
    private static string RepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "protocol.py")))
            dir = dir.Parent;
        Assert.NotNull(dir);   // ohne Repo-Wurzel ist der Test wertlos, nicht "grün"
        return dir!.FullName;
    }

    /// <summary>Die <c>NAME = "wert"</c>-Zuweisungen einer Python-Datei einsammeln.</summary>
    private static Dictionary<string, string> PythonConstants(string path)
    {
        var rx = new Regex("""^([A-Z_][A-Z0-9_]*)\s*=\s*["'](?<v>[^"']*)["']""",
                           RegexOptions.Multiline);
        return rx.Matches(File.ReadAllText(path))
                 .ToDictionary(m => m.Groups[1].Value, m => m.Groups["v"].Value);
    }

    [Fact]
    public void WireVokabular_stimmt_mit_protocol_py_ueberein()
    {
        var py = PythonConstants(Path.Combine(RepoRoot(), "protocol.py"));

        // Erwartete Paare: Python-Name -> C#-Wert
        var pairs = new (string PyName, string CsValue)[]
        {
            ("CMD_ASSIGN",       Protocol.CmdAssign),
            ("CMD_UNASSIGN",     Protocol.CmdUnassign),
            ("CMD_FOCUS_PANE",   Protocol.CmdFocusPane),
            ("CMD_SEND",         Protocol.CmdSend),
            ("CMD_KEY",          Protocol.CmdKey),
            ("CMD_CREATE_AGENT", Protocol.CmdCreateAgent),
            ("CMD_RELOAD",       Protocol.CmdReload),
            ("CMD_CLOSE_AGENT",  Protocol.CmdCloseAgent),
            ("CMD_CLOSE_WINDOW", Protocol.CmdCloseWindow),
            ("TYPE_HELLO",       Protocol.TypeHello),
            ("TYPE_TERMINALS",   Protocol.TypeTerminals),
            ("TYPE_SEEN",        Protocol.TypeSeen),
        };

        foreach (var (pyName, csValue) in pairs)
        {
            Assert.True(py.ContainsKey(pyName),
                        $"protocol.py kennt {pyName} nicht (mehr) – Port und Python driften.");
            Assert.Equal(py[pyName], csValue);
        }

        // Gegenprobe: protocol.py hat keine Kommandos/Typen, die der Port NICHT kennt.
        var unbekannt = py.Keys
            .Where(k => k.StartsWith("CMD_") || k.StartsWith("TYPE_"))
            .Except(pairs.Select(p => p.PyName))
            .ToList();
        Assert.Empty(unbekannt);
    }

    [Fact]
    public void BrokerPort_stimmt_mit_config_py_ueberein()
    {
        var config = File.ReadAllText(Path.Combine(RepoRoot(), "config.py"));
        var m = Regex.Match(config, @"^BROKER_PORT\s*=\s*(\d+)", RegexOptions.Multiline);
        Assert.True(m.Success, "BROKER_PORT steht nicht (mehr) in config.py");
        // config.py ist hier die Quelle der Wahrheit; die Reihenfolge folgt trotzdem der
        // xUnit-Konvention (Konstante als 'expected'), damit der Analyzer ruhig bleibt.
        Assert.Equal(Protocol.DefaultPort, int.Parse(m.Groups[1].Value));
    }

    [Fact]
    public void StateDir_zeigt_auf_denselben_Ordner_wie_deck_paths_py()
    {
        // deck_paths.py: LOCALAPPDATA (bzw. ~) + "claude-agent-deck" + "state"
        var erwartet = Path.Combine(
            Environment.GetEnvironmentVariable("LOCALAPPDATA")
                ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "claude-agent-deck", "state");
        Assert.Equal(erwartet, DeckPaths.StateDir);
    }

    [Fact]
    public void Slot_JSON_von_report_py_wird_vollstaendig_gelesen()
    {
        // Genau die Feldmenge, die report.py schreibt (inkl. float-ts und Umlauten).
        const string ausPython = """
            {"slot": "A1", "status": "thinking", "ts": 1785312045.8231234,
             "mode": "auto", "effort": "xhigh", "activity": "Bash: pytest -q",
             "session_id": "abc-123", "cwd": "C:\\Projekte\\agent-deck",
             "prompt": "Bitte prüfen, ob die Umlaute heil durchkommen: äöüß"}
            """;

        var st = System.Text.Json.JsonSerializer.Deserialize<SlotState>(ausPython);

        Assert.NotNull(st);
        Assert.Equal("A1", st!.Slot);
        Assert.Equal("thinking", st.Status);
        Assert.Equal(1785312045.8231234, st.Ts, precision: 6);
        Assert.Equal("auto", st.Mode);
        Assert.Equal("xhigh", st.Effort);
        Assert.Equal("Bash: pytest -q", st.Activity);
        Assert.Equal("abc-123", st.SessionId);
        Assert.Equal(@"C:\Projekte\agent-deck", st.Cwd);
        Assert.Contains("äöüß", st.Prompt);
    }

    [Fact]
    public void Geschriebenes_Slot_JSON_traegt_die_snake_case_Namen_von_report_py()
    {
        var dir = Path.Combine(Path.GetTempPath(), "agentdeck-test-" + Guid.NewGuid().ToString("N"));
        try
        {
            var path = Path.Combine(dir, "A1.json");
            DeckPaths.SaveJson(path, new SlotState
            {
                Slot = "A1",
                Status = "done",
                Ts = 1785312045.5,
                SessionId = "abc-123",
                Prompt = "Umlaut-Probe: äöüß",
            });

            var text = File.ReadAllText(path);

            // Die Python-Seite liest per Feldname – hier darf kein PascalCase entstehen.
            Assert.Contains("\"session_id\":", text);
            Assert.Contains("\"slot\":", text);
            Assert.DoesNotContain("SessionId", text);
            // Umlaute unescaped (Pythons json.dump mit ensure_ascii=False-Optik).
            Assert.Contains("äöüß", text);
            // Nulls gar nicht erst schreiben - report.py lässt fehlende Felder weg.
            Assert.DoesNotContain("\"mode\":", text);
            // Und das .tmp aus dem atomaren Schreiben darf nicht zurückbleiben.
            Assert.False(File.Exists(path + ".tmp"));
        }
        finally
        {
            if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public void LoadJson_liefert_null_statt_zu_werfen()
    {
        var dir = Path.Combine(Path.GetTempPath(), "agentdeck-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            // Fehlende Datei
            Assert.Null(DeckPaths.LoadJson<SlotState>(Path.Combine(dir, "fehlt.json")));

            // Halb geschriebene/kaputte Datei (der Leser arbeitet ohne Sperre)
            var kaputt = Path.Combine(dir, "halb.json");
            File.WriteAllText(kaputt, """{"slot": "A1", "status": "thin""");
            Assert.Null(DeckPaths.LoadJson<SlotState>(kaputt));
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }
}
