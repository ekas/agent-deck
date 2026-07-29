using AgentDeck.Claude;
using AgentDeck.Core;

// Claude-Code-Hooks als EIN Exe mit Unterkommando – statt zweier Skripte wie in der
// Python-Fassung (report.py / statusline.py), damit nur ein Artefakt gebaut und in
// ~/.claude/settings.json eingetragen werden muss:
//
//   AgentDeck.Hooks.exe report <status>     (SessionStart, UserPromptSubmit, Stop, …)
//   AgentDeck.Hooks.exe statusline          (statusLine)
//
// OBERSTE REGEL: niemals mit Fehler enden. Ein fehlgeschlagener Hook blockiert den
// Agenten – darum liegt um alles ein Fangnetz und der Exit-Code ist immer 0.

try
{
    var command = args.Length > 0 ? args[0].ToLowerInvariant() : "report";

    switch (command)
    {
        case "statusline":
            RunStatusLine();
            break;
        case "report":
            RunReport(args.Length > 1 ? args[1] : "thinking");
            break;
        default:
            // Unbekanntes Unterkommando: als Status für den report-Hook deuten, damit ein
            // Aufruf in der alten Form ("… report.py thinking" -> "… thinking") weiterhin
            // das Erwartete tut.
            RunReport(command);
            break;
    }
}
catch
{
    // Hooks dürfen niemals crashen.
}

return 0;

static void RunReport(string status)
{
    var slot = SlotResolver.ResolveSlot();
    if (string.IsNullOrEmpty(slot))
        return;                       // kein Slot zuordenbar -> nichts melden

    var data = HookPayload.ReadStdin();

    if (ReportHook.ShouldSkip(status, data))
        return;                       // Zustand bewusst unangetastet lassen

    var path = DeckPaths.StatePath(slot);
    var prev = DeckPaths.LoadJson<SlotState>(path);

    // Arbeitsverzeichnis = Repo-Root des Agenten (ein `cd` im Bash-Tool ändert das cwd
    // DIESES Prozesses nicht). Nur damit findet das Deck später den worktree wieder.
    string? cwd = null;
    try { cwd = Directory.GetCurrentDirectory(); } catch { /* egal */ }

    DeckPaths.SaveJson(path, ReportHook.Merge(
        slot, status, data, prev, Clock.UnixNow(), cwd));
}

static void RunStatusLine()
{
    var data = HookPayload.ReadStdin();
    var rec = StatusLineHook.Extract(data, Clock.UnixNow());

    // Der Deck-State ist Beiwerk -> darf die Statuszeile nie kaputt machen.
    try
    {
        var slot = SlotResolver.ResolveSlot();
        if (!string.IsNullOrEmpty(slot))
            StatusLineHook.SaveLive(slot, rec);
    }
    catch
    {
        // ignorieren
    }

    // IMMER eine Zeile ausgeben (auch ohne Slot), damit die Statuszeile nicht leer ist.
    Console.OutputEncoding = System.Text.Encoding.UTF8;   // das Trennzeichen ist "·"
    Console.Out.Write(StatusLineHook.Line(rec));
}
