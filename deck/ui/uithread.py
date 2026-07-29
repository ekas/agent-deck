"""Rückweg vom Arbeits-Thread auf den Tk-Thread.

Tk ist nicht thread-sicher. Ergebnisse aus Hintergrund-Threads laufen darum
über eine Queue, die _ui_pump im Tk-Takt leert - NICHT über root.after(0) aus
dem Thread heraus, das hat das Panel reproduzierbar sterben lassen.
"""
import queue
import threading

from deck import i18n
from deck.claude import summarize as cs
from deck.ops import log
from deck.render import kit as ck
from deck.render.kit import CARD_BORDER
from deck.render.kit import INK_3

from deck.ui.theme import SUMMARY_MODEL, SUMMARY_ON, TICKET_AUTO, TICKET_PROJECT, UI_PUMP_MS


class UiThreadMixin:
    """Wird in AgentDeck eingemischt (siehe panel.py)."""

    def _post(self, fn):
        """Aus einem Hintergrund-Thread etwas auf dem Tk-Thread ausfuehren lassen.

        NICHT self.root.after(0, …) aus dem Thread benutzen, auch wenn es meistens
        gutgeht: after() ruft Tcl am Interpreter des Main-Threads auf, und tkinter
        haelt einen Fremdthread dabei nicht auf. Bei einem threaded Tcl-Build
        (tcl86t.dll) endet das irgendwann in einem Tcl_Panic – der Prozess ist dann
        SOFORT weg (abort(), kein Traceback, im Event-Log nur 0x80000003). Genau so
        ist das Panel am 2026-07-28 um 16:11 gestorben, zwei Sekunden nachdem ein
        Summary-Thread fertig wurde. Eine Queue ist der einzige gefahrlose Weg:
        put() ist thread-safe und faellt nicht ins Tcl."""
        self._ui_q.put(fn)

    def _ui_pump(self):
        """Tk-Thread: abarbeiten, was die Threads hinterlegt haben (eigener Takt,
        damit das nicht am refresh-Poll haengt, der beim Kachel-Drag pausiert).
        Ein Fehler in einem Callback darf die Pumpe nie anhalten."""
        while True:
            try:
                fn = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exc("_ui_pump")
        self.root.after(UI_PUMP_MS, self._ui_pump)

    def _ensure_chat_info(self, sid, cwd, summary=True):
        """Im Hintergrund Ticket-ID + Zusammenfassung dieser Session sicherstellen.
        Beides liest das Transcript (und generate() startet zusaetzlich claude) -> Daemon-
        Thread, NIE auf dem Tk-Thread. Pro Session laeuft hoechstens ein Job gleichzeitig.
        summary=False holt nur die Ticket-ID (Prefetch-Scan bei abgeschaltetem
        HOVER_SUMMARY_PREFETCH). Adressiert wird ueber die SESSION, nicht den Slot: bis
        das Ergebnis da ist, kann die Kachel laengst umsortiert oder weg sein."""
        if not sid or sid in self._summary_jobs:
            return
        self._summary_jobs.add(sid)
        threading.Thread(target=self._chat_info_worker,
                         args=(sid, cwd, summary), daemon=True).start()

    def _chat_info_worker(self, sid, cwd, summary=True):
        """Daemon-Thread: erst Ticket/PR (billig, reine Regex -> sofort nachziehen),
        dann die Zusammenfassung (teuer, claude). Fasst hier KEIN Tk an – der Rueckweg
        laeuft ueber _post (Queue), NICHT ueber root.after; siehe dort, warum."""
        if TICKET_AUTO:
            try:
                refs = cs.ensure_refs(sid, cwd, project=TICKET_PROJECT)
            except Exception:
                refs = None
            if refs is not None:                # Bezugs-Zeile sofort, ohne auf claude zu warten
                self._post(lambda: self._refs_ready(sid, refs))
        text = None
        if summary and SUMMARY_ON:
            try:
                text = cs.generate(sid, cwd, model=SUMMARY_MODEL, lang=i18n.current())
            except Exception:
                text = None
        self._post(lambda: self._chat_info_ready(sid, text))

    def _refs_ready(self, sid, refs):
        """Zurueck auf dem Tk-Thread: erkanntes Ticket/PR merken (die Karte liest sie im
        Poll von hier) und einen gerade sichtbaren Tooltip derselben Session nachziehen."""
        if self._auto_refs.get(sid) == refs:
            return
        self._auto_refs[sid] = refs
        self._refresh_tip_for(sid)

    def _chat_info_ready(self, sid, summary):
        """Zusammenfassung ist da -> Tooltip nachziehen (nur wenn er gerade sichtbar ist
        und noch dieselbe Session gehovert wird, siehe _refresh_tip_for)."""
        self._summary_jobs.discard(sid)
        if summary:
            self._refresh_tip_for(sid)

    def _refresh_tip_for(self, sid):
        """Sichtbaren Tooltip mit frischem Inhalt neu zeichnen, ABER nur, wenn er GERADE
        sichtbar ist und die gehoverte Kachel noch zu dieser Session gehoert. Der
        _tip_visible-Check ist wichtig: nach einem Klick haelt focus_slot _hover_slot
        (keep_hover), blendet den Tooltip aber aus -> ohne den Check poppte die spaet
        eintreffende Zusammenfassung ueber dem nach vorn geholten VS-Code-Fenster auf
        (dieselbe Falle wie beim Klick-Reentry, siehe _hover_enter)."""
        slot = self._hover_slot
        if not self._tip_visible or not slot:
            return
        ids = self.tiles.get(slot)
        if not ids or (ids.get("session_id") or "") != sid:
            return
        text = self._tip_text(ids, sid, slot)
        if text:
            self._tip_at_pointer(text)

    def _draw_add(self, c, win, x, y, W, H, R):
        rect = ck.round_rect(c, x, y, x + W, y + H, R,
                                fill="#191921", outline=CARD_BORDER, width=1)
        plus = c.create_text(x + W / 2, y + H / 2, text="＋", fill=INK_3,
                             font=("Segoe UI", 18, "bold"))
        tag = "add_" + win
        for it in (rect, plus):
            c.addtag_withtag(tag, it)
        c.tag_bind(tag, "<Button-1>", lambda e, g=win: self.create_agent(g))
