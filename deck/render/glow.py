"""Die Bewegungs-Haelfte der Kachel-Records: ruhiger Status-Glow (Puls), weicher
Farbton-Crossfade der Kartenflaeche, kurzes Aufleuchten bei Statuswechsel (bloom)
und das taktile 'Press & Pop' beim Anklicken.

Wichtig zum Zusammenspiel mit dem Panel: refresh() setzt weiterhin nur die ZIELE
auf den Records (fill_target/glow_color/glow_intensity/glow_pulse/bloom); hier
wird je Frame dorthin geeast. `tiles` ist DASSELBE Dict wie im Panel – das Panel
ruft beim Neuzeichnen tiles.clear() statt es zu ersetzen, damit diese Referenz
gueltig bleibt. Der Farbton-/Statuswechsel aendert nur Farbe/Text, das Press&Pop
nur Koordinaten -> beide stoeren sich nicht.
"""
import math
import time
import tkinter as tk

from deck.render import card as cr
from deck.domain import config as cfg
from deck.render.kit import BG, CARD_FILL, INK_3, hex_to_rgb as _hex_to_rgb, mix as _mix

# Timing kommt zentral aus der config (Fallback = bisherige Werte).
ANIM_MS = getattr(cfg, "ANIM_MS", 55)
FILL_EASE = getattr(cfg, "FILL_EASE", 0.30)
BLOOM_DECAY = getattr(cfg, "BLOOM_DECAY", 0.82)
# Fade der 3 Glow-Ringe (innen -> außen): Anteil, um den zur BG-Farbe gemischt
# wird. Innerster Ring am kräftigsten, äußerster fast unsichtbar -> weicher Halo.
GLOW_RINGS = (0.30, 0.58, 0.80)

# Press&Pop-Kurve (frueher un-benannte Zahlen in _select_press): eindruecken auf
# DIP (95.5 %), zurueckfedern und ueberschwingen auf POP (102.5 %), dann in drei
# Phasen sauber auf 1.0 ausschwingen. DUR = Gesamtdauer in Sekunden.
_PRESS_DUR = 0.28
_PRESS_DIP, _PRESS_POP = 0.955, 1.025
_PHASE_IN, _PHASE_BACK = 0.32, 0.64   # Grenzen der drei Easing-Phasen (0..1)

# Glow-Surge-Kurve (Klick-Feedback, Effekt 02 "Glow Surge"): beim Auswählen läuft
# b = sin(pi*p) über _SURGE_DUR (0 -> Peak in der Mitte -> 0). b hebt kurz die
# Ring-Intensität (Surge-Term in apply_glow) und blendet die Auswahl-Kante von Weiß
# zur Cyan-Akzentfarbe und etwas dicker. _EFF_MAX = Deckel der Gesamt-Intensität,
# damit der äußerste Ring auch am Peak weich bleibt (wie in der Klick-Effekt-Vorschau).
_SURGE_DUR = 0.40
_SURGE_GAIN = 1.3
_EFF_MAX = 2.4
_SURGE_ACCENT = "#7ecbff"   # Kanten-Blitz -> Cyan (= Denk-/Auswahl-Akzent)
_SURGE_BORDER = "#ffffff"   # Ruhefarbe der Kante der gewählten Kachel (spiegelt SEL_BORDER)

# Frueher stand hier Effekt 03 ("Kopf-Schimmer"): ein Lichtband wanderte ueber die
# zeichenweise gezeichneten Repo-Namen. BEWUSST ENTFERNT (2026-07-28): das Band lief
# in Wort-Anteilen und ruckte darum je nach Namenslaenge um 0.3-1.8 Zeichen pro Frame
# weiter (Sprung bis 43 % der Effektstaerke in 55 ms) – das las sich als vertikale
# Pixelkante, die ueber die Namen wischt und die Buchstaben verschiebt.


