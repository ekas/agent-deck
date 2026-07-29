namespace AgentDeck.App;

/// <summary>
/// Die ＋-Kachel: öffnet einen weiteren Claude-Chat in ihrem Fenster. Es gibt eine je
/// verbundenem VS-Code-Fenster, damit klar ist, WO der neue Chat aufgeht.
/// </summary>
/// <param name="Window">Fenster-Buchstabe, z. B. <c>A</c>.</param>
public sealed record AddTileViewModel(string Window)
{
    public string Caption => $"＋  Fenster {Window}";
}
