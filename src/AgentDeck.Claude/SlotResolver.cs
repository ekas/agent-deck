using System.Runtime.InteropServices;
using System.Runtime.Versioning;

using AgentDeck.Core;

namespace AgentDeck.Claude;

/// <summary>
/// Gemeinsame Basis der Claude-Code-Hooks: State-Ordner und die Slot-Auflösung über die
/// Windows-Prozesskette. Portiert aus <c>hookstate.py</c>.
///
/// Wie die Hooks selbst darf hier NIE eine Exception nach außen dringen – ein
/// fehlgeschlagener Hook blockiert den Agenten. Jede Methode hat darum einen
/// definierten Rückfall (leere Liste / <c>null</c>).
/// </summary>
public static class SlotResolver
{
    public static string StateDir => DeckPaths.StateDir;

    private const uint Th32csSnapProcess = 0x00000002;
    private static readonly IntPtr InvalidHandle = new(-1);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    private struct ProcessEntry32
    {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExeFile;
    }

    // Bewusst DllImport statt des neueren LibraryImport: der Quellcode-Generator
    // unterstützt PROCESSENTRY32 mit ByValTStr nicht und verlangt zusätzlich
    // AllowUnsafeBlocks für das ganze Projekt. ExactSpelling verhindert, dass die
    // Laufzeit nach einem nicht existierenden "Process32FirstA" sucht.
    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint dwFlags, uint th32ProcessID);

    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32First(IntPtr hSnapshot, ref ProcessEntry32 lppe);

    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32Next(IntPtr hSnapshot, ref ProcessEntry32 lppe);

    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr hObject);

    /// <summary>
    /// PID-Kette (ich -> Elternprozesse) via Toolhelp32-Snapshot. Nur Windows, kein
    /// Subprozess. Bei Problemen: leere Liste.
    /// </summary>
    [SupportedOSPlatform("windows")]
    private static List<int> AncestorPidsWindows()
    {
        var snap = CreateToolhelp32Snapshot(Th32csSnapProcess, 0);
        if (snap == InvalidHandle)
            return [];

        try
        {
            var parent = new Dictionary<int, int>();
            var e = new ProcessEntry32 { dwSize = (uint)Marshal.SizeOf<ProcessEntry32>() };
            var ok = Process32First(snap, ref e);
            while (ok)
            {
                parent[(int)e.th32ProcessID] = (int)e.th32ParentProcessID;
                ok = Process32Next(snap, ref e);
            }

            // Kette hochlaufen, mit Zyklusschutz (wie die seen-Menge in Python).
            var chain = new List<int>();
            var seen = new HashSet<int>();
            var pid = Environment.ProcessId;
            while (pid != 0 && seen.Add(pid))
            {
                chain.Add(pid);
                pid = parent.GetValueOrDefault(pid, 0);
            }
            return chain;
        }
        finally
        {
            CloseHandle(snap);
        }
    }

    /// <summary>PID-Kette des eigenen Prozesses; auf Nicht-Windows leer.</summary>
    public static List<int> AncestorPids()
    {
        if (!OperatingSystem.IsWindows())
            return [];
        try
        {
            return AncestorPidsWindows();
        }
        catch
        {
            return [];
        }
    }

    /// <summary>
    /// Alle <c>pidmap-*.json</c> (je Fenster von der Extension geschrieben) zu
    /// {pid -> slot} mergen. PIDs sind global eindeutig -> die Union ist sicher.
    /// Halb geschriebene oder kaputte Dateien werden einzeln übersprungen.
    /// </summary>
    public static Dictionary<int, string> LoadPidMap(string baseDir)
    {
        var outMap = new Dictionary<int, string>();
        try
        {
            foreach (var file in Directory.EnumerateFiles(baseDir, "pidmap-*.json"))
            {
                try
                {
                    var map = DeckPaths.LoadJson<Dictionary<string, string>>(file);
                    if (map is null)
                        continue;
                    foreach (var (k, v) in map)
                        if (int.TryParse(k, out var pid))
                            outMap[pid] = v;
                }
                catch
                {
                    // einzelne kaputte Datei ignorieren
                }
            }
        }
        catch (DirectoryNotFoundException)
        {
            // State-Ordner existiert noch nicht -> nichts zuzuordnen
        }
        catch
        {
            // niemals werfen
        }
        return outMap;
    }

    /// <summary>
    /// Ersten eigenen Vorfahren finden, der in der pidmap steht -> dessen Slot. Der von
    /// der Extension notierte Claude-PID ist immer ein Vorfahre des Hook-Prozesses.
    /// Kein Treffer -> <c>null</c> (dann meldet der Hook nichts).
    /// </summary>
    public static string? SlotFromProcs(string baseDir)
    {
        var pm = LoadPidMap(baseDir);
        if (pm.Count == 0)
            return null;
        foreach (var pid in AncestorPids())
            if (pm.TryGetValue(pid, out var slot))
                return slot;
        return null;
    }

    /// <summary>
    /// Slot dieses Hook-Aufrufs: zuerst <c>AGENT_SLOT</c> (von der Extension gesetzt),
    /// sonst der Weg über die Prozesskette – wie in <c>report.py</c>.
    /// </summary>
    public static string? ResolveSlot()
    {
        var env = Environment.GetEnvironmentVariable("AGENT_SLOT");
        return !string.IsNullOrEmpty(env) ? env : SlotFromProcs(StateDir);
    }
}
