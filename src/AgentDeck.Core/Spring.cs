namespace AgentDeck.Core;

/// <summary>
/// Kritisch gedämpfte Feder, ANALYTISCH gerechnet – portiert aus
/// <c>edge_dock._spring_at</c>.
///
/// Bei Dämpfungsgrad genau 1 hat die Bewegungsgleichung die geschlossene Lösung
/// <c>d(t) = (d₀ + (v₀ + ω·d₀)·t)·e^(−ω·t)</c> (doppelte Nullstelle des
/// charakteristischen Polynoms), abgeleitet
/// <c>v(t) = (v₀ − ω·(v₀ + ω·d₀)·t)·e^(−ω·t)</c>.
///
/// Die geschlossene Form ist nicht Angeberei, sondern Robustheit: eine Schritt-für-
/// Schritt-Integration wird bei großem dt instabil und müsste in Teilschritte zerlegt
/// werden – genau dann, wenn das System ohnehin klemmt (ausgefallene Frames, Standby).
/// Die Formel ist bei JEDEM dt exakt und liefert bei sehr großem dt sauber
/// "steht am Ziel".
///
/// Warum überhaupt eine Feder und kein smoothstep: eine symmetrische Kurve liest sich
/// mechanisch. Die Feder ist front-loaded – nach 80 ms sind 74 % des Wegs geschafft
/// statt 46 %, das Ausrollen danach liegt im einstelligen Pixelbereich und wird nicht
/// als Warten gelesen, sondern als Weichheit.
/// </summary>
public sealed class Spring
{
    /// <summary>
    /// Näher als das am Ziel gilt als angekommen. Eine Feder erreicht ihr Ziel nur
    /// asymptotisch; ohne die Schwelle liefe die Animation noch hunderte Millisekunden
    /// für eine Bewegung weiter, die längst unter einem Pixel liegt.
    /// </summary>
    public const double SettlePx = 1.0;

    /// <summary>Antwortzeit beim Aufklappen (<c>REVEAL_RESPONSE_MS</c>).</summary>
    public const double RevealResponseMs = 190;

    /// <summary>Einklappen ist zügiger – Wegräumen soll nicht warten lassen.</summary>
    public const double CollapseResponseMs = 150;

    private double _omega;

    /// <summary>Aktuelle Position, 0 = versteckt … 1 = ausgefahren.</summary>
    public double Position { get; private set; }

    /// <summary>Aktuelle Geschwindigkeit (Einheiten pro Sekunde).</summary>
    public double Velocity { get; private set; }

    /// <summary>Zielposition.</summary>
    public double Target { get; private set; }

    public Spring(double start = 0.0)
    {
        Position = Target = start;
        _omega = OmegaFor(RevealResponseMs);
    }

    /// <summary>Kreisfrequenz aus der gewünschten Antwortzeit.</summary>
    public static double OmegaFor(double responseMs) => 2.0 * Math.PI / (responseMs / 1000.0);

    /// <summary>
    /// Die nackte Formel, ohne Zustand – das direkte Gegenstück zu
    /// <c>edge_dock._spring_at</c>. Rein: Abstand zum Ziel und Geschwindigkeit jetzt.
    /// Raus: beides nach <paramref name="dtSeconds"/>.
    ///
    /// Als eigene Funktion, weil sie sich so Fall für Fall gegen die Python-Fassung
    /// prüfen lässt (siehe Golden-Tests) – der Zustand der Instanz stünde dabei nur im Weg.
    /// </summary>
    public static (double Distance, double Velocity) Step(
        double d0, double v0, double omega, double dtSeconds)
    {
        var e = Math.Exp(-omega * dtSeconds);
        var c = v0 + omega * d0;
        return ((d0 + c * dtSeconds) * e, (v0 - omega * c * dtSeconds) * e);
    }

    /// <summary>
    /// Neues Ziel setzen. Position und Geschwindigkeit laufen BEWUSST weiter – beim
    /// Richtungswechsel wird nur das Ziel getauscht, damit die Bewegung nicht springt.
    /// </summary>
    public void SetTarget(double target, double responseMs)
    {
        Target = target;
        _omega = OmegaFor(responseMs);
    }

    /// <summary>
    /// Um <paramref name="dtSeconds"/> weiterrechnen.
    /// </summary>
    /// <returns><c>true</c>, wenn die Feder angekommen ist (und festgesetzt wurde).</returns>
    /// <param name="spanPx">
    /// Länge des Wegs in Pixeln – nur nötig, um <see cref="SettlePx"/> in denselben
    /// 0…1-Maßstab zu übersetzen wie <see cref="Position"/>.
    /// </param>
    public bool Advance(double dtSeconds, double spanPx)
    {
        var (d, v) = Step(Position - Target, Velocity, _omega, dtSeconds);
        var pos = Target + d;

        if (pos < 0.0 || pos > 1.0)
        {
            // Kann nur beim Umkehren aus voller Fahrt passieren (die Feder selbst
            // schwingt nicht über). Wie gegen eine Wand: hier ist Schluss, die
            // Restgeschwindigkeit verfällt – ein Panel, das über den Bildschirmrand
            // hinaus- oder vom Rand wegschwingt, sieht schlicht kaputt aus.
            pos = Math.Clamp(pos, 0.0, 1.0);
            v = 0.0;
        }

        Position = pos;
        Velocity = v;

        // Angekommen? Der Abstand wird in Pixel umgerechnet, damit die Schwelle
        // unabhängig von der Panel-Größe dasselbe bedeutet.
        var span = Math.Max(1.0, spanPx);
        if (Math.Abs(Position - Target) * span < SettlePx)
        {
            Position = Target;
            Velocity = 0.0;
            return true;
        }
        return false;
    }

    /// <summary>Sofort ans Ziel setzen (Notbremse bei überlangem Slide).</summary>
    public void SnapToTarget()
    {
        Position = Target;
        Velocity = 0.0;
    }
}
