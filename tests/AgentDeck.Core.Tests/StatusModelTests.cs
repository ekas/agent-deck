namespace AgentDeck.Core.Tests;

/// <summary>
/// Spiegelt die Python-Tests aus <c>tests/test_pure.py</c> (test_is_fresh …
/// test_mode_steps) Fall für Fall. Weicht hier etwas ab, ist der Port falsch – nicht
/// der Test.
/// </summary>
public class StatusModelTests
{
    /// <summary>Die Schlüssel von GLOW_STYLE (agent_deck.py) – gültige Status.</summary>
    private static readonly IReadOnlySet<string> Glow =
        new HashSet<string> { "idle", "done", "thinking", "running", "waiting", "none" };

    /// <summary>config.MODE_CYCLE.</summary>
    private static readonly string[] Cycle = ["manual", "accept", "plan", "auto"];

    private static SlotState At(double ts) => new() { Ts = ts };

    [Fact]
    public void IsFresh()
    {
        Assert.True(StatusModel.IsFresh(At(100), 100, 900));
        Assert.False(StatusModel.IsFresh(At(100), 1001, 900));
        Assert.False(StatusModel.IsFresh(null, 100, 900));
    }

    [Fact]
    public void NormalizeStatus()
    {
        Assert.Equal("idle", StatusModel.NormalizeStatus("thinking", false, Glow));   // eingeschlafen
        Assert.Equal("thinking", StatusModel.NormalizeStatus("thinking", true, Glow));
        Assert.Equal("idle", StatusModel.NormalizeStatus("running", false, Glow));
        Assert.Equal("waiting", StatusModel.NormalizeStatus("waiting", false, Glow)); // nicht thinking/running
        Assert.Equal("idle", StatusModel.NormalizeStatus("bogus", true, Glow));       // unbekannt
        Assert.Equal("none", StatusModel.NormalizeStatus("none", true, Glow));
    }

    [Fact]
    public void IsLost()
    {
        Assert.True(StatusModel.IsLost("thinking", true, false));
        Assert.False(StatusModel.IsLost("thinking", true, true));    // verbunden -> nicht lost
        Assert.False(StatusModel.IsLost("none", true, false));       // none nie lost
        Assert.False(StatusModel.IsLost("idle", false, false));      // nicht frisch -> nicht lost
    }

    [Fact]
    public void DominantStatus()
    {
        // Rangfolge für den Neon-Griff: Rückfrage > ungelesen > getrennt > denkt > idle.
        Assert.Equal("waiting", StatusModel.DominantStatus(["idle", "thinking", "done", "waiting"]));
        Assert.Equal("done", StatusModel.DominantStatus(["idle", "thinking", "done"]));
        Assert.Equal("lost", StatusModel.DominantStatus(["idle", "thinking", "lost"]));
        Assert.Equal("thinking", StatusModel.DominantStatus(["idle", "running"]));  // running == denkt
        Assert.Equal("idle", StatusModel.DominantStatus(["idle", "idle"]));
        Assert.Equal("none", StatusModel.DominantStatus([]));                       // keine Kachel
        Assert.Equal("none", StatusModel.DominantStatus(["none"]));
        Assert.Equal("none", StatusModel.DominantStatus(["bogus"]));                // Unbekanntes zählt nicht
    }

    [Fact]
    public void Escalated()
    {
        Assert.True(StatusModel.Escalated("idle", "waiting"));      // dringlicher -> Blitz
        Assert.True(StatusModel.Escalated("thinking", "done"));     // fertig geworden -> Blitz
        Assert.False(StatusModel.Escalated("done", "idle"));        // gelesen -> kein Blitz
        Assert.False(StatusModel.Escalated("waiting", "done"));     // ruhiger -> kein Blitz
        Assert.False(StatusModel.Escalated("done", "done"));        // kein Wechsel
        Assert.False(StatusModel.Escalated("thinking", "running")); // derselbe Zustand, gleicher Rang
        Assert.False(StatusModel.Escalated("running", "thinking")); // ...auch andersherum
    }

    [Fact]
    public void ResolveEffort()
    {
        Assert.Equal("ultracode", StatusModel.ResolveEffort("", "ultracode"));
        Assert.Equal("ultracode", StatusModel.ResolveEffort("xhigh", "ultracode")); // Kollision aufgelöst
        Assert.Equal("high", StatusModel.ResolveEffort("high", "ultracode"));       // echter Wert gewinnt
        Assert.Equal("xhigh", StatusModel.ResolveEffort("xhigh", null));
        Assert.Equal("", StatusModel.ResolveEffort("", null));
    }

    [Fact]
    public void AdoptHookMode()
    {
        Assert.Equal((2, 5.0), StatusModel.AdoptHookMode(0, new SlotState { Mode = "plan", Ts = 5 }, Cycle));
        Assert.Null(StatusModel.AdoptHookMode(9, new SlotState { Mode = "plan", Ts = 5 }, Cycle));  // älterer Event
        Assert.Null(StatusModel.AdoptHookMode(0, new SlotState { Mode = "bogus", Ts = 5 }, Cycle)); // ungültig
        Assert.Null(StatusModel.AdoptHookMode(0, new SlotState { Ts = 5 }, Cycle));                 // kein Modus
    }

    [Fact]
    public void ModeSteps()
    {
        // Unbekannter aktueller Modus (null) -> vom Start-Modus 'manual' (Index 0) aus rechnen.
        Assert.Equal((3, 3), StatusModel.ModeSteps(null, "auto", Cycle, "manual"));  // manual->accept->plan->auto
        Assert.Equal((2, 2), StatusModel.ModeSteps(null, "plan", Cycle, "manual"));
        Assert.Equal((0, 0), StatusModel.ModeSteps(null, "manual", Cycle, "manual")); // schon da -> 0 Schritte
        // Gemerkter aktueller Modus gewinnt: von 'plan' (2) nach 'auto' (3) = 1 Schritt.
        Assert.Equal((1, 3), StatusModel.ModeSteps(2, "auto", Cycle, "manual"));
        // Zyklisch: von 'auto' (3) zurück nach 'accept' (1) = (1-3) % 4 = 2 Schritte.
        // (In C# ist -2 % 4 == -2, nicht 2 – der Port muss das ausgleichen.)
        Assert.Equal((2, 1), StatusModel.ModeSteps(3, "accept", Cycle, "manual"));
        // Ungültiges Ziel -> null (Aufrufer schaltet nicht).
        Assert.Null(StatusModel.ModeSteps(null, "bogus", Cycle, "manual"));
        // Start-Modus nicht im Zyklus -> Fallback auf Index 0.
        Assert.Equal((2, 2), StatusModel.ModeSteps(null, "plan", Cycle, "weird"));
    }
}
