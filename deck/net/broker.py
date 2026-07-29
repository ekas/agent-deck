"""Broker: schlanker TCP-Server, ueber den das Panel mit den VS-Code-Extensions
redet. Protokoll = newline-getrenntes JSON, reine stdlib (kein websockets-Paket).

Ablauf:
- Jede Extension-Instanz verbindet sich und meldet ihren Workspace-Namen:
    {"type":"hello","workspace":"my-frontend","window":null,"slots":[]}
- Das Panel weist per Klick zu, welches Fenster A bzw. B ist:
    assign("my-frontend", "A")  -> schickt der Extension {"cmd":"assign","window":"A"}
- Danach adressiert das Panel per Fenster-Buchstabe:  send_window("A", {...})
- Extensions melden Terminals via {"type":"terminals","window":"A","slots":[...]}.
"""
import json
import socket
import threading

from deck.domain import protocol


class _Client:
    __slots__ = ("slots", "sock", "window", "workspace")

    def __init__(self, sock):
        self.sock = sock
        self.workspace = None
        self.window = None
        self.slots = []


class Broker:
    def __init__(self, host="127.0.0.1", port=8765):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._clients = []
        self._srv = None
        self._seen = set()   # Slots, deren Pane in VS Code fokussiert wurde (type:seen)

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        """Server-Socket schliessen -> der accept()-Loop bricht mit OSError ab und
        Port 8765 wird sofort frei. Wichtig beim Neustart, damit die neue Instanz den
        Port wieder binden kann (sonst bliebe der Broker still deaktiviert)."""
        srv, self._srv = self._srv, None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

    def _serve(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._srv.bind((self.host, self.port))
        except OSError:
            return  # Port belegt -> Broker still deaktiviert
        self._srv.listen(5)
        while True:
            try:
                client, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, sock):
        cl = _Client(sock)
        with self._lock:
            self._clients.append(cl)
        reader = sock.makefile("rb")
        try:
            for raw in reader:
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype in (protocol.TYPE_HELLO, protocol.TYPE_TERMINALS):
                    with self._lock:
                        if msg.get("workspace"):
                            cl.workspace = msg["workspace"]
                        if "window" in msg:
                            # Explizit null (nach cmd:unassign) MUSS den Buchstaben
                            # loeschen – sonst bliebe eine vergessene Kachel haengen,
                            # falls eine alte Meldung dem unassign zuvorgekommen ist.
                            # Legit-Bindungen setzt das Panel jede Runde neu (assign).
                            cl.window = msg["window"] or None
                        cl.slots = msg.get("slots", cl.slots)
                elif mtype == protocol.TYPE_SEEN:
                    # Pane in VS Code angeklickt -> Slot vormerken; das Panel holt
                    # ihn per drain_seen() ab und schaltet 'ungelesen' -> 'idle'.
                    slot = msg.get("slot")
                    if slot:
                        with self._lock:
                            self._seen.add(slot)
        except Exception:
            pass
        finally:
            with self._lock:
                if cl in self._clients:
                    self._clients.remove(cl)
            try:
                sock.close()
            except Exception:
                pass

    # ── intern ──────────────────────────────────────────
    def _find(self, window=None, workspace=None):
        with self._lock:
            for cl in self._clients:
                if window is not None and cl.window == window:
                    return cl
                if workspace is not None and cl.workspace and \
                        cl.workspace.lower() == workspace.lower():
                    return cl
        return None

    def _write(self, cl, obj):
        try:
            cl.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            return True
        except Exception:
            return False

    # ── oeffentlich ─────────────────────────────────────
    def send_window(self, window, obj):
        """Kommando an das Fenster mit diesem Buchstaben. True bei Erfolg."""
        cl = self._find(window=window)
        return self._write(cl, obj) if cl else False

    def assign(self, workspace, window):
        """Der Extension mit diesem Workspace den Fenster-Buchstaben zuweisen."""
        cl = self._find(workspace=workspace)
        if not cl:
            return False
        with self._lock:
            cl.window = window
        return self._write(cl, {"cmd": protocol.CMD_ASSIGN, "window": window})

    def forget(self, window):
        """Die Zuordnung dieses Buchstabens loesen: der Extension sagen, dass sie
        ihren Buchstaben vergisst (cmd:unassign), und die Server-seitige Zuordnung
        aufheben. Noetig, damit auch eine verbundene, aber bindungslose Phantomkachel
        verschwindet – sonst meldet die Extension ihren gemerkten Buchstaben neu."""
        cl = self._find(window=window)
        if not cl:
            return False
        self._write(cl, {"cmd": protocol.CMD_UNASSIGN})
        with self._lock:
            cl.window = None
        return True

    def connected(self, window):
        return self._find(window=window) is not None

    def drain_seen(self):
        """Slots, deren Pane seit dem letzten Aufruf in VS Code fokussiert wurde
        (Extension meldet type:'seen'), zurueckgeben UND den Puffer leeren. Das
        Panel nutzt das, um 'ungelesen' (done) -> 'idle' zu schalten, sobald du
        einen Agenten direkt in VS Code anklickst."""
        with self._lock:
            out, self._seen = self._seen, set()
        return out

    def terminals(self, window):
        """Aktuell gemeldete Terminal-/Slot-Namen des Fensters (leer, wenn keins)."""
        cl = self._find(window=window)
        return list(cl.slots) if cl else []

    def workspace_slots(self, workspace):
        """Slot-/Terminal-Namen des Clients mit diesem Workspace (leer, wenn keiner).
        Genutzt, um beim Auto-Binden den Buchstaben zu bevorzugen, den die vorhandenen
        Terminals schon tragen (Slot-Namen wie 'C1') -> stabile Zuordnung."""
        cl = self._find(workspace=workspace)
        return list(cl.slots) if cl else []

    def workspaces(self):
        """Aktuell verbundene Workspace-Namen (fuer die Anzeige/Diagnose)."""
        with self._lock:
            return [cl.workspace for cl in self._clients if cl.workspace]
