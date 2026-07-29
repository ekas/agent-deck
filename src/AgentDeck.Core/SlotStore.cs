namespace AgentDeck.Core;

/// <summary>
/// Liest den Zustand aller Slots aus dem State-Ordner und interpretiert ihn – die
/// anzeigefreie Hälfte dessen, was <c>agent_deck.refresh()</c> tut.
///
/// Welche Kacheln es gibt, weiß im Vollbetrieb die Extension (sie meldet die Terminals
/// des Fensters). Solange das .NET-Panel noch keinen Broker-Zugriff hat, dienen die
/// vorhandenen State-Dateien als Quelle – das genügt, um echte Slots mit echtem Status
/// anzuzeigen, und stört ein parallel laufendes Python-Deck nicht.
/// </summary>
public sealed class SlotStore(double staleSeconds = 900)
{
    /// <summary>Zuletzt eingelesener Zustand je Slot.</summary>
    public Dictionary<string, SlotState> States { get; } = [];

    /// <summary>Ausgewerteter Anzeige-Status je Slot (normalisiert, inkl. <c>lost</c>).</summary>
    public Dictionary<string, string> StatusKeys { get; } = [];

    /// <summary>
    /// Zugewiesenes Ticket je Slot, aus der Marker-Datei <c>&lt;slot&gt;.ticket</c>.
    ///
    /// Das ist NUR das per Rechtsklick zugewiesene Ticket (an dem auch der worktree
    /// hängt). Die zusätzliche Auto-Erkennung der Python-Fassung, die Ticket und PR per
    /// Regex aus dem Transcript liest, gehört zu <c>chat_summary.py</c> und ist noch
    /// nicht portiert.
    /// </summary>
    public Dictionary<string, string> Tickets { get; } = [];

    /// <summary>
    /// Slot-Namen im State-Ordner. <c>&lt;slot&gt;.json</c> zählt, die Beiwerk-Dateien
    /// (<c>.live.json</c>, <c>.tmp</c>, <c>pidmap-*</c>) nicht.
    /// </summary>
    public static List<string> DiscoverSlots(string? stateDir = null)
    {
        var dir = stateDir ?? DeckPaths.StateDir;
        try
        {
            return [.. Directory.EnumerateFiles(dir, "*.json")
                                .Select(Path.GetFileName)
                                .Where(f => f is not null)
                                .Select(f => f!)
                                .Where(f => !f.EndsWith(".live.json", StringComparison.OrdinalIgnoreCase)
                                            && !f.StartsWith("pidmap-", StringComparison.OrdinalIgnoreCase))
                                .Select(f => f[..^".json".Length])
                                .OrderBy(s => s, StringComparer.OrdinalIgnoreCase)];
        }
        catch (DirectoryNotFoundException)
        {
            return [];   // noch kein Deck gelaufen
        }
    }

    /// <summary>
    /// Alle Slots neu einlesen und ihren Anzeige-Status bestimmen.
    /// </summary>
    /// <param name="now">Jetzt in Unix-Sekunden (injizierbar, damit testbar).</param>
    /// <param name="isConnected">
    /// Ob das Fenster des Slots am Broker hängt. Ohne Broker-Wissen <c>null</c> übergeben –
    /// dann wird NICHT auf "getrennt" geschlossen, damit nicht alles fälschlich rot wird.
    /// </param>
    /// <param name="stateDir">State-Ordner (für Tests überschreibbar).</param>
    public void Refresh(double now, Func<string, bool>? isConnected = null, string? stateDir = null)
    {
        var dir = stateDir ?? DeckPaths.StateDir;
        States.Clear();
        StatusKeys.Clear();
        Tickets.Clear();

        foreach (var slot in DiscoverSlots(dir))
        {
            var st = DeckPaths.LoadJson<SlotState>(Path.Combine(dir, slot + ".json"));
            if (st is null)
                continue;

            States[slot] = st;

            var fresh = StatusModel.IsFresh(st, now, staleSeconds);
            var key = StatusModel.NormalizeStatus(st.Status, fresh, StatusStyle.ValidStatus);

            // "getrennt" schlägt den gemeldeten Status - aber nur, wenn wir überhaupt
            // wissen, ob das Fenster verbunden ist.
            if (isConnected is not null
                && StatusModel.IsLost(st.Status, fresh, isConnected(slot)))
                key = StatusStyle.Lost;

            StatusKeys[slot] = key;

            if (ReadTicket(dir, slot) is { Length: > 0 } ticket)
                Tickets[slot] = ticket;
        }
    }

    /// <summary>
    /// Die Ticket-Marker-Datei ist Klartext, eine Zeile – der Agent schreibt die selbst
    /// gefundene ID hinein. Fehlt sie, hat der Slot kein zugewiesenes Ticket.
    /// </summary>
    private static string? ReadTicket(string dir, string slot)
    {
        try
        {
            var path = Path.Combine(dir, slot + ".ticket");
            return File.Exists(path) ? File.ReadAllText(path).Trim() : null;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Gesamtzustand für den Griff-Balken: das Dringlichste, was gerade ansteht.
    /// </summary>
    public string DominantStatus() => StatusModel.DominantStatus(StatusKeys.Values);
}
