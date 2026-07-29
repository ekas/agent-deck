using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace AgentDeck.Core;

/// <summary>
/// Broker: schlanker TCP-Server, über den das Panel mit den VS-Code-Extensions redet.
/// Protokoll = newline-getrenntes JSON. Portiert aus <c>broker.py</c>.
///
/// Ablauf:
/// <list type="bullet">
/// <item>Jede Extension-Instanz verbindet sich und meldet ihren Workspace-Namen:
///   <c>{"type":"hello","workspace":"my-frontend","window":null,"slots":[]}</c></item>
/// <item>Das Panel weist zu, welches Fenster A bzw. B ist:
///   <c>Assign("my-frontend", "A")</c> -> schickt <c>{"cmd":"assign","window":"A"}</c></item>
/// <item>Danach adressiert das Panel per Fenster-Buchstabe: <see cref="SendWindow"/>.</item>
/// <item>Extensions melden Terminals via <c>{"type":"terminals","window":"A","slots":[…]}</c>.</item>
/// </list>
/// </summary>
public sealed class Broker : IDisposable
{
    private sealed class Client(TcpClient tcp)
    {
        public TcpClient Tcp { get; } = tcp;
        public string? Workspace { get; set; }
        public string? Window { get; set; }
        public List<string> Slots { get; set; } = [];
    }

    private readonly object _lock = new();
    private readonly List<Client> _clients = [];
    private readonly HashSet<string> _seen = [];   // Slots, deren Pane fokussiert wurde (type:seen)
    private readonly string _host;
    private TcpListener? _listener;
    private CancellationTokenSource? _cts;

    public Broker(string host = Protocol.DefaultHost, int port = Protocol.DefaultPort)
    {
        _host = host;
        Port = port;
    }

    /// <summary>
    /// Der Port, auf dem tatsächlich gehört wird. Wurde 0 übergeben, steht hier nach
    /// <see cref="Start"/> der vom Betriebssystem vergebene freie Port (praktisch für Tests).
    /// </summary>
    public int Port { get; private set; }

    /// <summary>
    /// True, wenn der Server hört. Ist der Port belegt (zweites Panel), bleibt der Broker
    /// – wie in der Python-Fassung – STILL DEAKTIVIERT statt zu werfen.
    /// </summary>
    public bool IsListening { get; private set; }

    public void Start()
    {
        try
        {
            _listener = new TcpListener(IPAddress.Parse(_host), Port);
            // BEWUSST OHNE SO_REUSEADDR (anders als broker.py): unter Windows erlaubt die
            // Option ZWEI Sockets auf demselben Port – der zweite Broker würde ebenfalls
            // "erfolgreich" binden, und Extensions landen dann teils beim toten Panel.
            // Ohne die Option schlägt Bind bei belegtem Port ehrlich fehl und der Broker
            // deaktiviert sich still, wie beabsichtigt. Für den Neustart genügt Stop(),
            // das den Listener schließt und den Port sofort freigibt.
            _listener.Start(backlog: 5);
            Port = ((IPEndPoint)_listener.LocalEndpoint).Port;
            IsListening = true;
        }
        catch (SocketException)
        {
            IsListening = false;   // Port belegt -> still deaktiviert
            return;
        }

        _cts = new CancellationTokenSource();
        _ = AcceptLoopAsync(_cts.Token);
    }

    /// <summary>
    /// Server-Socket schließen -> der Accept-Loop bricht ab und der Port wird sofort frei.
    /// Wichtig beim Neustart, damit die neue Instanz den Port wieder binden kann.
    /// </summary>
    public void Stop()
    {
        IsListening = false;
        try { _cts?.Cancel(); } catch { /* egal */ }
        try { _listener?.Stop(); } catch { /* egal */ }
        _listener = null;

        lock (_lock)
        {
            foreach (var c in _clients)
                try { c.Tcp.Close(); } catch { /* egal */ }
            _clients.Clear();
        }
    }

    public void Dispose() => Stop();