class GlowAnimator:
    def __init__(self, root, canvas, tiles):
        self.root = root
        self.canvas = canvas      # der Deck-Canvas (stabil, wird nie ersetzt)
        self.tiles = tiles        # dasselbe Dict wie im Panel (tiles.clear(), nie neu)
        self._pulse_i = 0         # Zähler für das langsame Atmen der Status-Glows
        self._paused = 0          # >0: Tick läuft leer (siehe pause())

    def start(self):
        """Den schnellen Animations-Timer starten (laeuft dann selbst weiter)."""
        self._tick()

    def pause(self):
        """Das Faden/Atmen kurz stilllegen – der Timer läuft weiter, sein teurer
        Rumpf aber nicht.

        Gedacht für die Slide-Animation des Edge-Docks: die will alle ~8 ms einen
        Frame, dieser Tick hier rendert im Bildmodus ganze Kachelbilder (Pillow)
        und braucht dafür gern mehr Zeit, als zwischen zwei Slide-Frames liegt.
        Tk ist einthreadig – der Slide muss also warten und ruckelt. Für die ~170 ms
        eines Slides fällt das ausgesetzte Faden nicht auf; die Kacheln sind
        währenddessen ohnehin gerade erst am Erscheinen.

        Gezählt statt boolesch, damit sich überlappende Aufrufer (Auf- und
        gleich wieder Zuklappen) nicht gegenseitig die Pause beenden."""
        self._paused += 1

    def resume(self):
        if self._paused > 0:
            self._paused -= 1

    def pulse_factor(self):
        """Sanftes Atmen 0.60..1.00 (Cosinus) für pulsierende Status."""
        n = 42                                   # Ticks je Atemzug (~2.3 s bei 55 ms)
        ang = 2 * math.pi * (self._pulse_i % n) / n
        return 0.60 + 0.40 * (0.5 - 0.5 * math.cos(ang))

    def _stale(self, slot, ids):
        """Gehoert dieser Record noch zur GEZEICHNETEN Kachel?

        Nach einem Neuaufbau (Panel: canvas.delete('all') + tiles.clear() +
        _draw_tile) liegt unter dem Slot ein NEUER Record, und das Gruppen-Tag
        'g_<slot>' traegt frische Items bei Maszstab 1.0. Ein noch laufender
        press/surge-Timer haelt aber den ALTEN Record in seiner Closure – machte
        er weiter, skalierte sein abschliessendes 'auf 1 einrasten' die frischen
        Items um genau seinen Maszstab auseinander, und der Kachel-Text stand
        danach dauerhaft schief (bis zum naechsten Redraw)."""
        return self.tiles.get(slot) is not ids

    def set_border(self, ids, color, width):
        """Kantenfarbe/-breite der Kachel setzen – die EINE Stelle dafür (auch das
        Panel geht in _update_tiles hier durch).

        Im Bildmodus wandert die Kante ins Bild (sie wird dort mit derselben
        weichen Rundung gerendert wie die Fläche), im Fallback bleibt es das
        Polygon-Outline. Rückgabe False, wenn die Kachel gerade weg ist."""
        ids["border"], ids["border_w"] = color, width
        if ids.get("rect"):
            try:
                self.canvas.itemconfig(ids["rect"], outline=color,
                                       width=max(1, int(round(width))))
            except tk.TclError:
                return False
        return True

    def effective_glow(self, ids, factor):
        """Leuchtkraft dieser Kachel im aktuellen Frame: Ruhe-Intensität (ggf.
        atmend), plus Statuswechsel-Bloom, plus Klick-Surge. _EFF_MAX deckelt, damit
        der äußere Halo auch am Spitzenwert weich bleibt."""
        base = ids.get("glow_intensity", 0.0) * (factor if ids.get("glow_pulse") else 1.0)
        eff = base + ids.get("bloom", 0.0) + ids.get("surge", 0.0)
        return _EFF_MAX if eff > _EFF_MAX else eff

    def apply_glow(self, slot, factor):
        """Fläche, Kante und Halo einer Karte auf den Ist-Zustand ihres Records
        bringen. `factor` = Puls.

        Zwei Wege, je nachdem was die Kachel beim Zeichnen bekommen hat:
          • Bild (Regelfall): EIN gerendertes PhotoImage trägt Halo, Fläche und
            Kante zusammen – weiche Rundung und echter Verlauf, weil Tk-Canvas
            selbst kein Antialiasing kann (card_render.py).
          • Ringe (Fallback ohne Pillow / bei durchsichtigem Fenster): die
            bisherigen drei Ring-Umrisse, von der Statusfarbe nach BG verblassend."""
        ids = self.tiles.get(slot)
        if not ids:
            return
        color = ids.get("glow_color", INK_3)
        eff = self.effective_glow(ids, factor)
        if ids.get("img"):
            self._paint_image_tile(ids, color, eff)
            return
        if "rings" not in ids:
            return
        try:
            for ring, ringbase in zip(ids["rings"], GLOW_RINGS):
                toward_bg = 1 - (1 - ringbase) * eff   # eff>1 -> _mix klemmt auf Vollfarbe
                self.canvas.itemconfig(ring, outline=_mix(color, BG, toward_bg))
        except tk.TclError:
            pass                                 # Kachel gerade neu gezeichnet -> egal

    def _paint_image_tile(self, ids, color, eff):
        """Das Kachelbild neu anfordern und setzen – aber nur, wenn sich wirklich
        etwas geändert hat (sonst liefe je Frame ein itemconfig für ein identisches
        Bild). Press & Pop skaliert die Kachel: dessen Maßstab geht in die Bildgröße
        ein, grob gerastert, damit nicht jeder Zwischenschritt eine neue Maske
        anlegt.

        Die zurückgegebene Bildreferenz landet BEWUSST im Record: der Canvas hält
        keine, und der Bildcache kann den Eintrag jederzeit verdrängen – ohne diese
        Referenz verschwände die Kachel."""
        w, h, r, pad = ids["geom"]
        ps = ids.get("press_scale", 1.0)
        ps = round(ps * 40) / 40 if abs(ps - 1.0) > 0.005 else 1.0
        if ps != 1.0:
            w, h, r, pad = (max(1, int(round(v * ps))) for v in (w, h, r, pad))
        key = (w, h, r, pad, ids.get("fill_hex"), color, round(eff, 2),
               ids.get("border"), ids.get("border_w", 1))
        if key == ids.get("img_key"):
            return
        photo = cr.tile_photo(w, h, r, pad, ids.get("fill_hex", CARD_FILL), color,
                              eff, ids.get("border", CARD_FILL),
                              ids.get("border_w", 1))
        if photo is None:
            return
        try:
            self.canvas.itemconfig(ids["img"], image=photo)
        except tk.TclError:
            return                               # Kachel gerade neu gezeichnet -> egal
        ids["photo"] = photo                     # Referenz halten (siehe Docstring)
        ids["img_key"] = key

    def _tick(self):
        """Schneller Timer für alle Bewegung: die Kartenfläche weich in ihre
        Ziel-Tönung faden (FILL_EASE/Frame), den Glow atmen lassen und das
        Statuswechsel-Aufleuchten (bloom) abklingen. Fertig eingefadete, ruhige
        Karten werden übersprungen (kein unnötiges Neuzeichnen/Flackern)."""
        if self._paused:
            # Pausiert (Dock-Slide läuft): den Takt halten, aber nichts rendern.
            # _pulse_i bleibt stehen, damit das Atmen nach der Pause dort
            # weitergeht, wo es aufgehört hat – ein Sprung wäre sichtbar.
            self.root.after(ANIM_MS, self._tick)
            return
        self._pulse_i += 1
        f = self.pulse_factor()
        c = self.canvas
        try:
            for slot, ids in list(self.tiles.items()):
                if "fill_rgb" not in ids:
                    continue
                cur = ids["fill_rgb"]
                tgt = _hex_to_rgb(ids.get("fill_target", CARD_FILL))
                dr, dg, db = tgt[0] - cur[0], tgt[1] - cur[1], tgt[2] - cur[2]
                moving = abs(dr) + abs(dg) + abs(db) > 1.5
                bloom = ids.get("bloom", 0.0)
                if not moving and bloom < 0.01 and not ids.get("glow_pulse"):
                    continue                     # nichts mehr zu tun -> ruhen lassen
                if moving:
                    cur[0] += dr * FILL_EASE
                    cur[1] += dg * FILL_EASE
                    cur[2] += db * FILL_EASE
                else:
                    cur[0], cur[1], cur[2] = float(tgt[0]), float(tgt[1]), float(tgt[2])
                hexf = "#%02x%02x%02x" % (round(cur[0]), round(cur[1]), round(cur[2]))
                if hexf != ids.get("fill_hex"):
                    ids["fill_hex"] = hexf
                    if ids.get("rect"):          # Polygon-Fallback: Fläche direkt
                        c.itemconfig(ids["rect"], fill=hexf)
                ids["bloom"] = bloom * BLOOM_DECAY if bloom >= 0.01 else 0.0
                # Im Bildmodus zieht apply_glow die neue Füllfarbe mit ins Bild.
                self.apply_glow(slot, f)
        except tk.TclError:
            pass
        self.root.after(ANIM_MS, self._tick)

    def press(self, slot):
        """Auswahl-Feedback beim Klick: 'Press & Pop' – die ganze Kachel drückt sich
        kurz ein und federt mit leichtem Überschwingen zurück (taktil wie eine Taste).
        Skaliert ALLE Items der Kachel als Gruppe (gtag) um ihr Zentrum. Das Glow-Feedback
        beim Klick liegt in surge() (Effekt 02 'Glow Surge'); press() ist rein die
        Skalierung. Läuft eigenständig und stört die Farb-/Statusanimation nicht (die
        ändern Farbe/Text, nicht Koordinaten).

        Hinweis: tkinters canvas.scale skaliert Koordinaten, nicht Schriftgrößen –
        darum bewusst kleiner Hub (~4-5 %), damit der Text sauber mitgeht statt zu zappeln."""
        ids = self.tiles.get(slot)
        if not ids or "gtag" not in ids:
            return
        c = self.canvas
        box = c.bbox(ids.get("rect") or ids.get("img"))
        if not box:
            return
        gtag = ids["gtag"]
        # Laufenden Press abbrechen und exakt auf Maßstab 1 zurücksetzen (kein Drift).
        if ids.get("press_job"):
            try:
                self.root.after_cancel(ids["press_job"])
            except Exception:
                pass
            ids["press_job"] = None
        prev = ids.get("press_scale", 1.0)
        if prev != 1.0:
            try:
                c.scale(gtag, ids["press_cx"], ids["press_cy"], 1.0 / prev, 1.0 / prev)
            except tk.TclError:
                pass
            ids["press_scale"] = 1.0
        # Zentrum ist skalierungs-invariant (wir skalieren immer um es herum).
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        ids["press_cx"], ids["press_cy"] = cx, cy

        def s_of(p):
            eo = lambda q: 1 - (1 - q) ** 3            # ease-out
            eio = lambda q: q * q * (3 - 2 * q)        # smoothstep
            if p < _PHASE_IN:                          # 1) eindrücken
                return 1 - (1 - _PRESS_DIP) * eo(p / _PHASE_IN)
            if p < _PHASE_BACK:                        # 2) zurück & überschwingen
                return _PRESS_DIP + (_PRESS_POP - _PRESS_DIP) * eio(
                    (p - _PHASE_IN) / (_PHASE_BACK - _PHASE_IN))
            return _PRESS_POP + (1.0 - _PRESS_POP) * eio(
                (p - _PHASE_BACK) / (1.0 - _PHASE_BACK))   # 3) ausschwingen -> 1.0

        start = time.time()

        def step():
            try:
                if not c.winfo_exists() or self._stale(slot, ids):
                    ids["press_job"] = None
                    return
                p = (time.time() - start) / _PRESS_DUR
                pv = ids.get("press_scale", 1.0)
                if p >= 1.0:
                    if pv != 1.0:
                        c.scale(gtag, cx, cy, 1.0 / pv, 1.0 / pv)   # exakt auf 1 einrasten
                    ids["press_scale"] = 1.0
                    self.apply_glow(slot, self.pulse_factor())      # Bild wieder 1:1
                    ids["press_job"] = None
                    return
                s = s_of(p)
                c.scale(gtag, cx, cy, s / pv, s / pv)
                ids["press_scale"] = s
                # canvas.scale verschiebt nur den Bild-ANKER; die Bildgröße muss
                # eigens mitgehen, sonst bliebe die Fläche stehen, während Text und
                # Kante zoomen. apply_glow rendert sie in der Press-Größe.
                self.apply_glow(slot, self.pulse_factor())
                ids["press_job"] = self.root.after(16, step)
            except tk.TclError:
                ids["press_job"] = None            # Kachel neu gezeichnet -> Press egal
        step()

    def surge(self, slot):
        """Auswahl-Feedback beim Klick: 'Glow Surge' (Effekt 02) – der Status-Halo
        schwillt kurz stark an und die Kante blitzt cyan auf. Ein Wert b = sin(pi*p)
        läuft über _SURGE_DUR (0 -> Peak in der Mitte -> 0); er hebt die Ring-Intensität
        (surge-Term in apply_glow) und blendet die Kante von der Auswahl-Weiß zur
        Cyan-Akzentfarbe (und minimal dicker). Nutzt den vorhandenen Glow – die Farbe
        des Halos bleibt die Statusfarbe, nur heller.

        Läuft eigenständig über einen eigenen 16-ms-Timer (wie press()) und stört die
        Farb-/Press-Animation nicht: press() ändert Koordinaten, refresh() die Ziele,
        surge() nur Ring-Outline + Kante. Annahme: die angeklickte Kachel ist die
        gerade AUSGEWÄHLTE (focus_slot setzt active_slot davor) -> Ruhefarbe der Kante
        ist das Auswahl-Weiß (_SURGE_BORDER)."""
        ids = self.tiles.get(slot)
        if not ids or not (ids.get("rings") or ids.get("img")):
            return
        c = self.canvas
        # Laufenden Surge abbrechen und sauber zurücksetzen (kein Rest-Boost/-Blitz).
        if ids.get("surge_job"):
            try:
                self.root.after_cancel(ids["surge_job"])
            except Exception:
                pass
            ids["surge_job"] = None
        # Auswahl-Kante SOFORT weiß setzen (nicht erst beim nächsten refresh) -> die
        # Selektion ist unmittelbar spürbar, der Blitz baut darauf auf. Die Kante
        # liegt im Record; das Malen (Bild ODER Polygon) macht _set_border.
        if not self.set_border(ids, _SURGE_BORDER, 2):
            return
        start = time.time()

        def step():
            try:
                if not c.winfo_exists() or self._stale(slot, ids):
                    ids["surge_job"] = None
                    return
                p = (time.time() - start) / _SURGE_DUR
                if p >= 1.0:
                    ids["surge"] = 0.0
                    self.set_border(ids, _SURGE_BORDER, 2)               # Kante zurück
                    self.apply_glow(slot, self.pulse_factor())            # Halo zurück
                    ids["surge_job"] = None
                    return
                b = math.sin(math.pi * p)
                ids["surge"] = _SURGE_GAIN * b
                self.set_border(ids, _mix(_SURGE_BORDER, _SURGE_ACCENT, b), 2 + b)
                self.apply_glow(slot, self.pulse_factor())
                ids["surge_job"] = self.root.after(16, step)
            except tk.TclError:
                ids["surge"] = 0.0
                ids["surge_job"] = None            # Kachel neu gezeichnet -> Surge egal
        step()
