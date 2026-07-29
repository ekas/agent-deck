"""Kacheln aufbauen und zeichnen: eine je Claude-Terminal, dazu die „+“-Kachel.

Die Kachelliste wird IN PLACE aktualisiert (siehe _carry_tile_anim) - ein
Vollneubau mit delete('all') setzt Farbe und Statuswert zurück, und dann blitzen
beim Auf- und Zuklappen alle Kacheln neu auf.
"""
import tkinter as tk

from deck import i18n
from deck.domain import config as cfg
from deck.platform import dpi
from deck.render import card as cr
from deck.render import kit as ck
from deck.render.glow import GLOW_RINGS
from deck.render.kit import BG
from deck.render.kit import CARD_BORDER
from deck.render.kit import CARD_FILL
from deck.render.kit import INK
from deck.render.kit import INK_2
from deck.render.kit import INK_3
from deck.render.kit import hex_to_rgb as _hex_to_rgb

from deck.ui.theme import LOST_GLOW, RAIL_IDLE, WINDOWS


class TilesMixin:
    """Wird in AgentDeck eingemischt (siehe panel.py)."""

    def _ordered_slots(self, w):
        """Die Slots dieses Fensters in der vom Nutzer gewaehlten Reihenfolge (Drag&Drop).
        Basis ist die von der Extension gemeldete Liste (broker.terminals); die
        gespeicherte Reihenfolge (self.order) wird darueber gelegt, neue/unbekannte
        Slots haengen hinten in Melde-Reihenfolge an. So bestimmt allein das Deck die
        Anordnung – VS Code gibt die visuelle Pane-Reihenfolge nicht preis, also kann
        sie nicht gespiegelt, wohl aber hier frei getauscht werden."""
        live = self.broker.terminals(w)
        live_set = set(live)
        saved = [s for s in self.order.get(w, []) if s in live_set]
        seen = set(saved)
        return saved + [s for s in live if s not in seen]

    def _layout_sig(self):
        """Signatur des gewuenschten Layouts – nur bei Aenderung neu zeichnen. Nutzt die
        vom Nutzer gewaehlte Reihenfolge, damit ein Umsortieren einen Redraw ausloest."""
        return tuple(
            (w, self.bindings.get(w), self.broker.connected(w),
             tuple(self._ordered_slots(w)) if self.broker.connected(w) else ())
            for w in WINDOWS
        )

    def _render_agents(self):
        """Pro verbundenem Fenster ein Block: kleiner Repo-Name als Kopf, darunter die
        Agenten-Kacheln (die schlanke, skalierende Ansicht in _render_agents_slim).
        Inhalt aendert sich (Agent/Fenster zu ODER auf): AKTUELLEN Zoom halten und das
        Fenster an den neuen Inhalt anpassen -> rechte/untere Kante schliessen auf (statt
        den Rest in ein fixes Fenster hochzuskalieren). Manuelles Ziehen laeuft nicht hier,
        sondern ueber _on_deck_configure (das skaliert in ein fixes Fenster)."""
        self._render_agents_slim(scale=self._slim_scale)
        self._fit_slim_window(self._slim_scale)

    # Slim-Layout in DESIGN-Einheiten (Faktor 1.0). Beim Zeichnen wird alles mit dem
    # Fit-Faktor multipliziert -> beim Verkleinern wird alles kleiner statt abgeschnitten.
    _SLIM_W, _SLIM_H, _SLIM_GAP, _SLIM_R, _SLIM_X0 = 148, 52, 10, 12, 12
    _SLIM_ADD_W = 34            # Breite der Geister-＋-Klickflaeche am Reihenende (Design-Einheiten)
    # Vertikale Gliederung der Repo-Bloecke. Diese vier Zahlen sind das, was
    # Zugehoerigkeit ueberhaupt erst lesbar macht, darum stehen sie beisammen:
    # der Glow-Halo ragt RING (= len(GLOW_RINGS)*2) ueber die Kachel hinaus, die
    # SICHTBARE Luft ist also immer der Abstand MINUS RING. Frueher war die Luft
    # unter dem Kopf 4 und ueber dem naechsten Kopf 6 – der Repo-Name stand damit
    # praktisch mittig zwischen der fremden Reihe darueber und seiner eigenen
    # darunter, und die Gruppierung war Auslegungssache. Jetzt 3 gegen 16.
    _SLIM_TOP, _SLIM_BOT = 6, 6      # Rand oben/unten
    _SLIM_HEAD_GAP  = 3              # sichtbare Luft Kopf -> EIGENE Kachelreihe
    _SLIM_BLOCK_GAP = 16             # sichtbare Luft ZWISCHEN zwei Repo-Bloecken
    _SLIM_RAIL_X, _SLIM_RAIL_W = 2, 2   # Schiene links: Abstand vom Canvasrand, Breite.
                                        # Bleibt links vom Halo (der beginnt bei X0-RING = 6).

    def _slim_extent(self):
        """Natuerliche (ungescalte) Ausdehnung des Slim-Layouts in Design-Einheiten –
        Basis fuer den Fit-Faktor. Spiegelt exakt die y-/x-Schritte von _render_agents_slim
        bei Faktor 1.0 (Name-Zeile, Kachelreihe inkl. Glow-Halo, Platzhalter). Misst den
        Fensternamen bei Design-Groesse 12 (Font kurz darauf gestellt) – in PIXELN, damit
        die Messung im selben Raum wie die uebrigen Design-Einheiten liegt und nicht mit
        `tk scaling` (also der Monitor-Skalierung) mitwandert.

        ACHTUNG: die y-Schritte hier und in _render_agents_slim MUESSEN gleich bleiben –
        laufen sie auseinander, skaliert das Deck gegen eine falsche natuerliche Groesse
        (Inhalt abgeschnitten oder Fenster zu gross)."""
        W, H, GAP, R, X0 = self._SLIM_W, self._SLIM_H, self._SLIM_GAP, self._SLIM_R, self._SLIM_X0
        nf = self._slim_name_font
        nf.configure(size=dpi.fontpx(12)[1])
        RING = len(GLOW_RINGS) * 2
        name_h = nf.metrics("linespace")
        y, maxx = self._SLIM_TOP, X0 + W
        shown = [w for w in WINDOWS if self.bindings.get(w) or self.broker.connected(w)]
        for i, w in enumerate(shown):
            if i:
                y += self._SLIM_BLOCK_GAP        # Luft zum vorigen Block
            repo = self.bindings.get(w) or f"{i18n.L('Fenster', 'Window')} {w}"
            maxx = max(maxx, X0 + nf.measure(repo))
            y += name_h + RING + self._SLIM_HEAD_GAP
            if self.broker.connected(w):
                x = X0
                for _slot in self.broker.terminals(w):
                    x += W + GAP
                    maxx = max(maxx, x - GAP)
                maxx = max(maxx, x + self._SLIM_ADD_W)   # Platz fuer das Geister-＋ am Reihenende
                y += H + RING                    # Blockende = Unterkante des Halos
            else:
                y += name_h
        y += self._SLIM_BOT
        if not shown:
            y += 26
        return maxx + X0, max(y, 40)

    def _render_agents_slim(self, scale=None):
        """Slim-Modus: pro verbundenem Fenster nur ein KLEINER Name (kein ⟳/✕/Punkt)
        und darunter die Agenten-Kacheln – kein Button-Raster, keine ＋-Kachel. Die
        Kacheln sind dieselben wie im Vollmodus (_draw_tile), Klick/Glow/Tooltip also
        unveraendert. So bleibt 'wirklich nur die Agenten' uebrig.

        Alles wird mit `scale` gezeichnet (Koordinaten, Offsets UND Font-Groessen), damit
        beim Verkleinern des Fensters alles kleiner wird statt abgeschnitten zu werden
        (tkinters canvas.scale wuerde Fonts NICHT mitnehmen -> darum echtes Neuzeichnen).
        scale=None -> aus aktueller Canvas-Flaeche und natuerlicher Groesse berechnen. Im
        Slim-Modus wird BEWUSST keine Canvas-Groesse gesetzt (das macht nur _seed_slim_size)."""
        c = self.deck
        self._hide_prompt_tip()
        # Anim-Zustand der aktuell gezeichneten Kacheln merken, BEVOR alles neu
        # aufgebaut wird: ueberlebende Slots (Fenster/Agent bleibt) sollen ihren
        # Farbton/Glow BEHALTEN, damit ein einzelnes Auf-/Zugehen nicht ALLE Kacheln
        # neu "aufleuchten" laesst (kein Farb-Refade, kein Bloom-Blitz -> kein Reload-Look).
        prev_tiles = dict(self.tiles)
        c.delete("all")
        self.tiles.clear()
        self.win_items.clear()      # Kopf-/Schienen-Items sterben mit dem delete('all')
        self._hot_win = None
        # 1) natuerliche Groesse ermitteln (Design-Einheiten) -> merken fuer den Fit-Handler.
        nat_w, nat_h = self._slim_extent()
        self._slim_nat = (nat_w, nat_h)
        if scale is None:
            scale = self._slim_fit_scale()
        self._slim_scale = scale
        s = scale
        # 2) skaliert zeichnen.
        W, H, GAP, R, X0 = (self._SLIM_W * s, self._SLIM_H * s, self._SLIM_GAP * s,
                            self._SLIM_R * s, self._SLIM_X0 * s)
        nf = self._slim_name_font
        # Pixelschrift (negative Groesse): folgt exakt dem Kachelraster, statt
        # zusaetzlich ueber `tk scaling` mit der Monitor-Skalierung zu wandern.
        nf.configure(size=dpi.fontpx(12, s)[1])
        RING = len(GLOW_RINGS) * 2 * s
        name_h = nf.metrics("linespace")
        small_font = dpi.fontpx(8, s)
        rail_x, rail_w = self._SLIM_RAIL_X * s, self._SLIM_RAIL_W * s
        y = self._SLIM_TOP * s
        shown = [w for w in WINDOWS if self.bindings.get(w) or self.broker.connected(w)]
        for i, w in enumerate(shown):
            if i:
                y += self._SLIM_BLOCK_GAP * s      # Luft zum vorigen Block
            y_top = y                              # Blockanfang – die Schiene beginnt hier
            repo = self.bindings.get(w) or f"{i18n.L('Fenster', 'Window')} {w}"
            connected = self.broker.connected(w)
            # EIN Text-Item je Name: verbunden hell, sonst gedimmt. (Frueher zeichenweise
            # fuer den Kopf-Schimmer – der ist raus, siehe glow_animator.)
            name = c.create_text(X0, y, anchor="nw", text=repo, font=nf,
                                 fill=INK if connected else INK_3)
            # Knapp gehalten: der Kopf soll an SEINER Reihe kleben. Der Halo braucht RING,
            # darueber bleiben _SLIM_HEAD_GAP sichtbare Luft (siehe Konstanten).
            y += name_h + RING + self._SLIM_HEAD_GAP * s
            if connected:
                x = X0
                for slot in self._ordered_slots(w):
                    self._draw_tile(c, slot, x, y, W, H, R, scale=s, step=W + GAP)
                    x += W + GAP
                # Geister-＋ am Reihenende: einziger Startweg im Slim-Modus (bewusst
                # klein/blass statt volle ＋-Kachel wie im Vollmodus).
                self._draw_slim_add(c, w, x, y, H, s)
                y += H + RING
            else:
                c.create_text(X0, y, anchor="nw",
                              text=i18n.L("— nicht verbunden —", "— not connected —"),
                              fill="#52525b", font=small_font)
                y += name_h
            # Schiene ZULETZT: erst jetzt steht die Unterkante des Blocks fest. Sie ist
            # der eigentliche Behaelter – Kopf und Kachelreihe haengen sichtbar an
            # derselben Linie, statt nur ungefaehr beieinander zu stehen.
            rail = c.create_rectangle(rail_x, y_top, rail_x + rail_w, y,
                                      fill=RAIL_IDLE, outline="")
            self.win_items[w] = {"name": name, "rail": rail, "connected": connected}
        if not shown:
            c.create_text(X0, y, anchor="nw", width=220 * s, fill="#52525b",
                          font=small_font,
                          text=i18n.L("Warte auf VS-Code-Fenster …", "Waiting for VS Code window …"))
        self._carry_tile_anim(prev_tiles)   # ueberlebende Kacheln erben ihren Zustand -> kein Reload-Blitz
        if self.active_slot and self.active_slot not in self.tiles:
            self.active_slot = None

    # Felder, die eine ueberlebende Kachel beim Neuaufbau erbt, damit sie optisch
    # RUHIG bleibt: die aktuell gefadete Fuellfarbe (fill_rgb/fill_hex), die Glow-
    # Ziele und – entscheidend – status_key. Ohne uebernommenen status_key haelte
    # _update_tiles jede Kachel faelschlich fuer "Statuswechsel" und zuendete bei
    # jedem Redraw einen bloom-Blitz. surge/press-Jobs werden NICHT geerbt: die
    # neue Kachel startet sauber im Ruhezustand (Defaults aus _draw_tile). Ihre
    # Timer laufen aber noch und tragen den ALTEN Record in der Closure – sie
    # duerfen die frischen Items nicht mehr anfassen; dafuer sorgt
    # GlowAnimator._stale (sonst stand der Kachel-Text danach schief).
    # border/border_w gehoeren dazu, seit die Kante im Bildmodus MITGERENDERT wird:
    # ohne sie faellt eine ausgewaehlte Kachel beim Neuaufbau fuer einen Frame auf
    # die Ruhekante zurueck (sichtbares Blinzeln der weissen Auswahl-Kante).
    _CARRY_FIELDS = ("fill_hex", "fill_target", "glow_color", "glow_intensity",
                     "glow_pulse", "status_key", "bloom", "border", "border_w")

    def _carry_tile_anim(self, prev):
        """Anim-Zustand ueberlebender Slots aus <prev> in die frisch gezeichneten
        Kacheln uebernehmen. Ein Slot, den es vorher NICHT gab (frisch per ＋ oder
        neu verbundenes Fenster), fehlt in <prev> -> er faedt bewusst normal ein.
        Die geerbte Fuellfarbe wird sofort auf die Flaeche gesetzt (sonst blitzt ein
        Frame CARD_FILL auf, bevor der Animator wieder eingreift)."""
        anim = getattr(self, "anim", None)
        for slot, ids in self.tiles.items():
            old = prev.get(slot)
            if not old:
                continue                      # frischer Agent -> normal einfaden
            for k in self._CARRY_FIELDS:
                if k in old:
                    ids[k] = old[k]
            ids["fill_rgb"] = list(old.get("fill_rgb") or ids["fill_rgb"])  # eigene Liste
            if ids.get("rect"):               # Polygon-Fallback: Flaeche direkt faerben
                try:
                    self.deck.itemconfig(ids["rect"], fill=ids["fill_hex"])
                except tk.TclError:
                    pass
            if anim:
                # Im Bildmodus malt das die geerbte Flaeche gleich mit.
                anim.apply_glow(slot, anim.pulse_factor())

    def _draw_slim_add(self, c, win, x, y, H, s):
        """Slim-Weg zum Starten eines Agenten (Mockup-Option #2 »Geister-＋ am Reihenende«):
        ein blasses, schmales ＋ hinter der letzten Kachel der Reihe – KEINE volle ＋-Kachel
        wie im Vollmodus, damit der ruhige Slim-Look bleibt. Die gefuellte (BG = im Ruhe-
        zustand unsichtbare) Box ist die Klickflaeche; Hover hellt das ＋ auf und blendet
        einen gestrichelten Rahmen ein. Klick -> create_agent(win): die Extension oeffnet
        EIN weiteres Claude-Terminal in genau diesem Fenster (Autofokus wie bei der Voll-
        Kachel). x kommt bereits um eine GAP hinter der letzten Kachel herein."""
        bw = self._SLIM_ADD_W * s
        bh = min(H, 34 * s)
        by = y + (H - bh) / 2
        box = ck.round_rect(c, x, by, x + bw, by + bh, 11 * s,
                            fill=BG, outline="", width=max(1, int(round(s))), dash=(3, 3))
        # Zwei Striche statt des Zeichens "＋": als Text sass das Plus 3,5 px zu tief
        # im Kaestchen (tk zentriert die Zeilenbox, das Glyph sitzt auf der Mathe-
        # Achse – Begruendung in ck.plus). Die Masse sind dem alten Glyph abgemessen,
        # damit sich am Aussehen sonst nichts aendert: 16 pt bold ergab bei s=1.5
        # 16 px Spannweite. Der Strich dort war senkrecht 4 px, waagerecht 3 px
        # (Hinting); gewaehlt sind 3 px – mit 4 px in BEIDE Richtungen wirkt das
        # Kreuz fetter als das Zeichen, und das Geister-＋ soll blass bleiben.
        plus = ck.plus(c, x + bw / 2, by + bh / 2, 5.4 * s, 2.2 * s, fill=INK_3)
        tag = "slimadd_" + win
        ptag = tag + "_plus"          # trifft beide Striche mit einem itemconfig
        for it in (box, *plus):
            c.addtag_withtag(tag, it)
        for it in plus:
            c.addtag_withtag(ptag, it)
        c.tag_bind(tag, "<Button-1>", lambda e, g=win: self.create_agent(g))
        c.tag_bind(tag, "<Enter>", lambda e, b=box, p=ptag:
                   (c.itemconfig(p, fill=INK), c.itemconfig(b, outline=CARD_BORDER),
                    c.configure(cursor="hand2")))
        c.tag_bind(tag, "<Leave>", lambda e, b=box, p=ptag:
                   (c.itemconfig(p, fill=INK_3), c.itemconfig(b, outline=""),
                    c.configure(cursor="")))

    def _draw_tile(self, c, slot, x, y, W, H, R, scale=1.0, step=None):
        # Frostpane-Karte: dunkle Graphitfläche, heller Text, ruhiger Status-GLOW
        # (weicher Halo ringsum). BEWUSST WEGGELASSEN (nach Vorgabe): der Leucht-
        # Streifen an der linken Kante und der Status-Punkt in der Ecke – den Status
        # trägt allein der Glow (+ betonte Kante bei Auswahl/Rückfrage).
        # Layout: Modell (oben links) · ✕ (oben rechts) · Effort (Zeile 2 rechts)
        # · Status (unten links) · Modus (unten rechts).
        # scale != 1.0 nur im Slim-Modus: x/y/W/H/R kommen bereits skaliert herein, die
        # internen Text-Offsets, Font-Groessen und Halo-Masse werden hier mit demselben
        # Faktor mitskaliert -> die ganze Kachel wird kleiner statt abgeschnitten.
        s = scale
        # Kachelschrift in PIXELN (dpi.fontpx): sie folgt damit exakt demselben
        # Faktor wie die Koordinaten. Eine Punktangabe wuerde zusaetzlich ueber
        # `tk scaling` mit der Monitor-Skalierung wachsen -> doppelt, und der Text
        # liefe aus der Karte.
        fs = lambda b, w=None: dpi.fontpx(b, s, weight=w)
        # Flaeche + Halo + Kante: EIN gerendertes Bild (weiche Rundung, echter
        # Verlauf) – Tk-Canvas selbst kann kein Antialiasing, seine Rundungen
        # treppen. Ohne Pillow ODER bei durchsichtigem Fenster (dort wuerde der
        # Bild-Hintergrund mit ausgestanzt) bleibt es beim bisherigen Weg:
        # Polygon + drei Ring-Umrisse.
        rings, rect, img = [], None, None
        geom = None
        if cr.AVAILABLE and not getattr(cfg, "TRANSPARENT_BG", False):
            pad = cr.pad_for(s)
            geom = (max(1, int(round(W))), max(1, int(round(H))),
                    max(1, int(round(R))), pad)
            img = c.create_image(x - pad, y - pad, anchor="nw")
        else:
            for i in range(len(GLOW_RINGS)):
                d = (i + 1) * 2 * s
                rings.append(ck.round_rect(c, x - d, y - d, x + W + d, y + H + d,
                                           R + d, fill="", outline=BG, width=2 * s))
            rect = ck.round_rect(c, x, y, x + W, y + H, R,
                                 fill=CARD_FILL, outline=CARD_BORDER, width=1)
        model = c.create_text(x + 11 * s, y + 12 * s, anchor="w", text="—", fill=INK,
                              font=fs(10, "bold"))
        effort = c.create_text(x + W - 10 * s, y + 27 * s, anchor="e", text="", fill=INK_2,
                               font=fs(8))
        # Ticket-ID (Zeile 2 links, dem Effort gegenueber): zugewiesenes Ticket dieses
        # Slots – unter dem Modell, ueber dem Status. Farbe/Text setzt _update_tiles je
        # Poll; ohne Ticket bleibt die Zeile leer (der frueher hier stehende "Ticket"-
        # Platzhalter war eine Klick-Aufforderung – die Zuweisung laeuft jetzt nur noch
        # ueber das Rechtsklick-Menue, darum keine Button-Attrappe mehr auf der Karte).
        ticket = c.create_text(x + 11 * s, y + 27 * s, anchor="w",
                               text="", fill=INK_3,
                               font=fs(8, "bold"))
        act = c.create_text(x + 11 * s, y + H - 11 * s, anchor="w", text="idle", fill=INK_2,
                            font=fs(8))
        mode = c.create_text(x + W - 10 * s, y + H - 11 * s, anchor="e", text="", fill=INK_2,
                             font=fs(8))
        tag = "t_" + slot
        # Die Ticket-Zeile gehoert jetzt zur normalen Kachel (t_-Tag): Linksklick
        # fokussiert die Kachel wie ueberall, Rechtsklick oeffnet das Kachel-Menue.
        # Ticket zuweisen/aendern laeuft ausschliesslich ueber dieses Rechtsklick-
        # Menue (Untermenue "Ticket") – die frueher direkte Klickflaeche auf der
        # Zeile ist bewusst entfernt.
        # Klick-/Hover-Flaeche: im Bildmodus traegt das Bild den Tag (es IST die
        # Kachelflaeche), sonst das Polygon.
        for it in ((rect or img), model, effort, ticket, act, mode):
            c.addtag_withtag(tag, it)
        # Linksklick auf die Kachel: Klick ODER Ziehen. _tile_press merkt sich nur den
        # Start; ob es ein Klick (fokussieren) oder ein Drag (umsortieren) war, entscheidet
        # _tile_release anhand der Bewegung – die Motion/Release-Handler haengen EINMAL fest
        # am Canvas (siehe _build), damit die Events auch kommen, wenn der Zeiger die Kachel
        # beim Ziehen kurz verlaesst.
        c.tag_bind(tag, "<Button-1>", lambda e, s=slot: self._tile_press(s, e))
        # Rechtsklick irgendwo auf die Kachel -> Kachel-Menue (Ticket zuweisen/entfernen,
        # Agent schliessen).
        c.tag_bind(tag, "<Button-3>", lambda e, s=slot: self._card_menu(s, e))
        # Hover auf der Kachel -> nach kurzer Verzoegerung ein Tooltip mit einer KI-Kurz-
        # zusammenfassung des Chats (chat_summary; Session-Adresse in _update_tiles je Poll
        # aktualisiert, Erzeugung laeuft im Hintergrund). ACHTUNG:
        # der t_-Tag liegt auf mehreren gestapelten Items (rect + Textzeilen); Tk feuert
        # beim Wechsel zwischen ihnen Leave+Enter, OHNE dass die Kachel verlassen wird ->
        # _hover_enter/_hover_leave fangen das ab (Slot-Vergleich + verzoegertes Ausblenden),
        # sonst wuerde der Tooltip beim Bewegen ueber der Kachel flackern/nie erscheinen.
        c.tag_bind(tag, "<Enter>", lambda e, s=slot: self._hover_enter(s))
        c.tag_bind(tag, "<Leave>", lambda e: self._hover_leave())
        # Sichtbarer ✕-Button oben rechts: EIGENES Item/Tag, damit ein Klick darauf
        # NICHT zusaetzlich die Kachel fokussiert (das ✕ liegt ueber dem Rechteck und
        # traegt den t_-Tag NICHT). Klick -> Agent SOFORT schliessen (ohne Rueckfrage);
        # Hover faerbt rot. Feste Farbe (nicht im refresh()-Loop) -> Hover-Rot wird
        # nicht ueberschrieben. (Rechtsklick auf die Kachel zeigt weiter das Menue.)
        cls = c.create_text(x + W - 8 * s, y + 12 * s, anchor="e", text="✕", fill=INK_3,
                            font=fs(11, "bold"))
        xt = "x_" + slot
        c.addtag_withtag(xt, cls)
        c.tag_bind(xt, "<Button-1>", lambda e, s=slot: self.close_agent(s))
        c.tag_bind(xt, "<Enter>", lambda e, i=cls:
                   (c.itemconfig(i, fill=LOST_GLOW), c.configure(cursor="hand2")))
        c.tag_bind(xt, "<Leave>", lambda e, i=cls:
                   (c.itemconfig(i, fill=INK_3), c.configure(cursor="")))
        # Gruppen-Tag über ALLE Items der Kachel -> als Einheit skalierbar (Press & Pop).
        gtag = "g_" + slot
        for it in rings + [rect, img, model, effort, ticket, act, mode, cls]:
            if it is not None:
                c.addtag_withtag(gtag, it)
        # Anim-/Glow-State: Ziele setzt refresh(), gefadet wird im _anim_tick.
        # fill_rgb = aktuelle Füllfarbe (float, wird zur fill_target hin geeast);
        # bloom = kurzes Aufleuchten bei Statuswechsel; status_key erkennt den Wechsel.
        # surge/surge_job = laufender Glow-Surge (Klick-Feedback 02): Halo-Boost + Kanten-Blitz.
        # press_scale/-job = laufender Press&Pop-Zoom (Klick-Feedback), press_cx/cy = Zentrum.
        # img/geom/photo/img_key = der Bildweg (Flaeche+Halo+Kante als PhotoImage):
        # geom sind die Bildmasse in Pixeln, photo HAELT die Bildreferenz (der
        # Canvas tut das nicht – ohne sie verschwaende die Kachel, sobald der
        # Cache den Eintrag verdraengt), img_key merkt den zuletzt gemalten
        # Zustand, damit nicht jeder Frame ein identisches Bild neu setzt.
        # rect/rings sind im Bildmodus None bzw. leer (Polygon-Fallback).
        self.tiles[slot] = {"rect": rect, "model": model,
                            "effort": effort, "ticket": ticket,
                            "act": act, "mode": mode,
                            "img": img, "geom": geom, "photo": None,
                            "img_key": None,
                            "border": CARD_BORDER, "border_w": 1,
                            "rings": rings, "glow_color": INK_3,
                            "glow_intensity": 0.0, "glow_pulse": False,
                            "fill_rgb": list(_hex_to_rgb(CARD_FILL)),
                            "fill_hex": CARD_FILL, "fill_target": CARD_FILL,
                            "bloom": 0.0, "surge": 0.0, "surge_job": None,
                            "status_key": None,
                            "gtag": gtag, "press_scale": 1.0, "press_job": None,
                            "press_cx": 0.0, "press_cy": 0.0,
                            # Geometrie fuer das Drag&Drop-Umsortieren: linke obere Ecke,
                            # Breite/Hoehe und der horizontale Schritt (Kachel+Abstand) zur
                            # naechsten Kachel. win = Fensterbuchstabe (nur innerhalb des
                            # eigenen Fensters wird getauscht).
                            "x": x, "y": y, "w": W, "h": H,
                            "step": step if step else W, "win": slot[0],
                            # Hover-Tooltip-Daten (je Poll in _update_tiles aktualisiert):
                            # letzte Frage (Fallback bei HOVER_SUMMARY=False) + session_id/
                            # cwd, mit denen chat_summary das Transcript findet.
                            "prompt": "", "session_id": "", "cwd": ""}
