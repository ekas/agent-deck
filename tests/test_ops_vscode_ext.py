"""VS Codes Extension-Registratur (extensions.json).

Diese Datei deckt einen Ausfall ab, den man am Ordner NICHT sehen kann: die Extension
liegt vollstaendig und aktuell an ihrem Platz, aber der Eintrag in extensions.json zeigt
auf einen umbenannten Ordner - VS Code laedt sie dann gar nicht. `install.ps1 -Check`
hat in genau diesem Zustand gruen gemeldet, weil es Datei und Hash prueft.

Zwei Dinge werden darum besonders festgenagelt: dass fremde Extensions eine Reparatur
unbeschadet ueberleben (sie stehen in DERSELBEN Datei), und dass eine unlesbare
Registratur nicht ueberschrieben wird.
"""
import io
import json
import os
import shutil
import tempfile
from contextlib import redirect_stdout

import helpers  # noqa: F401  - legt die Repo-Wurzel auf den sys.path

from deck.ops import vscode_ext as ve

EXT = r"C:\Users\test\.vscode\extensions"
GEIST = "agent-deck-bridge.testbackup"      # der Ordnername, der am 2026-07-30 stand


def _unser(rel=ve.EXT_DIR, version="0.1.1", ext_dir=EXT):
    """Ein Eintrag, wie VS Code ihn fuer eine lokal hineinkopierte Extension schreibt.

    `ext_dir` muss zu dem Ordner passen, gegen den geprueft wird - sonst widersprechen
    sich location und relativeLocation, und das ist ein eigener Befund.
    """
    return {"identifier": {"id": ve.EXT_ID}, "version": version,
            "location": ve._location(ext_dir, rel), "relativeLocation": rel}


def _fremd(name="ms-vscode.powershell-2025.4.0", ident="ms-vscode.powershell"):
    """Eine Extension, die jemand anderem gehoert."""
    return {"identifier": {"id": ident}, "version": "2025.4.0",
            "location": ve._location(EXT, name), "relativeLocation": name}


def _exists(*ordner):
    """exists-Attrappe: nur die genannten Ordner haben eine package.json."""
    da = {os.path.join(EXT, o, "package.json") for o in ordner}
    return lambda p: p in da


def _grade(findings):
    return [g for g, _t in findings]


# ── Urteile ─────────────────────────────────────────────────────────────────

def test_korrekt_registrierte_extension_ist_ok():
    findings = ve.audit([_fremd(), _unser()], EXT, exists=_exists(ve.EXT_DIR))
    assert _grade(findings) == ["ok"]


def test_eintrag_ins_leere_ist_ein_fail():
    """Der echte Fall: der Eintrag zeigt auf einen Ordner, den es nicht mehr gibt."""
    findings = ve.audit([_unser(rel=GEIST)], EXT, exists=_exists(ve.EXT_DIR))
    assert any(g == "fail" and GEIST in t for g, t in findings)


def test_ordner_ohne_eintrag_ist_ein_fail():
    """Datei und Hash stimmen, VS Code laedt trotzdem nichts - der stille Ausfall."""
    findings = ve.audit([_fremd()], EXT, exists=_exists(ve.EXT_DIR))
    assert any(g == "fail" and "laedt die Extension nicht" in t for g, t in findings)


def test_gar_nichts_installiert_ist_auch_ein_fail():
    findings = ve.audit([_fremd()], EXT, exists=_exists())
    assert any(g == "fail" and "weder installiert noch registriert" in t
               for g, t in findings)


def test_zwei_eintraege_sind_eine_warnung():
    """Welchen VS Code nimmt, entscheidet dann die Reihenfolge in der Datei."""
    findings = ve.audit([_unser(), _unser()], EXT, exists=_exists(ve.EXT_DIR))
    assert any(g == "warn" and "2 Eintraege" in t for g, t in findings)


def test_widerspruch_zwischen_location_und_relativelocation_faellt_auf():
    """Beide Felder werden benutzt - ein Widerspruch ist keine Kosmetik."""
    e = _unser()
    e["location"] = ve._location(EXT, GEIST)
    findings = ve.audit([e], EXT, exists=_exists(ve.EXT_DIR))
    assert any(g == "fail" and "widerspricht" in t for g, t in findings)


def test_eintrag_ohne_relativelocation_ist_ein_fail():
    e = _unser()
    del e["relativeLocation"]
    findings = ve.audit([e], EXT, exists=_exists(ve.EXT_DIR))
    assert any(g == "fail" for g, _t in findings)


def test_unlesbare_registratur_ist_nur_eine_warnung():
    """Ohne die Datei ist von hier aus KEIN Urteil moeglich - also kein fail."""
    findings = ve.audit(None, EXT, exists=_exists(ve.EXT_DIR))
    assert _grade(findings) == ["warn"]


