namespace AgentDeck.Core;

/// <summary>An welchem Bildschirmrand das Deck klebt (<c>edge_dock.EDGES</c>).</summary>
public enum DockEdge
{
    /// <summary>Nicht angedockt – normales Fenster mit Titelleiste.</summary>
    Off,
    Left,
    Right,
    Top,
}

/// <summary>Ein Rechteck in Bildschirmkoordinaten (DIPs), ohne WPF-Abhängigkeit.</summary>
public readonly record struct DockRect(double X, double Y, double Width, double Height)
{
    public double Right => X + Width;
    public double Bottom => Y + Height;
}

/// <summary>
/// Wo Panel und Griff liegen – die reine Rechnerei hinter <c>edge_dock.py</c>, ohne
/// Fenster und ohne Win32. Damit ist der Teil testbar, der am ehesten still falsch
/// wird: ein halb ausgefahrenes Deck ist der eine Zustand, den es nicht geben darf.
/// </summary>
public static class DockGeometry
{
    /// <summary>Dicke des Griffs quer zum Rand.</summary>
    public const double HandleThick = 16;

    /// <summary>Mindestlänge des Griffs entlang des Rands.</summary>
    public const double HandleMinLen = 90;

    /// <summary>Höchstlänge – sehr hohe/breite Decks bekommen keinen Riesen-Griff.</summary>
    public const double HandleMaxLen = 220;

    /// <summary>
    /// Abstand zur echten Bildschirmkante. NICHT bündig: Windows 11 malt bei runden
    /// Ecken seinen grauen Rand über die äußerste Pixelreihe, wodurch an drei von vier
    /// Kanten der eigene Rand verschluckt würde.
    /// </summary>
    public const double EdgeGap = 2;

    /// <summary>Kulanz um das aufgeklappte Fenster, bevor "Zeiger draußen" gilt.</summary>
    public const double InsideMargin = 8;

    /// <summary>Liegt die Kante an einer senkrechten Bildschirmseite?</summary>
    public static bool IsVertical(this DockEdge edge) => edge is DockEdge.Left or DockEdge.Right;

    /// <summary>
    /// Position des Panels bei Fortschritt <paramref name="progress"/>
    /// (0 = ganz versteckt, 1 = ganz ausgefahren).
    ///
    /// Versteckt heißt: komplett jenseits der Kante. Der Slide ist ein Positions-Slide,
    /// KEIN Ein-/Ausblenden – das Panel schiebt sich quer zum Rand hinaus, und der über
    /// die Kante geschobene Teil wird abgeschnitten.
    /// </summary>
    /// <param name="along">
    /// Position entlang des Rands (y bei Left/Right, x bei Top) – der Griff ist dort
    /// verschiebbar, das Panel folgt ihm.
    /// </param>
    public static DockRect PanelRect(
        DockEdge edge, DockRect workArea, double width, double height,
        double along, double progress)
    {
        progress = Math.Clamp(progress, 0.0, 1.0);

        switch (edge)
        {
            case DockEdge.Left:
                {
                    var shown = workArea.X + EdgeGap;
                    var hidden = workArea.X - width;
                    return new DockRect(Lerp(hidden, shown, progress),
                                        ClampAlong(along, workArea.Y, workArea.Bottom, height),
                                        width, height);
                }
            case DockEdge.Right:
                {
                    var shown = workArea.Right - width - EdgeGap;
                    var hidden = workArea.Right;
                    return new DockRect(Lerp(hidden, shown, progress),
                                        ClampAlong(along, workArea.Y, workArea.Bottom, height),
                                        width, height);
                }
            case DockEdge.Top:
                {
                    var shown = workArea.Y + EdgeGap;
                    var hidden = workArea.Y - height;
                    return new DockRect(ClampAlong(along, workArea.X, workArea.Right, width),
                                        Lerp(hidden, shown, progress),
                                        width, height);
                }
            default:
                // Nicht angedockt: das Fenster steht, wo es steht.
                return new DockRect(workArea.X, workArea.Y, width, height);
        }
    }

    /// <summary>
    /// Rechteck des Griffs. Er sitzt IMMER am Rand, unabhängig davon, ob das Panel
    /// gerade ausgefahren ist – er ist der Anfasser, wenn nichts anderes sichtbar ist.
    /// </summary>
    /// <param name="panelLength">
    /// Länge des Panels entlang des Rands; der Griff orientiert sich daran, bleibt aber
    /// zwischen <see cref="HandleMinLen"/> und <see cref="HandleMaxLen"/>.
    /// </param>
    public static DockRect HandleRect(DockEdge edge, DockRect workArea, double along, double panelLength)
    {
        var len = Math.Clamp(panelLength * 0.5, HandleMinLen, HandleMaxLen);

        return edge switch
        {
            DockEdge.Left => new DockRect(
                workArea.X + EdgeGap, ClampAlong(along, workArea.Y, workArea.Bottom, len),
                HandleThick, len),
            DockEdge.Right => new DockRect(
                workArea.Right - HandleThick - EdgeGap, ClampAlong(along, workArea.Y, workArea.Bottom, len),
                HandleThick, len),
            DockEdge.Top => new DockRect(
                ClampAlong(along, workArea.X, workArea.Right, len), workArea.Y + EdgeGap,
                len, HandleThick),
            _ => default,
        };
    }

    /// <summary>
    /// Ist der Zeiger innerhalb des Panels (mit Kulanzrand)? Grundlage fürs Einklappen.
    /// </summary>
    public static bool PointerInside(DockRect panel, double px, double py, double margin = InsideMargin) =>
        px >= panel.X - margin && px <= panel.Right + margin
        && py >= panel.Y - margin && py <= panel.Bottom + margin;

    private static double Lerp(double a, double b, double t) => a + (b - a) * t;

    /// <summary>
    /// Entlang des Rands im sichtbaren Bereich halten – sonst rutscht ein weit
    /// gezogener Griff aus dem Arbeitsbereich und ist nicht mehr erreichbar.
    /// </summary>
    private static double ClampAlong(double along, double min, double max, double length)
    {
        var limit = Math.Max(min, max - length);
        return Math.Clamp(along, min, limit);
    }
}
