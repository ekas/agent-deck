namespace AgentDeck.Core;

/// <summary>
/// Reine Status-Interpretation, aus dem Renderloop herausgezogen: keine Anzeige, kein
/// UI-Framework, keine Seiteneffekte -> ohne laufendes Panel testbar. Portiert aus
/// <c>status_model.py</c>, Verhalten 1:1 (die Tests spiegeln die Python-Tests).
///
/// Es geht um die kleinen, aber kniffligen Regeln: wann gilt eine Meldung als frisch,
/// wann fällt ein "denkt" auf idle zurück, wann ist eine Verbindung verloren, wie löst
/// sich die xhigh/ultracode-Effort-Kollision auf und wann übernimmt das Deck einen per
/// Hook gemeldeten Permission-Mode.
/// </summary>
public static class StatusModel
{
    /// <summary>
    /// Rangfolge der Kachel-Status für den Griff-Balken (Neon): wichtig -> unwichtig.
    /// <c>lost</c> ist kein gemeldeter Status, sondern der im Panel berechnete
    /// Verbindungsverlust.
    /// </summary>
    public static readonly string[] DeckPriority =
        ["waiting", "done", "lost", "thinking", "idle", "none"];

    /// <summary>
    /// <c>running</c> und <c>thinking</c> sind derselbe Zustand ("denkt", gleiche Farbe)
    /// -> gleicher Rang und derselbe kanonische Name. Sonst blitzte der Griff bei jedem
    /// Wechsel zwischen den beiden Meldungen auf, obwohl sich sichtbar nichts ändert.
    /// </summary>
    private static readonly Dictionary<string, string> DeckAlias = new() { ["running"] = "thinking" };

    private static string Canonical(string? key) =>
        key is not null && DeckAlias.TryGetValue(key, out var alias) ? alias : key ?? "";

    /// <summary>
    /// <c>IReadOnlyList&lt;T&gt;</c> hat kein <c>IndexOf</c>. Der Parametertyp bleibt
    /// trotzdem die schmale Schnittstelle (der Zyklus wird nie verändert), also die
    /// Suche hier von Hand – wie Pythons <c>list.index()</c>, nur ohne Exception.
    /// </summary>
    private static int IndexIn(IReadOnlyList<string> list, string? value)
    {
        for (var i = 0; i < list.Count; i++)
            if (list[i] == value)
                return i;
        return -1;
    }

    /// <summary>Meldung frisch = existiert und ist nicht älter als <paramref name="staleS"/> Sekunden.</summary>
    public static bool IsFresh(SlotState? st, double now, double staleS) =>
        st is not null && now - st.Ts <= staleS;

    /// <summary>
    /// Status für die Anzeige normalisieren: <c>thinking</c>/<c>running</c> ohne frische
    /// Meldung gilt als eingeschlafen (idle); ein unbekannter Status ebenfalls idle.
    /// <paramref name="valid"/> = erlaubte Status (z. B. die Schlüssel von GLOW_STYLE).
    /// </summary>
    public static string NormalizeStatus(string? status, bool fresh, IReadOnlySet<string> valid)
    {
        if ((status is "thinking" or "running") && !fresh)
            return "idle";                       // lange still -> als idle
        if (status is null || !valid.Contains(status))
            return "idle";
        return status;
    }

    /// <summary>
    /// Rot = Verbindung zum Fenster verloren. Nur für frische, aktive Agenten, damit
    /// alte Restdateien beim Start nicht fälschlich rot werden.
    /// </summary>
    public static bool IsLost(string? status, bool fresh, bool connected) =>
        status != "none" && fresh && !connected;

    /// <summary>
    /// Alle Kachel-Status zu EINEM Deck-Gesamtzustand verdichten (für die Neon-Farbe des
    /// eingeklappten Griff-Balkens): Rückfrage &gt; ungelesen &gt; getrennt &gt; denkt &gt; idle.
    /// So sieht man am Griff, ob einer etwas von dir will, auch wenn das Deck zu ist.
    /// Keine Kacheln -> <c>none</c>.
    /// </summary>
    public static string DominantStatus(IEnumerable<string?> keys)
    {
        var canonical = keys.Select(Canonical).ToHashSet();
        foreach (var k in DeckPriority)
            if (canonical.Contains(k))
                return k;
        return "none";
    }

    /// <summary>Rang in <see cref="DeckPriority"/> (klein = dringlicher); Unbekanntes zählt als harmlos.</summary>
    private static int DeckRank(string? key)
    {
        var idx = Array.IndexOf(DeckPriority, Canonical(key));
        return idx >= 0 ? idx : DeckPriority.Length;
    }

    /// <summary>
    /// Wird der Deck-Gesamtzustand DRINGLICHER? Nur dann blitzt der Griff kurz auf. Ein
    /// Wechsel auf einen ruhigeren Zustand (z. B. ungelesen -> idle, weil du die Antwort
    /// gelesen hast) ist deine eigene Geste und blitzt bewusst NICHT.
    /// </summary>
    public static bool Escalated(string? prev, string? key) =>
        key != prev && DeckRank(key) < DeckRank(prev);

    /// <summary>
    /// Effort-Kollision auflösen: die statusLine meldet für xhigh UND ultracode nur
    /// <c>xhigh</c>. Das per Menü gemerkte Effort gewinnt bei leer/<c>xhigh</c> (und
    /// überbrückt fehlende Live-Daten); meldet die statusLine ein KONKRETES anderes Level
    /// (z. B. nach Modellwechsel auf den Modell-Default zurückgesetzt), gewinnt dieser
    /// echte Wert -> keine veraltete Anzeige.
    /// </summary>
    public static string? ResolveEffort(string? liveEff, string? remembered) =>
        !string.IsNullOrEmpty(remembered) && liveEff is "" or "xhigh" ? remembered : liveEff;

    /// <summary>
    /// Anzahl Shift+Tab vom angenommenen aktuellen zum Ziel-Modus (zyklisch).
    /// <paramref name="remembered"/> = gemerkter Modus-Index des Slots (<c>null</c> ->
    /// es wird der Start-Modus angenommen, wie bei einem frischen Chat). Liefert
    /// (Schritte, Ziel-Index) oder <c>null</c>, wenn das Ziel nicht im Zyklus liegt.
    /// Gemeinsame Basis für die Mode-Umschaltung und den Auto-Startmodus neuer Agenten.
    /// </summary>
    public static (int Steps, int Target)? ModeSteps(
        int? remembered, string target, IReadOnlyList<string> cycle, string start)
    {
        var tgt = IndexIn(cycle, target);
        if (tgt < 0)
            return null;

        var cur = remembered ?? Math.Max(IndexIn(cycle, start), 0);   // Start nicht im Zyklus -> 0
        // In C# kann % negativ werden (Python nicht) -> vor dem Modulo addieren.
        var steps = ((tgt - cur) % cycle.Count + cycle.Count) % cycle.Count;
        return (steps, tgt);
    }

    /// <summary>
    /// Ist-Permission-Mode aus einem Hook-Event übernehmen (selbstkorrigierend): liefert
    /// (Modus-Index, ts) bei einem NEUEREN Event mit gültigem Modus, sonst <c>null</c>.
    /// So folgt die Deck-Annahme dem zuletzt gemeldeten echten Modus.
    /// </summary>
    public static (int ModeIndex, double Ts)? AdoptHookMode(
        double prevTs, SlotState? st, IReadOnlyList<string> cycle)
    {
        if (st?.Mode is null)
            return null;
        var idx = IndexIn(cycle, st.Mode);
        return idx >= 0 && st.Ts > prevTs ? (idx, st.Ts) : null;
    }
}
