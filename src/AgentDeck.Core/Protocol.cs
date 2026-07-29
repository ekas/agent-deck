namespace AgentDeck.Core;

/// <summary>
/// Wire-Vokabular des Broker-Protokolls – die eine Quelle der Wahrheit (.NET-Seite).
///
/// Zwischen Panel und VS-Code-Extension laufen newline-getrennte JSON-Zeilen. Die
/// Strings hier sind der Vertrag und müssen mit <c>protocol.py</c> sowie
/// <c>extension/extension.js</c> übereinstimmen. Es gibt bewusst keinen Build-Step,
/// der die drei Seiten koppelt – wer hier etwas ändert, zieht die anderen von Hand mit.
/// <c>PythonCompatibilityTests</c> vergleicht diese Werte zur Testzeit gegen protocol.py.
/// </summary>
public static class Protocol
{
    // ── Kommandos: Panel -> Extension (JSON-Feld "cmd") ──────────────────
    /// <summary>Der Extension ihren Fenster-Buchstaben zuweisen.</summary>
    public const string CmdAssign = "assign";

    /// <summary>Buchstaben wieder vergessen (löst eine Phantomkachel).</summary>
    public const string CmdUnassign = "unassign";

    /// <summary>Ein bestimmtes Terminal (Slot) fokussieren.</summary>
    public const string CmdFocusPane = "focusPane";

    /// <summary>
    /// Text/Slash-Kommando an den Agent schicken. Feld <c>submit: true</c> heißt: die
    /// Extension schreibt den Text und schickt ihn per SEPARATEM Enter ab – ein
    /// mitgeschicktes \r würde bei langen Prompts (Paste) verschluckt.
    /// </summary>
    public const string CmdSend = "send";

    /// <summary>Einzelne Taste (enter/esc/shift-tab, ggf. mit repeat).</summary>
    public const string CmdKey = "key";

    /// <summary>Ein weiteres Claude-Terminal öffnen.</summary>
    public const string CmdCreateAgent = "createAgent";

    /// <summary>"Developer: Reload Window" auslösen.</summary>
    public const string CmdReload = "reload";

    /// <summary>Ein einzelnes Terminal/Agent schließen.</summary>
    public const string CmdCloseAgent = "closeAgent";

    /// <summary>Das ganze VS-Code-Fenster schließen.</summary>
    public const string CmdCloseWindow = "closeWindow";

    // ── Nachrichten-Typen: Extension -> Panel (JSON-Feld "type") ─────────
    /// <summary>Erstmeldung: Workspace-Name (+ evtl. window/slots).</summary>
    public const string TypeHello = "hello";

    /// <summary>Aktualisierte Terminal-/Slot-Liste des Fensters.</summary>
    public const string TypeTerminals = "terminals";

    /// <summary>Pane in VS Code direkt fokussiert -> Slot als gelesen (done->idle).</summary>
    public const string TypeSeen = "seen";

    /// <summary>Standard-Port des Brokers (config.BROKER_PORT).</summary>
    public const int DefaultPort = 8765;

    /// <summary>Der Broker hört ausschließlich lokal.</summary>
    public const string DefaultHost = "127.0.0.1";
}