# ── Wiedererkennen ──────────────────────────────────────────────────────────

def test_umbenannter_ordner_wird_am_namen_wiedererkannt():
    """Wie bei den Hooks am Dateinamen: sonst legt die Reparatur einen ZWEITEN Eintrag
    daneben, und VS Code hat die Wahl."""
    ohne_id = {"relativeLocation": GEIST, "location": ve._location(EXT, GEIST)}
    assert ve.is_ours(ohne_id)


def test_fremde_extension_gehoert_uns_nicht():
    assert not ve.is_ours(_fremd())
    assert not ve.is_ours(_fremd("agent-deck-bridge-fork", "someone.agent-deck-bridge-fork"))
    assert not ve.is_ours("kein dict")


# ── Reparieren ──────────────────────────────────────────────────────────────

def test_reparatur_biegt_den_geisterpfad_auf_den_echten_ordner():
    after, notes = ve.repair([_unser(rel=GEIST)], EXT, "0.1.1")
    assert after[0]["relativeLocation"] == ve.EXT_DIR
    assert after[0]["location"]["path"].endswith("/" + ve.EXT_DIR)
    assert any("umgebogen" in t for _g, t in notes)


def test_reparatur_legt_einen_fehlenden_eintrag_an():
    after, notes = ve.repair([_fremd()], EXT, "0.1.1")
    assert [e for e in after if ve.is_ours(e)]
    assert any("angelegt" in t for _g, t in notes)


def test_reparatur_faellt_zwei_eintraege_auf_einen_zusammen():
    after, _ = ve.repair([_unser(), _unser(rel=GEIST)], EXT, "0.1.1")
    assert len([e for e in after if ve.is_ours(e)]) == 1


def test_reparatur_laesst_fremde_extensions_in_ruhe():
    """Sie stehen in DERSELBEN Datei - ein Vollneubau wuerde sie deinstallieren.
    Geprueft wird Inhalt UND Reihenfolge."""
    fremde = [_fremd("a-1.0", "x.a"), _fremd("b-2.0", "y.b"), _fremd("c-3.0", "z.c")]
    after, _ = ve.repair([fremde[0], _unser(rel=GEIST), fremde[1], fremde[2]], EXT, "0.1.1")
    assert [e for e in after if not ve.is_ours(e)] == fremde


def test_reparatur_erhaelt_felder_die_vs_code_selbst_dazugeschrieben_hat():
    """Ergaenzen, nicht ersetzen: was wir nicht kennen, muss stehenbleiben."""
    e = _unser(rel=GEIST)
    e["metadata"] = {"installedTimestamp": 123}
    after, _ = ve.repair([e], EXT, "0.1.1")
    assert after[0]["metadata"] == {"installedTimestamp": 123}


def test_zweiter_lauf_aendert_nichts():
    """Idempotenz ist die Bedingung dafuer, dass install.ps1 wiederholbar ist."""
    einmal, _ = ve.repair([_fremd(), _unser(rel=GEIST)], EXT, "0.1.1")
    zweimal, notes = ve.repair(einmal, EXT, "0.1.1")
    assert zweimal == einmal
    assert notes == []


def test_reparierter_eintrag_besteht_die_pruefung():
    """Die beiden Haelften muessen zueinander passen - sonst repariert der Installer in
    einen Zustand, den sein eigener Doctor bemaengelt."""
    after, _ = ve.repair([_unser(rel=GEIST)], EXT, "0.1.1")
    assert _grade(ve.audit(after, EXT, exists=_exists(ve.EXT_DIR))) == ["ok"]


def test_entfernen_nimmt_nur_unseren_eintrag_heraus():
    after, notes = ve.remove([_fremd(), _unser(), _fremd("b-2.0", "y.b")])
    assert not [e for e in after if ve.is_ours(e)]
    assert len(after) == 2
    assert notes


# ── Die Pfadform, die VS Code erwartet ──────────────────────────────────────

def test_uri_pfad_hat_fuehrenden_slash_und_kleinen_laufwerksbuchstaben():
    """Abgelesen an dem Wert, den VS Code selbst geschrieben hatte."""
    assert ve._uri_path(r"C:\Users\x\.vscode\extensions\y") \
        == "/c:/Users/x/.vscode/extensions/y"


def test_umlaute_im_pfad_bleiben_unescaped():
    """VS Code fuehrt sie in `path` unescaped; ein \\u00df waere ein anderer String."""
    assert "ß" in ve._uri_path(r"C:\Users\Müßiggang\.vscode\extensions\y")


# ── Mit echter Datei: der Sicherheitsgurt ───────────────────────────────────

