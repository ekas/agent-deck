namespace AgentDeck.Core;

/// <summary>
/// Die vom Benutzer per Drag &amp; Drop festgelegte Kachel-Reihenfolge.
///
/// Warum das überhaupt nötig ist: VS Code gibt die Reihenfolge seiner Terminals nicht
/// preis. Die Extension meldet sie in einer Ordnung, die sich beim Öffnen und Schließen
/// von Chats ändert – das Deck führt darum seine eigene und merkt sie in
/// <c>slot_order.json</c>.
/// </summary>
public sealed class SlotOrder
{
    /// <summary>Reihenfolge je Fenster-Buchstabe, z. B. <c>{"B": ["B2","B1"]}</c>.</summary>
    private Dictionary<string, List<string>> _order = [];

    /// <summary>
    /// Fenster-Buchstabe eines Slots: bei <c>A12</c> das führende <c>A</c>. Slots ohne
    /// erkennbaren Buchstaben landen in einer gemeinsamen Gruppe.
    /// </summary>
    public static string WindowOf(string slot) =>
        slot.Length > 0 && char.IsLetter(slot[0]) ? slot[..1].ToUpperInvariant() : "?";

    /// <summary>
    /// Slots in die gemerkte Reihenfolge bringen. Unbekannte Slots (neu angelegte
    /// Chats) hängen hinten an, in ihrer natürlichen Ordnung – so springt beim Anlegen
    /// eines Agenten nichts durcheinander.
    /// </summary>
    public List<string> Apply(IEnumerable<string> slots)
    {
        var rest = slots.ToList();
        var result = new List<string>();

        // Gruppen in der Reihenfolge ihrer Fenster-Buchstaben durchgehen.
        foreach (var window in rest.Select(WindowOf).Distinct().OrderBy(w => w, StringComparer.Ordinal))
        {
            var inWindow = rest.Where(s => WindowOf(s) == window).ToList();

            if (_order.TryGetValue(window, out var gemerkt))
            {
                // Erst die gemerkten (soweit noch vorhanden), dann der Rest sortiert.
                result.AddRange(gemerkt.Where(inWindow.Contains));
                result.AddRange(inWindow.Where(s => !gemerkt.Contains(s))
                                        .OrderBy(s => s, StringComparer.Ordinal));
            }
            else
            {
                result.AddRange(inWindow.OrderBy(s => s, StringComparer.Ordinal));
            }
        }
        return result;
    }

    /// <summary>
    /// Einen Slot an eine neue Stelle setzen (Ergebnis eines Drag &amp; Drop). Die
    /// Reihenfolge wird für das Fenster des Slots vollständig neu festgehalten.
    /// </summary>
    /// <param name="slots">Die vollständige, bereits neu sortierte Slot-Liste.</param>
    public void Remember(IEnumerable<string> slots)
    {
        var neu = new Dictionary<string, List<string>>();
        foreach (var slot in slots)
        {
            var w = WindowOf(slot);
            if (!neu.TryGetValue(w, out var list))
                neu[w] = list = [];
            list.Add(slot);
        }

        // Reihenfolgen von Fenstern, die gerade gar keinen Slot haben, NICHT vergessen:
        // ein kurz geschlossenes VS-Code-Fenster soll seine Ordnung behalten.
        foreach (var (w, list) in _order)
            neu.TryAdd(w, list);

        _order = neu;
    }

    /// <summary>
    /// Zwei Slots tauschen bzw. einen vor einen anderen ziehen – die übliche
    /// Drag-&amp;-Drop-Geste. Liefert die neue Reihenfolge.
    /// </summary>
    public static List<string> Move(IReadOnlyList<string> slots, string dragged, string target)
    {
        var list = slots.ToList();
        var from = list.IndexOf(dragged);
        var to = list.IndexOf(target);
        if (from < 0 || to < 0 || from == to)
            return list;

        list.RemoveAt(from);
        list.Insert(to, dragged);
        return list;
    }

    // ── Persistenz ──────────────────────────────────────────────────────
    /// <summary>
    /// Ablageort. BEWUSST derselbe wie in der Python-Fassung (<c>slot_order.json</c>
    /// neben dem Code), damit beide Fassungen dieselbe Reihenfolge sehen.
    /// </summary>
    public static string PathFor(string repoDir) => Path.Combine(repoDir, "slot_order.json");

    public static SlotOrder Load(string path)
    {
        var order = new SlotOrder();
        var data = DeckPaths.LoadJson<Dictionary<string, List<string>>>(path);
        if (data is not null)
            order._order = data;
        return order;
    }

    public void Save(string path)
    {
        try
        {
            DeckPaths.SaveJson(path, _order);
        }
        catch
        {
            // Reihenfolge ist Komfort, kein Zustand -> ein Schreibfehler darf das Deck
            // nicht aufhalten.
        }
    }
}
