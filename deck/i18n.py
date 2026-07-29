"""Sprachumschaltung fuer die DECK-eigene Oberflaeche (nicht die Agenten selbst).

Quelle ist DIESELBE Einstellung wie die Antwortsprache der Agenten:
`claude_settings.read_values()["language"]` (Dropdown "Sprache" im Einstellungs-
Dialog -> ~/.claude/settings.json). So schaltet EIN Regler alles gemeinsam um:
die Sprache, in der die Agenten antworten, UND die Sprache der Deck-Oberflaeche
inkl. der Hover-Kurzzusammenfassung. Fehlt/unbekannt -> Deutsch (die Herkunfts-
sprache des Decks).

`L(de, en)` ist der Inline-Helfer: jeder sichtbare Text steht zweisprachig direkt
am Aufrufort (kein Key-Katalog, keine fehlenden Keys, die deutsche Herkunftsfassung
bleibt lesbar). `current()` ist gecacht; nach dem Speichern der Einstellungen einmal
`refresh()` aufrufen, dann greift die neue Sprache bei der naechsten Anzeige (voll
durchgaengig nach einem Deck-Neustart). Reine stdlib + claude_settings -> von
ueberall (auch aus den Nebenmodulen) gefahrlos importierbar.
"""
from deck.claude import settings as cset

GERMAN = "german"
ENGLISH = "english"

_lang = None   # gecachte, normalisierte Sprache (None = noch nicht gelesen)


def normalize(value):
    """Roh-Wert aus settings.json -> "english"/"german". Alles Unbekannte/Fehlende
    faellt auf Deutsch zurueck (Herkunftssprache des Decks)."""
    s = (value or "").strip().lower()
    if s.startswith("en"):
        return ENGLISH
    return GERMAN


def refresh():
    """Sprache aus settings.json neu einlesen und cachen. Nach dem Speichern der
    Einstellungen aufrufen. Nie eine Exception (Datei fehlt/kaputt -> Deutsch)."""
    global _lang
    try:
        _lang = normalize(cset.read_values().get("language"))
    except Exception:
        _lang = GERMAN
    return _lang


def current():
    """Aktuelle Deck-Sprache ("english"/"german"); liest beim ersten Mal lazy ein."""
    return _lang if _lang is not None else refresh()


def is_english():
    return current() == ENGLISH


def L(de, en):
    """Zweisprachiger Inline-Text: engl. Fassung bei Sprache=Englisch, sonst deutsch."""
    return en if current() == ENGLISH else de
