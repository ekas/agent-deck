using System.Globalization;

namespace AgentDeck.Core;

/// <summary>
/// Farbrechnung auf Hex-Zeichenketten – portiert aus <c>canvas_kit.hex_to_rgb</c> und
/// <c>canvas_kit.mix</c>.
///
/// Bewusst auf Hex statt auf einem Farbtyp: <c>Core</c> darf WPF nicht kennen, und die
/// Palette liegt ohnehin als Hex vor. Die Oberfläche wandelt am Ende einmal um.
/// </summary>
public static class ColorMath
{
    /// <summary>Hex (<c>#rrggbb</c> oder <c>rrggbb</c>) in seine Kanäle zerlegen.</summary>
    public static (int R, int G, int B) HexToRgb(string hex)
    {
        var s = hex.TrimStart('#');
        return (int.Parse(s[..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture),
                int.Parse(s[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture),
                int.Parse(s[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture));
    }

    /// <summary>
    /// Zwei Hexfarben linear mischen: <c>t=0</c> -> <paramref name="c1"/>,
    /// <c>t=1</c> -> <paramref name="c2"/>.
    ///
    /// Gerundet wird kaufmännisch-symmetrisch (<see cref="MidpointRounding.ToEven"/>) –
    /// das ist Pythons <c>round()</c> und damit dieselbe Farbe wie in der
    /// Python-Fassung. Mit der C#-Vorgabe für <c>ToString</c>-Rundung käme bei
    /// exakt .5 ein anderer Kanalwert heraus.
    /// </summary>
    public static string Mix(string c1, string c2, double t)
    {
        t = Math.Clamp(t, 0.0, 1.0);
        var a = HexToRgb(c1);
        var b = HexToRgb(c2);

        return "#" + Channel(a.R, b.R) + Channel(a.G, b.G) + Channel(a.B, b.B);

        string Channel(int from, int to) =>
            ((int)Math.Round(from + (to - from) * t, MidpointRounding.ToEven))
            .ToString("x2", CultureInfo.InvariantCulture);
    }
}
