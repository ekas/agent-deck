namespace AgentDeck.Core.Tests;

/// <summary>
/// Die Feder. Getestet werden die Eigenschaften, auf die sich das Andocken verlässt:
/// kein Überschwingen, front-loaded, und bei jedem noch so großen Zeitschritt stabil.
/// </summary>
public class SpringTests
{
    /// <summary>Feder von 0 nach 1 laufen lassen und die Bahn aufzeichnen.</summary>
    private static List<(double Ms, double Pos)> Run(
        double responseMs, double stepMs = 5, double maxMs = 2000, double spanPx = 300)
    {
        var s = new Spring(0.0);
        s.SetTarget(1.0, responseMs);
        var bahn = new List<(double, double)> { (0, s.Position) };

        for (double t = stepMs; t <= maxMs; t += stepMs)
        {
            var fertig = s.Advance(stepMs / 1000.0, spanPx);
            bahn.Add((t, s.Position));
            if (fertig)
                break;
        }
        return bahn;
    }

    [Fact]
    public void Schwingt_nicht_ueber()
    {
        // Das ist der Grund für die kritische Dämpfung: ein am Rand verankertes Panel
        // darf nicht über die Kante hinaus- oder von ihr wegschwingen.
        foreach (var (_, pos) in Run(Spring.RevealResponseMs))
            Assert.InRange(pos, 0.0, 1.0);
    }

    [Fact]
    public void Ist_front_loaded()
    {
        // Aus edge_dock.py: nach 80 ms rund 74 % (smoothstep hätte 46 %),
        // nach 120 ms rund 90 %.
        var bahn = Run(Spring.RevealResponseMs, stepMs: 1);

        var bei80 = bahn.First(p => p.Ms >= 80).Pos;
        var bei120 = bahn.First(p => p.Ms >= 120).Pos;

        Assert.InRange(bei80, 0.68, 0.80);
        Assert.InRange(bei120, 0.86, 0.95);
    }

    [Fact]
    public void Kommt_an_und_setzt_sich_fest()
    {
        var s = new Spring(0.0);
        s.SetTarget(1.0, Spring.RevealResponseMs);

        var fertig = false;
        for (var i = 0; i < 400 && !fertig; i++)
            fertig = s.Advance(0.005, spanPx: 300);

        Assert.True(fertig);
        Assert.Equal(1.0, s.Position);
        Assert.Equal(0.0, s.Velocity);
    }

    [Fact]
    public void Bleibt_bei_absurd_grossem_Zeitschritt_stabil()
    {
        // Genau der Fall, für den die geschlossene Form da ist: ausgefallene Frames,
        // Standby. Eine Schritt-für-Schritt-Integration würde hier explodieren.
        var s = new Spring(0.0);
        s.SetTarget(1.0, Spring.RevealResponseMs);

        var fertig = s.Advance(dtSeconds: 30.0, spanPx: 300);

        Assert.True(fertig);
        Assert.Equal(1.0, s.Position);
    }

    [Fact]
    public void Richtungswechsel_behaelt_die_Geschwindigkeit_bei()
    {
        // Beim Umkehren wird NUR das Ziel getauscht - sonst würde die Bewegung springen.
        var s = new Spring(0.0);
        s.SetTarget(1.0, Spring.RevealResponseMs);
        s.Advance(0.03, 300);

        var vVorher = s.Velocity;
        Assert.True(vVorher > 0);

        s.SetTarget(0.0, Spring.CollapseResponseMs);
        Assert.Equal(vVorher, s.Velocity);      // Geschwindigkeit läuft weiter
        Assert.Equal(0.0, s.Target);
    }

    [Fact]
    public void Einklappen_ist_zuegiger_als_Aufklappen()
    {
        var auf = Run(Spring.RevealResponseMs, stepMs: 1);
        var zu = Run(Spring.CollapseResponseMs, stepMs: 1);

        Assert.True(zu.Last().Ms < auf.Last().Ms,
                    "Wegräumen soll nicht länger dauern als Hervorholen");
    }

    [Fact]
    public void SnapToTarget_ist_die_Notbremse()
    {
        var s = new Spring(0.0);
        s.SetTarget(1.0, Spring.RevealResponseMs);
        s.Advance(0.01, 300);

        s.SnapToTarget();

        Assert.Equal(1.0, s.Position);
        Assert.Equal(0.0, s.Velocity);
    }
}

