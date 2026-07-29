"""Die Teile, die aus den Mixins herausgelöst wurden, sind ohne Panel benutzbar.

Das ist der eigentliche Gewinn der Umstellung, und er ist nur hier prüfbar: ein Mixin
kann man nicht einzeln bauen — es braucht die ganze Klasse und damit ein Tk-Fenster,
einen Broker, einen BindStore. Ein Kollaborateur nennt seine Abhängigkeiten im
Konstruktor, also lässt er sich mit Attrappen aufbauen.

Geprüft wird bewusst die SIGNATUR und die Konstruktion, nicht das Zeichnen: sobald
show() läuft, ist ein echtes Tk-Fenster im Spiel, und das gehört in den Sichttest.
"""
import inspect

import helpers  # noqa: F401 - setzt sys.path und die Deck-Sprache

from deck.ui.settings_dialog import SettingsDialog


def test_settings_dialog_nennt_seine_abhaengigkeiten_im_konstruktor():
    """Vorher stand nirgends, was der Dialog anfasst - man musste die 375 Zeilen von
    AgentDeck lesen. Jetzt steht es in der Signatur, und dieser Test hält es fest."""
    sig = inspect.signature(SettingsDialog.__init__)
    params = [p for p in sig.parameters if p != "self"]
    assert params == ["root", "settings", "store", "dock",
                      "set_modal", "restart", "place"], params
    # Die drei Rückrufe sind keyword-only: beim Aufruf steht damit am Aufrufort, WAS
    # verdrahtet wird, statt vier gleich aussehender Positionsargumente.
    kwonly = [p for p, v in sig.parameters.items()
              if v.kind is inspect.Parameter.KEYWORD_ONLY]
    assert kwonly == ["set_modal", "restart", "place"], kwonly


def test_settings_dialog_baut_ohne_panel():
    """Konstruktion mit Attrappen - kein Tk, kein Broker, kein BindStore.

    Genau das war als Mixin unmöglich: die Methode hing an einer Klasse, die im
    __init__ einen Broker startet, ein Tk-Fenster aufbaut und die DPI-Anmeldung macht.
    """
    gerufen = []
    dlg = SettingsDialog(
        root=None,
        settings={"glow": False, "jira_prefix": "PROJ"},
        store=type("S", (), {"save_settings": lambda self: gerufen.append("save")})(),
        dock=None,
        set_modal=lambda v: gerufen.append(("modal", v)),
        restart=lambda: gerufen.append("restart"),
        place=lambda w: gerufen.append("place"),
    )
    assert dlg.settings["jira_prefix"] == "PROJ"
    # Die Rückrufe liegen als Attribute bereit und sind aufrufbar, ohne dass ein Panel
    # existiert - der Dialog kann also isoliert geprüft werden.
    dlg._set_modal(True)
    dlg.restart()
    dlg.store.save_settings()
    assert gerufen == [("modal", True), "restart", "save"]


def test_kein_mixin_mehr_fuer_den_dialog():
    """AgentDeck mischt den Dialog nicht mehr ein, sondern baut ihn.

    Sonst bliebe die alte Kopplung bestehen und niemand würde es merken: ein
    zusätzliches Mixin in der Vererbungsliste fällt nicht auf.
    """
    from deck.ui.panel import AgentDeck
    namen = [b.__name__ for b in AgentDeck.__bases__]
    assert "SettingsMixin" not in namen, namen
    assert not any("Settings" in n for n in namen), namen
    # Der Einstieg bleibt aber erhalten - die Bottom-Bar hängt ihren ⚙-Knopf daran.
    assert callable(AgentDeck._open_settings)
