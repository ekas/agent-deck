"""render/kit: Farb- und Textrechnerei ohne Canvas.
"""

import helpers  # setzt sys.path und die Deck-Sprache

from deck.render import kit as ck


def test_color_helpers():
    assert ck.hex_to_rgb("#ffffff") == (255, 255, 255)
    assert ck.hex_to_rgb("#010203") == (1, 2, 3)
    assert ck.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert ck.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert ck.mix("#000000", "#ffffff", 2.0) == "#ffffff"            # ueber 1 klemmt
    assert ck.short_model("Opus 5 (1M context)") == "Opus 5 (1M)"
    assert ck.short_model(None) == "—"


def test_plus_liegt_symmetrisch_auf_ganzen_pixeln():
    # Achse und Arm muessen ganzzahlig herauskommen: der tk-Canvas antialiast nicht,
    # eine Linie liegt nur dann symmetrisch um ihre Achse, wenn beide auf dem Raster
    # sitzen. (Die Kachel-Mitte kommt bei Skalierung fast immer gebrochen herein.)
    ax, ay, arm, w = ck.plus_geom(55.5, 69.0, 8.1, 3.3)
    assert (ax, ay) == (56.0, 69.0)
    assert (arm, w) == (8, 3)
    assert float(ax).is_integer() and float(ay).is_integer()

    # .5 rundet immer nach oben – nicht wie round() zur geraden Zahl, sonst haengt
    # die Verschiebung davon ab, wo die Kachel gerade steht.
    assert ck.plus_geom(10.5, 11.5, 5, 3)[:2] == (11.0, 12.0)

    # Strich und Arm bleiben sichtbar, auch wenn klein gerechnet wird
    assert ck.plus_geom(0, 0, 0.1, 0.1)[2:] == (1, 1)
