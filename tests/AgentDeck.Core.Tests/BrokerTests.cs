using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace AgentDeck.Core.Tests;

/// <summary>
/// Echter Round-Trip über Loopback. Alle Tests binden auf Port 0 (freier Port vom
/// Betriebssystem) – NIEMALS auf 8765, sonst kollidiert die Testsuite mit einem
/// laufenden Deck auf demselben Rechner.
/// </summary>
public class BrokerTests : IDisposable
{
    private readonly Broker _broker = new(port: 0);

    public BrokerTests() => _broker.Start();

    public void Dispose() => _broker.Dispose();

    /// <summary>Verbundene Test-Extension: Zeilen schreiben und lesen.</summary>
    private sealed class FakeExtension : IDisposable
    {
        private readonly TcpClient _tcp;
        private readonly StreamReader _reader;
        private readonly StreamWriter _writer;

        public FakeExtension(int port)
        {
            _tcp = new TcpClient("127.0.0.1", port);
            var stream = _tcp.GetStream();
            _reader = new StreamReader(stream, Encoding.UTF8);
            _writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
        }

        public void Send(string json) => _writer.Write(json + "\n");

        /// <summary>Eine Zeile mit Zeitgrenze lesen; nichts -> null.</summary>
        public string? Receive(int timeoutMs = 2000)
        {
            _tcp.ReceiveTimeout = timeoutMs;
            try { return _reader.ReadLine(); }
            catch (IOException) { return null; }
        }

        public void Dispose() { try { _tcp.Close(); } catch { } }
    }

