"""Fuehrt alle Testdateien aus - ohne pytest.

Das Deck hat bewusst keine Test-Abhaengigkeit: `python tests/run.py` genuegt. Mit
pytest laufen die Dateien ebenfalls (`python -m pytest tests/`), das ist aber kein
Muss.
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.dirname(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    files = sorted(f[:-3] for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    cases = []
    for name in files:
        mod = importlib.import_module(name)
        cases += [(f"{name[5:]}.{n}", getattr(mod, n)) for n in sorted(dir(mod))
                  if n.startswith("test_") and callable(getattr(mod, n))]

    fails = 0
    for label, fn in cases:
        try:
            fn()
            print(f"  ok  {label}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {label}: {e}")
        except Exception as e:  # ein Fehler im Test ist auch ein Fehlschlag
            fails += 1
            print(f"ERR   {label}: {type(e).__name__}: {e}")
    print(f"\n{len(cases) - fails}/{len(cases)} bestanden  ({len(files)} Dateien)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