/// <summary>
/// Wo Panel und Griff landen. "Halb ausgefahren" ist der eine Zustand, den es nicht
/// geben darf – darum sind die Endlagen hier festgenagelt.
/// </summary>
public class DockGeometryTests
{
    private static readonly DockRect Work = new(0, 0, 1920, 1040);   // typischer Arbeitsbereich

    [Fact]
    public void Links_ausgefahren_klebt_am_Rand_mit_Spalt()
    {
        var r = DockGeometry.PanelRect(DockEdge.Left, Work, width: 260, height: 520,
                                       along: 200, progress: 1.0);
        // EDGE_GAP, nicht bündig: Windows 11 malt seinen Rand sonst über die
        // äußerste Pixelreihe.
        Assert.Equal(Work.X + DockGeometry.EdgeGap, r.X);
        Assert.Equal(200, r.Y);
    }

    [Fact]
    public void Links_versteckt_liegt_vollstaendig_jenseits_der_Kante()
    {
        var r = DockGeometry.PanelRect(DockEdge.Left, Work, 260, 520, 200, progress: 0.0);
        Assert.Equal(Work.X - 260, r.X);
        Assert.True(r.Right <= Work.X, "ein Rest des Panels ragt noch in den Schirm");
    }

    [Fact]
    public void Rechts_versteckt_liegt_vollstaendig_jenseits_der_Kante()
    {
        var r = DockGeometry.PanelRect(DockEdge.Right, Work, 260, 520, 200, progress: 0.0);
        Assert.True(r.X >= Work.Right, "ein Rest des Panels ragt noch in den Schirm");
    }

    [Fact]
    public void Oben_bewegt_sich_senkrecht()
    {
        var aus = DockGeometry.PanelRect(DockEdge.Top, Work, 260, 520, 400, 1.0);
        var zu = DockGeometry.PanelRect(DockEdge.Top, Work, 260, 520, 400, 0.0);

        Assert.Equal(400, aus.X);
        Assert.Equal(aus.X, zu.X);                 // quer zum Rand passiert nichts
        Assert.Equal(Work.Y + DockGeometry.EdgeGap, aus.Y);
        Assert.True(zu.Bottom <= Work.Y);
    }

    [Fact]
    public void Der_Weg_dazwischen_ist_stetig_und_monoton()
    {
        double? vorher = null;
        for (var p = 0.0; p <= 1.0; p += 0.05)
        {
            var x = DockGeometry.PanelRect(DockEdge.Left, Work, 260, 520, 200, p).X;
            if (vorher is not null)
                Assert.True(x > vorher, "das Panel muss durchgehend nach innen wandern");
            vorher = x;
        }
    }

    [Fact]
    public void Position_entlang_des_Rands_bleibt_im_Arbeitsbereich()
    {
        // Weit über den unteren Rand hinaus gezogen -> muss zurückgeholt werden,
        // sonst ist der Griff nicht mehr erreichbar.
        var r = DockGeometry.PanelRect(DockEdge.Left, Work, 260, 520, along: 5000, progress: 1.0);
        Assert.True(r.Bottom <= Work.Bottom);

        var oben = DockGeometry.PanelRect(DockEdge.Left, Work, 260, 520, along: -500, progress: 1.0);
        Assert.True(oben.Y >= Work.Y);
    }

    [Fact]
    public void Griff_sitzt_am_Rand_und_haelt_seine_Laengengrenzen()
    {
        var kurz = DockGeometry.HandleRect(DockEdge.Left, Work, 100, panelLength: 60);
        var lang = DockGeometry.HandleRect(DockEdge.Left, Work, 100, panelLength: 2000);

        Assert.Equal(DockGeometry.HandleMinLen, kurz.Height);
        Assert.Equal(DockGeometry.HandleMaxLen, lang.Height);
        Assert.Equal(DockGeometry.HandleThick, kurz.Width);
        Assert.Equal(Work.X + DockGeometry.EdgeGap, kurz.X);
    }

    [Fact]
    public void Griff_rechts_haengt_an_der_rechten_Kante()
    {
        var r = DockGeometry.HandleRect(DockEdge.Right, Work, 100, 400);
        Assert.Equal(Work.Right - DockGeometry.HandleThick - DockGeometry.EdgeGap, r.X);
    }