    /// <summary>Auf eine Bedingung warten, statt fest zu schlafen (der Broker liest async).</summary>
    private static bool WaitFor(Func<bool> cond, int timeoutMs = 2000)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (Environment.TickCount64 < deadline)
        {
            if (cond())
                return true;
            Thread.Sleep(10);
        }
        return cond();
    }

    [Fact]
    public void Hello_meldet_den_Workspace_an()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"hello","workspace":"my-frontend","window":null,"slots":[]}""");

        Assert.True(WaitFor(() => _broker.Workspaces().Contains("my-frontend")),
                    "Broker hat den Workspace nicht übernommen");
    }

    [Fact]
    public void Assign_schickt_den_Buchstaben_und_macht_das_Fenster_adressierbar()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"hello","workspace":"my-frontend","window":null,"slots":[]}""");
        Assert.True(WaitFor(() => _broker.Workspaces().Contains("my-frontend")));

        Assert.True(_broker.Assign("my-frontend", "A"));

        var line = ext.Receive();
        Assert.NotNull(line);
        using var doc = JsonDocument.Parse(line!);
        Assert.Equal(Protocol.CmdAssign, doc.RootElement.GetProperty("cmd").GetString());
        Assert.Equal("A", doc.RootElement.GetProperty("window").GetString());
        Assert.True(_broker.Connected("A"));
    }

    [Fact]
    public void Assign_findet_den_Workspace_auch_bei_anderer_Gross_Kleinschreibung()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"hello","workspace":"My-Frontend","slots":[]}""");
        Assert.True(WaitFor(() => _broker.Workspaces().Count == 1));

        Assert.True(_broker.Assign("my-frontend", "A"));
    }

    [Fact]
    public void Terminals_Meldung_fuellt_die_Slotliste()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"terminals","workspace":"my-frontend","window":"A","slots":["A1","A2"]}""");

        Assert.True(WaitFor(() => _broker.Terminals("A").Count == 2));
        Assert.Equal(["A1", "A2"], _broker.Terminals("A"));
        Assert.Equal(["A1", "A2"], _broker.WorkspaceSlots("my-frontend"));
    }

    [Fact]
    public void Explizites_window_null_loescht_die_Zuordnung()
    {
        // Das ist der Fall, für den broker.py ausdrücklich 'if "window" in msg' prüft:
        // nach cmd:unassign muss eine nachlaufende Meldung mit window:null den
        // Buchstaben LÖSCHEN, sonst bleibt eine Phantomkachel hängen.
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"terminals","workspace":"my-frontend","window":"A","slots":["A1"]}""");
        Assert.True(WaitFor(() => _broker.Connected("A")));

        ext.Send("""{"type":"terminals","workspace":"my-frontend","window":null,"slots":["A1"]}""");
        Assert.True(WaitFor(() => !_broker.Connected("A")),
                    "window:null hat den Buchstaben nicht gelöscht -> Phantomkachel");
    }

    [Fact]
    public void Fehlendes_window_Feld_laesst_die_Zuordnung_stehen()
    {
        // Gegenprobe zum Test oben: FEHLT das Feld, darf nichts gelöscht werden.
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"terminals","workspace":"my-frontend","window":"A","slots":["A1"]}""");
        Assert.True(WaitFor(() => _broker.Connected("A")));

        ext.Send("""{"type":"terminals","workspace":"my-frontend","slots":["A1","A2"]}""");
        Assert.True(WaitFor(() => _broker.Terminals("A").Count == 2));
        Assert.True(_broker.Connected("A"), "ohne window-Feld darf der Buchstabe nicht verfallen");
    }

    [Fact]
    public void Seen_wird_gepuffert_und_beim_Abholen_geleert()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"seen","slot":"A1"}""");

        Assert.True(WaitFor(() => _broker.DrainSeen().Contains("A1")));
        Assert.Empty(_broker.DrainSeen());   // zweiter Abruf: Puffer ist leer
    }

    [Fact]
    public void Kaputte_Zeile_reisst_die_Verbindung_nicht_ab()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("das ist kein JSON {{{");
        ext.Send("""{"type":"hello","workspace":"my-frontend","slots":[]}""");

        Assert.True(WaitFor(() => _broker.Workspaces().Contains("my-frontend")),
                    "nach einer kaputten Zeile wurde nicht weitergelesen");
    }

    [Fact]
    public void SendWindow_an_unbekanntes_Fenster_ist_false_statt_Fehler()
    {
        Assert.False(_broker.SendWindow("Z", new Dictionary<string, object?> { ["cmd"] = "noop" }));
        Assert.False(_broker.Assign("gibt-es-nicht", "A"));
        Assert.False(_broker.Forget("Z"));
        Assert.Empty(_broker.Terminals("Z"));
    }

    [Fact]
    public void Forget_sagt_der_Extension_ab_und_loest_die_Bindung()
    {
        using var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"hello","workspace":"my-frontend","window":"A","slots":[]}""");
        Assert.True(WaitFor(() => _broker.Connected("A")));

        Assert.True(_broker.Forget("A"));

        var line = ext.Receive();
        Assert.NotNull(line);
        using var doc = JsonDocument.Parse(line!);
        Assert.Equal(Protocol.CmdUnassign, doc.RootElement.GetProperty("cmd").GetString());
        Assert.False(_broker.Connected("A"));
    }

    [Fact]
    public void Zweiter_Broker_auf_demselben_Port_bleibt_still_deaktiviert()
    {
        // Verhalten aus broker.py: Port belegt -> Broker still deaktiviert, KEIN Wurf.
        // Deshalb ist SO_REUSEADDR hier weggelassen - mit der Option würde Windows den
        // zweiten Bind erlauben und Extensions landen teils beim toten Panel.
        using var zweiter = new Broker(port: _broker.Port);
        zweiter.Start();

        Assert.True(_broker.IsListening);
        Assert.False(zweiter.IsListening);
    }

    [Fact]
    public void Verbindungsverlust_entfernt_den_Client()
    {
        var ext = new FakeExtension(_broker.Port);
        ext.Send("""{"type":"hello","workspace":"my-frontend","window":"A","slots":[]}""");
        Assert.True(WaitFor(() => _broker.Connected("A")));

        ext.Dispose();   // Fenster zu / Reload

        Assert.True(WaitFor(() => !_broker.Connected("A")),
                    "abgebrochene Verbindung wurde nicht aufgeräumt");
    }
}
