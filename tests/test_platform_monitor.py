"""monitor: Tooltip und Dialog bleiben auf dem Monitor unter dem Anker -
geklappt um den Anker, nicht geschoben.
"""

import helpers  # noqa: F401 - Import MIT Absicht: legt die Repo-Wurzel auf den

# sys.path und nagelt die Deck-Sprache auf Deutsch.
from deck.platform import monitor as sf

# Arbeitsflaeche wie ein 1920x1080-Schirm mit 40 px Taskleiste unten.
_AREA = (0, 0, 1920, 1040)


def test_fit_prefers_the_offset_position():
    """Solange Platz ist, sitzt das Fenster genau dort, wo es gedacht ist:
    Anker + Versatz. Kein Klemmen, kein Klappen."""
    assert sf.fit(500, 400, 300, 120, _AREA, dx=14, dy=18) == (514, 418)


def test_fit_flips_around_the_anchor_at_the_edges():
    """Am rechten/unteren Rand klappt das Fenster auf die ANDERE Seite des Ankers –
    beim rechts angedockten Deck ist genau das der Normalfall. Es wird NICHT nur an
    den Rand geschoben: dort laege der Tooltip unter dem Mauszeiger und verdeckte die
    Kachel, auf die er sich bezieht."""
    assert sf.fit(1900, 400, 300, 120, _AREA, dx=14, dy=18) == (1900 - 14 - 300, 418)
    assert sf.fit(500, 1030, 300, 120, _AREA, dx=14, dy=18) == (514, 1030 - 18 - 120)
    x, y = sf.fit(1900, 1030, 300, 120, _AREA, dx=14, dy=18)   # Ecke: beide Achsen
    assert (x, y) == (1586, 892)


def test_fit_clamps_when_flipping_does_not_help_either():
    """Passt es auch gespiegelt nicht (Fenster fast so breit wie der Schirm, Anker
    mittig), wird geklemmt – und zwar so, dass die linke/obere Kante sichtbar bleibt:
    dort sitzen Titel und Beschriftungen."""
    assert sf.fit(1000, 500, 1900, 1000, _AREA, dx=30, dy=60) == (20, 40)
    # Groesser als der Schirm -> Anschlag links/oben, nicht ins Negative.
    assert sf.fit(1000, 500, 3000, 2000, _AREA, dx=30, dy=60) == (0, 0)


def test_fit_never_leaves_the_work_area_on_a_grid_of_anchors():
    """Rundumprobe: fuer jede Anker-Position auf dem Schirm liegt das Fenster
    vollstaendig in der Arbeitsflaeche (solange es hineinpasst) – auch mit negativen
    Koordinaten, wie sie ein Monitor LINKS des Hauptschirms hat."""
    for area in (_AREA, (-1920, 232, -384, 1144)):
        left, top, right, bottom = area
        for ax in range(left, right + 1, 97):
            for ay in range(top, bottom + 1, 61):
                x, y = sf.fit(ax, ay, 320, 140, area, dx=14, dy=18)
                assert left <= x and x + 320 <= right, (area, ax, ay, x)
                assert top <= y and y + 140 <= bottom, (area, ax, ay, y)


def test_fit_without_a_known_area_stays_unclamped():
    """Kein Windows/kein Monitor-Info -> work_area() liefert None. Dann wird BEWUSST
    nicht geklemmt: eine geratene Bildschirmgroesse (winfo_screenwidth = nur der
    Primaerschirm) zog den Tooltip auf dem zweiten Monitor auf den falschen Schirm."""
    assert sf.fit(2500, 900, 300, 120, None, dx=14, dy=18) == (2514, 918)


def test_work_area_is_a_plausible_rect_or_none():
    """work_area() darf nie halb Gares liefern: entweder ein echtes Rechteck oder
    None. Auf dem Entwicklungsrechner (Windows) kommt der Hauptmonitor."""
    a = sf.work_area(10, 10)
    assert a is None or (a[0] < a[2] and a[1] < a[3])