    [Fact]
    public void PointerInside_hat_Kulanz_am_Rand()
    {
        var panel = new DockRect(100, 100, 200, 400);

        Assert.True(DockGeometry.PointerInside(panel, 150, 200));
        Assert.True(DockGeometry.PointerInside(panel, 95, 200));      // knapp daneben zählt noch
        Assert.False(DockGeometry.PointerInside(panel, 50, 200));     // deutlich daneben nicht
    }
}

/// <summary>Die selbst geführte Kachel-Reihenfolge.</summary>
public class SlotOrderTests
{
    [Fact]
    public void Ohne_Merker_wird_natuerlich_sortiert()
    {
        var order = new SlotOrder();
        Assert.Equal(["A1", "A2", "B1"], order.Apply(["B1", "A2", "A1"]));
    }

    [Fact]
    public void Gemerkte_Reihenfolge_gewinnt()
    {
        var order = new SlotOrder();
        order.Remember(["B2", "B1", "B3"]);

        Assert.Equal(["B2", "B1", "B3"], order.Apply(["B1", "B2", "B3"]));
    }

    [Fact]
    public void Neue_Slots_haengen_hinten_an()
    {
        // Wichtig fürs Gefühl: ein neu angelegter Agent darf die Ordnung der
        // bestehenden nicht durcheinanderwerfen.
        var order = new SlotOrder();
        order.Remember(["B2", "B1"]);

        Assert.Equal(["B2", "B1", "B3"], order.Apply(["B1", "B2", "B3"]));
    }

    [Fact]
    public void Verschwundene_Slots_fallen_still_heraus()
    {
        var order = new SlotOrder();
        order.Remember(["B1", "B2", "B3"]);

        Assert.Equal(["B1", "B3"], order.Apply(["B1", "B3"]));
    }

    [Fact]
    public void Fenster_werden_getrennt_gehalten()
    {
        var order = new SlotOrder();
        order.Remember(["A2", "A1", "B2", "B1"]);

        var ergebnis = order.Apply(["A1", "A2", "B1", "B2"]);

        Assert.Equal(["A2", "A1", "B2", "B1"], ergebnis);
    }

    [Fact]
    public void Move_zieht_eine_Kachel_an_die_Zielstelle()
    {
        Assert.Equal(["B2", "B1", "B3"], SlotOrder.Move(["B1", "B2", "B3"], "B2", "B1"));
        Assert.Equal(["B1", "B3", "B2"], SlotOrder.Move(["B1", "B2", "B3"], "B2", "B3"));
        // Unbekanntes oder Ziel = Quelle: unverändert
        Assert.Equal(["B1", "B2"], SlotOrder.Move(["B1", "B2"], "B1", "B1"));
        Assert.Equal(["B1", "B2"], SlotOrder.Move(["B1", "B2"], "X9", "B1"));
    }

    [Fact]
    public void Reihenfolge_ueberlebt_das_Speichern()
    {
        var datei = Path.Combine(Path.GetTempPath(), "slot-order-" + Guid.NewGuid().ToString("N") + ".json");
        try
        {
            var a = new SlotOrder();
            a.Remember(["B3", "B1", "B2"]);
            a.Save(datei);

            var b = SlotOrder.Load(datei);
            Assert.Equal(["B3", "B1", "B2"], b.Apply(["B1", "B2", "B3"]));
        }
        finally { File.Delete(datei); }
    }

    [Fact]
    public void Ein_kurz_geschlossenes_Fenster_behaelt_seine_Ordnung()
    {
        var order = new SlotOrder();
        order.Remember(["A2", "A1", "B1"]);
        order.Remember(["B1"]);                  // Fenster A ist gerade zu

        // A kommt zurück -> die alte Ordnung muss noch da sein.
        Assert.Equal(["A2", "A1", "B1"], order.Apply(["A1", "A2", "B1"]));
    }

    [Fact]
    public void WindowOf_liest_den_fuehrenden_Buchstaben()
    {
        Assert.Equal("A", SlotOrder.WindowOf("A1"));
        Assert.Equal("B", SlotOrder.WindowOf("B12"));
        Assert.Equal("?", SlotOrder.WindowOf("42"));
        Assert.Equal("?", SlotOrder.WindowOf(""));
    }
}
