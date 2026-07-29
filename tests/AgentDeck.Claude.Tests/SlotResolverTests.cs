namespace AgentDeck.Claude.Tests;

/// <summary>
/// Die Slot-Auflösung der Hooks. Der Grundsatz aus <c>hookstate.py</c> gilt hier
/// besonders: NIE werfen. Ein Hook, der mit Fehler endet, blockiert den Agenten – darum
/// prüft jeder Test auch den kaputten Fall.
/// </summary>
public class HookStateTests
{
    /// <summary>Wegwerf-Ordner, der die pidmap-Dateien der Extension nachstellt.</summary>
    private static string TempDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "agentdeck-hook-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }

    [Fact]
    public void LoadPidMap_merged_alle_Fensterdateien()
    {
        var dir = TempDir();
        try
        {
            // Je Fenster eine Datei; PIDs sind global eindeutig -> Union ist sicher.
            File.WriteAllText(Path.Combine(dir, "pidmap-A.json"), """{"1234":"A1","1235":"A2"}""");
            File.WriteAllText(Path.Combine(dir, "pidmap-B.json"), """{"5678":"B1"}""");
            // Nicht-pidmap-Dateien im State-Ordner dürfen nicht mitgelesen werden.
            File.WriteAllText(Path.Combine(dir, "A1.json"), """{"slot":"A1","status":"idle"}""");

            var map = SlotResolver.LoadPidMap(dir);

            Assert.Equal(3, map.Count);
            Assert.Equal("A1", map[1234]);
            Assert.Equal("A2", map[1235]);
            Assert.Equal("B1", map[5678]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void LoadPidMap_ueberspringt_kaputte_Dateien_einzeln()
    {
        var dir = TempDir();
        try
        {
            File.WriteAllText(Path.Combine(dir, "pidmap-A.json"), """{"1234":"A1""");   // halb geschrieben
            File.WriteAllText(Path.Combine(dir, "pidmap-B.json"), """{"5678":"B1"}""");  // intakt

            var map = SlotResolver.LoadPidMap(dir);

            // Die intakte Datei muss trotzdem greifen – sonst fällt beim Schreiben der
            // einen Datei die Statusmeldung ALLER Fenster aus.
            Assert.Single(map);
            Assert.Equal("B1", map[5678]);
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void LoadPidMap_ohne_Ordner_ist_leer_statt_Fehler()
    {
        var fehlt = Path.Combine(Path.GetTempPath(), "agentdeck-gibt-es-nicht-" + Guid.NewGuid());
        Assert.Empty(SlotResolver.LoadPidMap(fehlt));
    }

    [Fact]
    public void SlotFromProcs_ohne_pidmap_ist_null()
    {
        var dir = TempDir();
        try
        {
            Assert.Null(SlotResolver.SlotFromProcs(dir));
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void SlotFromProcs_findet_den_eigenen_Prozess_in_der_pidmap()
    {
        // Der von der Extension notierte Claude-PID ist immer ein VORFAHRE des Hooks.
        // Der eigene Prozess ist das erste Glied der Kette, also der einfachste Beweis,
        // dass Kette und pidmap zusammenfinden.
        var dir = TempDir();
        try
        {
            File.WriteAllText(Path.Combine(dir, "pidmap-A.json"),
                              $"{{\"{Environment.ProcessId}\":\"A7\"}}");

            Assert.Equal("A7", SlotResolver.SlotFromProcs(dir));
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void SlotFromProcs_bevorzugt_den_naechsten_Vorfahren()
    {
        // Stehen mehrere Glieder der Kette in der pidmap, gewinnt das NÄCHSTE (der
        // eigene Prozess vor dem Elternprozess) - wie die Reihenfolge in Python.
        var kette = SlotResolver.AncestorPids();
        Assert.True(kette.Count >= 2, "Prozesskette sollte mindestens Ich + Eltern haben");

        var dir = TempDir();
        try
        {
            File.WriteAllText(Path.Combine(dir, "pidmap-A.json"),
                              $"{{\"{kette[0]}\":\"NAH\",\"{kette[1]}\":\"FERN\"}}");

            Assert.Equal("NAH", SlotResolver.SlotFromProcs(dir));
        }
        finally { Directory.Delete(dir, recursive: true); }
    }

    [Fact]
    public void AncestorPids_beginnt_beim_eigenen_Prozess()
    {
        var kette = SlotResolver.AncestorPids();

        Assert.NotEmpty(kette);
        Assert.Equal(Environment.ProcessId, kette[0]);
        Assert.Distinct(kette);   // Zyklusschutz: kein PID doppelt
    }

    [Fact]
    public void ResolveSlot_nimmt_AGENT_SLOT_wenn_gesetzt()
    {
        var vorher = Environment.GetEnvironmentVariable("AGENT_SLOT");
        try
        {
            Environment.SetEnvironmentVariable("AGENT_SLOT", "C3");
            Assert.Equal("C3", SlotResolver.ResolveSlot());
        }
        finally
        {
            Environment.SetEnvironmentVariable("AGENT_SLOT", vorher);
        }
    }
}