    private async Task AcceptLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            TcpClient tcp;
            try
            {
                tcp = await _listener!.AcceptTcpClientAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception)
            {
                break;   // Listener geschlossen
            }
            _ = HandleClientAsync(tcp, ct);
        }
    }

    private async Task HandleClientAsync(TcpClient tcp, CancellationToken ct)
    {
        var cl = new Client(tcp);
        lock (_lock)
            _clients.Add(cl);

        try
        {
            using var reader = new StreamReader(tcp.GetStream(), Encoding.UTF8);
            while (!ct.IsCancellationRequested)
            {
                var line = await reader.ReadLineAsync(ct).ConfigureAwait(false);
                if (line is null)
                    break;                       // Gegenseite hat geschlossen
                if (string.IsNullOrWhiteSpace(line))
                    continue;
                HandleMessage(cl, line);
            }
        }
        catch
        {
            // Verbindungsabbrüche sind normal (Fenster zu, Reload) -> nicht lärmen
        }
        finally
        {
            lock (_lock)
                _clients.Remove(cl);
            try { tcp.Close(); } catch { /* egal */ }
        }
    }

    /// <summary>
    /// Eine Zeile verarbeiten. Bewusst über <see cref="JsonDocument"/> statt über einen
    /// Record: bei <c>window</c> muss "Feld fehlt" von "Feld ist explizit null"
    /// unterschieden werden. Explizit null (nach <c>cmd:unassign</c>) MUSS den
    /// Buchstaben löschen – sonst bliebe eine vergessene Kachel hängen, falls eine alte
    /// Meldung dem unassign zuvorgekommen ist. Ein deserialisierter Record könnte beide
    /// Fälle nicht auseinanderhalten (beides wäre <c>null</c>).
    /// </summary>
    private void HandleMessage(Client cl, string line)
    {
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(line);
        }
        catch (JsonException)
        {
            return;   // kaputte Zeile überspringen (wie Pythons continue)
        }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return;

            var type = root.TryGetProperty("type", out var t) ? t.GetString() : null;

            if (type is Protocol.TypeHello or Protocol.TypeTerminals)
            {
                lock (_lock)
                {
                    if (root.TryGetProperty("workspace", out var ws)
                        && ws.GetString() is { Length: > 0 } wsName)
                        cl.Workspace = wsName;

                    // Nur wenn das Feld ÜBERHAUPT da ist – dann aber auch bei null.
                    if (root.TryGetProperty("window", out var win))
                        cl.Window = win.ValueKind == JsonValueKind.String
                                    && win.GetString() is { Length: > 0 } w ? w : null;

                    if (root.TryGetProperty("slots", out var slots)
                        && slots.ValueKind == JsonValueKind.Array)
                        cl.Slots = [.. slots.EnumerateArray()
                                            .Select(s => s.GetString())
                                            .Where(s => s is not null)
                                            .Select(s => s!)];
                }
            }
            else if (type == Protocol.TypeSeen)
            {
                // Pane in VS Code angeklickt -> Slot vormerken; das Panel holt ihn per
                // DrainSeen() ab und schaltet 'ungelesen' -> 'idle'.
                if (root.TryGetProperty("slot", out var s)
                    && s.GetString() is { Length: > 0 } slot)
                    lock (_lock)
                        _seen.Add(slot);
            }
        }
    }

    // ── intern ──────────────────────────────────────────────────────────
    private Client? Find(string? window = null, string? workspace = null)
    {
        lock (_lock)
        {
            foreach (var cl in _clients)
            {
                if (window is not null && cl.Window == window)
                    return cl;
                if (workspace is not null && cl.Workspace is not null
                    && string.Equals(cl.Workspace, workspace, StringComparison.OrdinalIgnoreCase))
                    return cl;
            }
        }
        return null;
    }

    private static bool Write(Client cl, object obj)
    {
        try
        {
            var payload = Encoding.UTF8.GetBytes(
                JsonSerializer.Serialize(obj, DeckPaths.JsonOptions) + "\n");
            cl.Tcp.GetStream().Write(payload, 0, payload.Length);
            return true;
        }
        catch
        {
            return false;
        }
    }

    // ── öffentlich ──────────────────────────────────────────────────────
    /// <summary>Kommando an das Fenster mit diesem Buchstaben. True bei Erfolg.</summary>
    public bool SendWindow(string window, object obj)
    {
        var cl = Find(window: window);
        return cl is not null && Write(cl, obj);
    }

    /// <summary>Der Extension mit diesem Workspace den Fenster-Buchstaben zuweisen.</summary>
    public bool Assign(string workspace, string window)
    {
        var cl = Find(workspace: workspace);
        if (cl is null)
            return false;
        lock (_lock)
            cl.Window = window;
        return Write(cl, new Dictionary<string, object?>
        {
            ["cmd"] = Protocol.CmdAssign,
            ["window"] = window,
        });
    }

    /// <summary>
    /// Die Zuordnung dieses Buchstabens lösen: der Extension sagen, dass sie ihren
    /// Buchstaben vergisst (<c>cmd:unassign</c>), und die serverseitige Zuordnung
    /// aufheben. Nötig, damit auch eine verbundene, aber bindungslose Phantomkachel
    /// verschwindet – sonst meldet die Extension ihren gemerkten Buchstaben neu.
    /// </summary>
    public bool Forget(string window)
    {
        var cl = Find(window: window);
        if (cl is null)
            return false;
        Write(cl, new Dictionary<string, object?> { ["cmd"] = Protocol.CmdUnassign });
        lock (_lock)
            cl.Window = null;
        return true;
    }

    public bool Connected(string window) => Find(window: window) is not null;

    /// <summary>
    /// Slots, deren Pane seit dem letzten Aufruf in VS Code fokussiert wurde, zurückgeben
    /// UND den Puffer leeren. Damit schaltet das Panel 'ungelesen' (done) -> 'idle',
    /// sobald man einen Agenten direkt in VS Code anklickt.
    /// </summary>
    public HashSet<string> DrainSeen()
    {
        lock (_lock)
        {
            var outSet = new HashSet<string>(_seen);
            _seen.Clear();
            return outSet;
        }
    }

    /// <summary>Aktuell gemeldete Terminal-/Slot-Namen des Fensters (leer, wenn keins).</summary>
    public List<string> Terminals(string window)
    {
        var cl = Find(window: window);
        lock (_lock)
            return cl is not null ? [.. cl.Slots] : [];
    }

    /// <summary>
    /// Slot-Namen des Clients mit diesem Workspace. Genutzt, um beim Auto-Binden den
    /// Buchstaben zu bevorzugen, den die vorhandenen Terminals schon tragen (z. B. 'C1')
    /// -> stabile Zuordnung.
    /// </summary>
    public List<string> WorkspaceSlots(string workspace)
    {
        var cl = Find(workspace: workspace);
        lock (_lock)
            return cl is not null ? [.. cl.Slots] : [];
    }

    /// <summary>Aktuell verbundene Workspace-Namen (für Anzeige/Diagnose).</summary>
    public List<string> Workspaces()
    {
        lock (_lock)
            return [.. _clients.Where(c => c.Workspace is not null).Select(c => c.Workspace!)];
    }
}
