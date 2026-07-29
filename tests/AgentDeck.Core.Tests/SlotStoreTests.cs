namespace AgentDeck.Core.Tests;

/// <summary>
/// Der Refresh-Loop ohne Oberfläche: welche Slots gefunden werden und welcher
/// Anzeige-Status daraus folgt.
/// </summary>
public class SlotStoreTests
{
    private static string TempDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "agentdeck-store-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }

    private static void Write(string dir, string slot, string status, double ts) =>
        File.WriteAllText(Path.Combine(dir, slot + ".json"),
            $"{{\"slot\":\"{slot}\",\"status\":\"{status}\",\"ts\":{ts.ToString(System.Globalization.CultureInfo.InvariantCulture)},\"activity\":\"\"}}");

    [Fact]
    public void DiscoverSlots_ignoriert_Beiwerk_Dateien()
    {
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "idle", 100);
            Write(dir, "B2", "idle", 100);
            // Diese drei dürfen NICHT als Slot gelten:
            File.WriteAllText(Path.Combine(dir, "A1.live.json"), "{}");
            File.WriteAllText(Path.Combine(dir, "pidmap-A.json"), "{}");
            File.WriteAllText(Path.Combine(dir, "C3.json.tmp"), "{}");

            Assert.Equal(["A1", "B2"], SlotStore.DiscoverSlots(dir));
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void DiscoverSlots_ohne_Ordner_ist_leer()
    {
        Assert.Empty(SlotStore.DiscoverSlots(
            Path.Combine(Path.GetTempPath(), "agentdeck-nichts-" + Guid.NewGuid())));
    }

    [Fact]
    public void Refresh_normalisiert_eingeschlafene_Agenten_auf_idle()
    {
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "thinking", ts: 1000);    // frisch
            Write(dir, "A2", "thinking", ts: 0);       // seit Ewigkeiten still
            Write(dir, "A3", "waiting", ts: 0);        // waiting bleibt, auch alt

            var store = new SlotStore(staleSeconds: 900);
            store.Refresh(now: 1000, stateDir: dir);

            Assert.Equal("thinking", store.StatusKeys["A1"]);
            Assert.Equal("idle", store.StatusKeys["A2"]);
            Assert.Equal("waiting", store.StatusKeys["A3"]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void Refresh_ohne_Broker_Wissen_faerbt_nichts_rot()
    {
        // Das ist die Absicherung gegen "alles getrennt": ohne isConnected-Auskunft
        // darf kein Slot auf 'lost' laufen.
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "thinking", ts: 1000);

            var store = new SlotStore();
            store.Refresh(now: 1000, isConnected: null, stateDir: dir);

            Assert.Equal("thinking", store.StatusKeys["A1"]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void Refresh_erkennt_getrennt_wenn_das_Fenster_nicht_haengt()
    {
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "thinking", ts: 1000);

            var store = new SlotStore();
            store.Refresh(now: 1000, isConnected: _ => false, stateDir: dir);

            Assert.Equal("lost", store.StatusKeys["A1"]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void Refresh_ueberspringt_kaputte_Dateien()
    {
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "done", ts: 1000);
            File.WriteAllText(Path.Combine(dir, "A2.json"), """{"slot":"A2","stat""");   // halb

            var store = new SlotStore();
            store.Refresh(now: 1000, stateDir: dir);

            Assert.Single(store.StatusKeys);
            Assert.Equal("done", store.StatusKeys["A1"]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void DominantStatus_verdichtet_auf_das_Dringlichste()
    {
        var dir = TempDir();
        try
        {
            Write(dir, "A1", "idle", 1000);
            Write(dir, "A2", "thinking", 1000);
            Write(dir, "A3", "waiting", 1000);

            var store = new SlotStore();
            store.Refresh(now: 1000, stateDir: dir);

            Assert.Equal("waiting", store.DominantStatus());
        }
        finally { Directory.Delete(dir, recursive: true); }
    }
}

/// <summary>Die Statusstile – dieselbe Tabelle wie GLOW_STYLE in agent_deck.py.</summary>
public class StatusStyleTests
{
    [Fact]
    public void ValidStatus_entspricht_den_GLOW_STYLE_Schluesseln()
    {
        Assert.Equal(
            new[] { "done", "idle", "none", "running", "thinking", "waiting" },
            StatusStyle.ValidStatus.OrderBy(s => s));
    }

    [Fact]
    public void Thinking_und_Running_sehen_gleich_aus()
    {
        // Beides ist "denkt" - unterschiedliche Optik hätte bei jedem Wechsel geflackert.
        Assert.Equal(StatusStyle.For("thinking"), StatusStyle.For("running"));
    }

    [Fact]
    public void Nur_aktive_Zustaende_atmen()
    {
        Assert.True(StatusStyle.For("thinking").Breathes);
        Assert.True(StatusStyle.For("waiting").Breathes);
        Assert.False(StatusStyle.For("idle").Breathes);
        Assert.False(StatusStyle.For("done").Breathes);
        Assert.False(StatusStyle.For("none").Breathes);
    }

    [Fact]
    public void None_leuchtet_gar_nicht()
    {
        var none = StatusStyle.For("none");
        Assert.Equal(0.0, none.Glow);
        Assert.Equal(0.0, none.Tint);
    }

    [Fact]
    public void Lost_ist_rot_obwohl_es_kein_gemeldeter_Status_ist()
    {
        var lost = StatusStyle.For("lost");
        Assert.Equal("#ff6b6b", lost.Color);
        Assert.Equal(StatusStyle.LostFill, lost.Tint);
        // ... und taucht darum NICHT in den gültigen (gemeldeten) Status auf.
        Assert.DoesNotContain("lost", StatusStyle.ValidStatus);
    }

    [Fact]
    public void Unbekanntes_faellt_auf_idle_zurueck()
    {
        Assert.Equal(StatusStyle.For("idle"), StatusStyle.For("quatsch"));
        Assert.Equal(StatusStyle.For("idle"), StatusStyle.For(null));
    }
}
