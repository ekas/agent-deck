using System.Runtime.InteropServices;
using AgentDeck.Core;

namespace AgentDeck.Windows;

/// <summary>
/// Die Win32-Auskünfte, die kein Framework von sich aus gibt: der Arbeitsbereich des
/// Monitors unter einem bestimmten Punkt (nicht nur des primären) und die
/// Zeigerposition. Gegenstück zu <c>screen_fit.py</c> und dem Mess-Teil von
/// <c>win_focus.py</c>.
///
/// BEWUSST ohne WPF-Bezug: diese Schicht kennt nur Fensterhandles und Zahlen. Der
/// Skalierungsfaktor wird hereingereicht, statt ihn aus einem <c>Window</c> zu ziehen –
/// sonst hinge die Plattformschicht an der Oberfläche und ließe sich weder einzeln
/// testen noch von den Hooks nutzen.
///
/// Einheiten: gelieferte Rechtecke sind LOGISCH (durch <c>scale</c> geteilt); alles,
/// was <c>Pixel</c> heißt, ist roh. Das ist die Falle bei 150 %-Anzeige – wer beides
/// mischt, liegt um den Faktor 1,5 daneben.
/// </summary>
public static class Win32Screen
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left, Top, Right, Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X, Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo
    {
        public int cbSize;
        public Rect rcMonitor;
        public Rect rcWork;
        public int dwFlags;
    }

    private const int MonitorDefaultToNearest = 2;

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(Point pt, int flags);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, int flags);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetMonitorInfoW(IntPtr hMonitor, ref MonitorInfo lpmi);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out Point lpPoint);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForSystem();

    /// <summary>
    /// Pixel→Punkt-Faktor des Systems. Nur als Rückfall gedacht – wer ein Fenster hat,
    /// nimmt dessen echten Faktor, denn der kann je Monitor abweichen.
    /// </summary>
    public static double SystemScale()
    {
        var dpi = GetDpiForSystem();
        return dpi > 0 ? dpi / 96.0 : 1.0;
    }

    /// <summary>Zeigerposition in ROHEN Pixeln.</summary>
    public static (int X, int Y) CursorPixel() =>
        GetCursorPos(out var p) ? (p.X, p.Y) : (0, 0);

    /// <summary>Zeigerposition in logischen Einheiten.</summary>
    public static (double X, double Y) CursorPosition(double scale)
    {
        var (px, py) = CursorPixel();
        var s = scale > 0 ? scale : 1.0;
        return (px / s, py / s);
    }

    /// <summary>
    /// Arbeitsbereich (ohne Taskleiste) des Monitors unter dem angegebenen Punkt.
    /// Der Punkt wird in Pixeln erwartet, weil er meist direkt aus
    /// <see cref="CursorPixel"/> stammt.
    /// </summary>
    public static DockRect WorkAreaAtPixel(int px, int py, double scale) =>
        WorkAreaOf(MonitorFromPoint(new Point { X = px, Y = py }, MonitorDefaultToNearest), scale);

    /// <summary>Arbeitsbereich des Monitors, auf dem das Fenster überwiegend liegt.</summary>
    public static DockRect WorkAreaForWindow(IntPtr hwnd, double scale)
    {
        var mon = hwnd != IntPtr.Zero
            ? MonitorFromWindow(hwnd, MonitorDefaultToNearest)
            : MonitorFromPoint(default, MonitorDefaultToNearest);
        return WorkAreaOf(mon, scale);
    }

    private static DockRect WorkAreaOf(IntPtr monitor, double scale)
    {
        var s = scale > 0 ? scale : 1.0;
        var mi = new MonitorInfo { cbSize = Marshal.SizeOf<MonitorInfo>() };

        if (monitor == IntPtr.Zero || !GetMonitorInfoW(monitor, ref mi))
            return new DockRect(0, 0, 1920 / s, 1080 / s);   // letzter Rückfall

        return new DockRect(
            mi.rcWork.Left / s,
            mi.rcWork.Top / s,
            (mi.rcWork.Right - mi.rcWork.Left) / s,
            (mi.rcWork.Bottom - mi.rcWork.Top) / s);
    }
}
