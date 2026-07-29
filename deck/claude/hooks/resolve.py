"""Gemeinsame Basis der Claude-Code-Hooks (report.py + statusline.py):
State-Ordner + die Slot-Aufloesung ueber die Windows-Prozesskette.

Frueher wohnte die Slot-Aufloesung in report.py, und statusline.py griff auf
dessen privates _slot_from_procs zu. Das ist jetzt dieser gemeinsame, abhaengig-
keitsarme Leaf (nur stdlib + deck_paths). Er muss – wie die Hooks selbst – import-
sicher in beliebigem Arbeitsverzeichnis sein und darf NIE mit Fehler enden (sonst
blockiert der Hook den Agenten).
"""
import json
import os
import sys

from deck.domain.paths import STATE_DIR


def state_dir() -> str:
    return STATE_DIR


def _ancestor_pids() -> list[int]:
    """PID-Kette (ich -> Elternprozesse) via Toolhelp32-Snapshot. Nur Windows,
    reine stdlib (ctypes), kein Subprozess. Bei Problemen: leere Liste."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k = ctypes.windll.kernel32
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return []
        parent = {}
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k.Process32First(snap, ctypes.byref(e))
        while ok:
            parent[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
            ok = k.Process32Next(snap, ctypes.byref(e))
        k.CloseHandle(snap)

        chain, pid, seen = [], os.getpid(), set()
        while pid and pid not in seen:
            seen.add(pid)
            chain.append(pid)
            pid = parent.get(pid, 0)
        return chain
    except Exception:
        return []


def _load_pidmap(base: str) -> dict[int, str]:
    """Alle pidmap-*.json (je Fenster von der Extension geschrieben) zu
    {pid(int): slot} mergen. PIDs sind global eindeutig -> Union ist sicher."""
    out = {}
    try:
        for fn in os.listdir(base):
            if fn.startswith("pidmap-") and fn.endswith(".json"):
                try:
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        for k, v in json.load(f).items():
                            out[int(k)] = v
                except Exception:
                    pass  # halb geschriebene / kaputte Datei ignorieren
    except FileNotFoundError:
        pass
    return out


def slot_from_procs(base: str) -> str | None:
    """Ersten eigenen Vorfahren finden, der in der pidmap steht -> dessen Slot.
    Der von der Extension notierte Claude-PID ist immer ein Vorfahre des Hooks."""
    pm = _load_pidmap(base)
    if not pm:
        return None
    for pid in _ancestor_pids():
        if pid in pm:
            return pm[pid]
    return None