def _tempdir_mit_extension():
    """Ein Wegwerf-Extensions-Ordner mit installierter, aber unregistrierter Extension."""
    d = tempfile.mkdtemp(prefix="deck-ext-")
    os.makedirs(os.path.join(d, ve.EXT_DIR))
    with open(os.path.join(d, ve.EXT_DIR, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": ve.EXT_DIR, "version": "0.1.1"}, f)
    return d


def _lauf(*args):
    """main() gegen einen Ordner laufen lassen, ohne die Testausgabe zu verschmutzen."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ve.main(list(args))
    return code, buf.getvalue()


def test_unlesbare_registratur_wird_nicht_ueberschrieben():
    """Der Sicherheitsgurt: in dieser Datei stehen ALLE Extensions des Nutzers. Ein
    Tippfehler darin darf sie nicht deinstallieren."""
    d = _tempdir_mit_extension()
    try:
        p = ve.registry_path(d)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{kein json[")
        code, out = _lauf("--extensions-dir", d)
        assert code == 1 and "nicht angefasst" in out
        with open(p, encoding="utf-8") as f:
            assert f.read() == "{kein json["      # unveraendert
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fehlende_registratur_wird_angelegt_und_dann_ist_es_ok():
    d = _tempdir_mit_extension()
    try:
        assert _lauf("--extensions-dir", d)[0] == 0
        assert _lauf("--check", "--extensions-dir", d) == (0, "  [ok]   "
                     + ve.EXT_ID + " ist auf " + ve.EXT_DIR + "\\ registriert\n")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_check_endet_mit_exit_1_solange_der_geisterpfad_steht():
    """install.ps1 zaehlt daran seine Befunde - ohne Exit 1 bleibt der Doctor gruen."""
    d = _tempdir_mit_extension()
    try:
        ve.save([_unser(rel=GEIST, ext_dir=d)], ve.registry_path(d))
        code, out = _lauf("--check", "--porcelain", "--extensions-dir", d)
        assert code == 1
        assert "## fails=1 warns=0" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ohne_installierte_extension_wird_nichts_registriert():
    """Sonst legt der Installer genau den Geistereintrag an, gegen den es hier geht."""
    d = tempfile.mkdtemp(prefix="deck-ext-")
    try:
        ve.save([_fremd()], ve.registry_path(d))
        code, out = _lauf("--extensions-dir", d)
        assert code == 1 and "nicht installiert" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_geschriebene_datei_ist_eine_kompakte_zeile_ohne_bom():
    """So schreibt VS Code sie selbst. Ein BOM oder eingerueckte Zeilen sind zwar
    gueltiges JSON, aber ein unnoetiger Unterschied in einer fremden Datei."""
    d = tempfile.mkdtemp(prefix="deck-ext-")
    try:
        p = ve.registry_path(d)
        ve.save([_unser(), _fremd()], p)
        roh = open(p, "rb").read()
        assert not roh.startswith(b"\xef\xbb\xbf")
        assert b"\n" not in roh
        assert b", " not in roh and b'": ' not in roh
        assert len(json.loads(roh.decode("utf-8"))) == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_backup_entsteht_nur_wenn_wirklich_geschrieben_wird():
    """Sonst sammelt jeder Doctor-Lauf eine weitere Sicherung an."""
    d = _tempdir_mit_extension()
    try:
        ve.save([_unser(rel=GEIST, ext_dir=d)], ve.registry_path(d))
        _lauf("--extensions-dir", d)                     # repariert -> Sicherung
        n1 = len([f for f in os.listdir(d) if ".bak-" in f])
        _lauf("--extensions-dir", d)                     # Nulldurchgang -> keine weitere
        n2 = len([f for f in os.listdir(d) if ".bak-" in f])
        assert n1 == 1 and n2 == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── Die Doppelung mit install.ps1 ───────────────────────────────────────────

def test_der_ordnername_steht_genauso_in_install_ps1():
    """EXT_DIR und $ExtDst muessen denselben Ordner meinen. Liefen sie auseinander,
    kopierte der Installer nach A und registrierte B - der Geistereintrag von Hand."""
    with open(os.path.join(helpers.ROOT, "install.ps1"), encoding="utf-8") as f:
        src = f.read()
    assert f"'.vscode\\extensions\\{ve.EXT_DIR}'" in src, \
        "$ExtDst in install.ps1 zeigt auf einen anderen Ordner als EXT_DIR"


def test_die_id_steht_genauso_in_der_package_json_der_extension():
    """EXT_ID ist publisher.name aus der package.json - VS Code bildet sie daraus."""
    with open(os.path.join(helpers.ROOT, "extension", "package.json"),
              encoding="utf-8") as f:
        pkg = json.load(f)
    assert ve.EXT_ID == f"{pkg['publisher']}.{pkg['name']}"
    assert ve.EXT_DIR == pkg["name"]
