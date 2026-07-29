using System.Windows;
using System.Windows.Interop;
using AgentDeck.Core;
using AgentDeck.Windows;

namespace AgentDeck.App;

/// <summary>
/// Die Brücke zwischen WPF und der Plattformschicht: hier – und nur hier – wird aus
/// einem <see cref="Window"/> ein Fensterhandle und ein Skalierungsfaktor.
/// </summary>
public static class WindowExtensions
{
    /// <summary>
    /// Pixel→Punkt-Faktor DIESES Fensters. WPF kennt ihn über die
    /// Rendertransformation; vor dem ersten Anzeigen gibt es die noch nicht, dann
    /// gilt der Systemwert.
    /// </summary>
    public static double DpiScale(this Window window) =>
        PresentationSource.FromVisual(window)?.CompositionTarget is { } ct
            ? ct.TransformToDevice.M11
            : Win32Screen.SystemScale();

    /// <summary>Fensterhandle; <see cref="IntPtr.Zero"/>, solange das Fenster nicht erzeugt ist.</summary>
    public static IntPtr Hwnd(this Window window) => new WindowInteropHelper(window).Handle;

    /// <summary>Arbeitsbereich des Monitors, auf dem dieses Fenster liegt.</summary>
    public static DockRect WorkArea(this Window window) =>
        Win32Screen.WorkAreaForWindow(window.Hwnd(), window.DpiScale());

    /// <summary>Zeigerposition in denselben Einheiten wie <c>Window.Left</c>/<c>Top</c>.</summary>
    public static (double X, double Y) CursorPosition(this Window window) =>
        Win32Screen.CursorPosition(window.DpiScale());
}
