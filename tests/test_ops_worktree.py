"""worktree: Parser, Selektoren, die Sicherung is_linked_worktree und die
'<repo>.wt'-Pfadkonvention des Disk-Sweeps.
"""

import os

import helpers  # setzt sys.path und die Deck-Sprache

from deck.ops import worktree as wtc


_WT_PORCELAIN = (
    "worktree C:/repo/my-backend\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree C:/repo/my-backend.wt/abc-123\n"
    "HEAD 2222222222222222222222222222222222222222\n"
    "branch refs/heads/ticket/abc-123\n"
    "\n"
    "worktree C:/repo/detached-one\n"
    "HEAD 3333333333333333333333333333333333333333\n"
    "detached\n"
)


def test_wt_parse_porcelain():
    e = wtc.parse_worktrees_porcelain(_WT_PORCELAIN)
    assert [x["path"] for x in e] == [
        "C:/repo/my-backend",
        "C:/repo/my-backend.wt/abc-123",
        "C:/repo/detached-one",
    ]
    assert [x["branch"] for x in e] == ["main", "ticket/abc-123", None]  # refs/heads/ ab
    assert wtc.parse_worktrees_porcelain("") == []
    assert wtc.parse_worktrees_porcelain(None) == []


def test_wt_main_and_branch_lookup():
    e = wtc.parse_worktrees_porcelain(_WT_PORCELAIN)
    assert wtc.main_path(e) == "C:/repo/my-backend"          # erster Eintrag
    assert wtc.path_for_branch(e, "ticket/abc-123") == "C:/repo/my-backend.wt/abc-123"
    assert wtc.path_for_branch(e, "ticket/nope") is None                 # kein Treffer
    assert wtc.path_for_branch(e, "") is None                            # kein Branch
    assert wtc.path_for_branch([], "ticket/abc-123") is None
    assert wtc.main_path([]) is None


# ── worktree_cleanup: die Sicherung is_linked_worktree (die einzige Schranke vor
# rmtree, wenn git fehlt). Nur ein VERLINKTER worktree (.git -> …/worktrees/<name>)
# darf True sein – Submodul (…/modules/…), separate-git-dir und Haupt-Checkout NICHT.
def test_is_linked_worktree_guard():
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtguard_")
    try:
        def mk(name, gitfile_content=None, gitdir=False):
            d = os.path.join(base, name)
            os.makedirs(d)
            if gitdir:
                os.makedirs(os.path.join(d, ".git"))                     # Haupt-Checkout
            elif gitfile_content is not None:
                with open(os.path.join(d, ".git"), "w", encoding="utf-8") as f:
                    f.write(gitfile_content)
            return d

        linked = mk("linked", "gitdir: C:/repo/.git/worktrees/abc-123\n")
        submod = mk("submod", "gitdir: ../.git/modules/sub\n")
        sepdir = mk("sepdir", "gitdir: C:/elsewhere/store.git\n")
        maindir = mk("maindir", gitdir=True)
        plaindir = mk("plaindir")                                        # gar kein .git
        garbage = mk("garbage", "not a gitdir line\n")

        assert wtc.is_linked_worktree(linked) is True
        assert wtc.is_linked_worktree(submod) is False                   # Submodul -> modules
        assert wtc.is_linked_worktree(sepdir) is False                   # separate-git-dir
        assert wtc.is_linked_worktree(maindir) is False                  # .git ist Verzeichnis
        assert wtc.is_linked_worktree(plaindir) is False
        assert wtc.is_linked_worktree(garbage) is False
        assert wtc.is_linked_worktree(os.path.join(base, "does-not-exist")) is False
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── worktree_cleanup: '<repo>.wt'-Pfadkonvention (Disk-Orphan-Sweep) ──────
def test_wt_dir_for_repo_roundtrip():
    root = os.path.normpath("C:/repo/my-web-ui")
    wt = wtc.wt_dir_for_repo(root)
    assert wt == root + ".wt"                                        # Geschwisterordner + '.wt'
    assert wtc.repo_root_from_wt_dir(wt) == root                     # Umkehr trifft wieder das Root
    # aus einem worktree-Marker das Repo-Root gewinnen (dirname -> repo_root_from_wt_dir)
    marker = os.path.join(wt, "46845651463")
    assert wtc.repo_root_from_wt_dir(os.path.dirname(marker)) == root
    # kein '.wt'-Ordner / leere Eingaben -> None
    assert wtc.repo_root_from_wt_dir(os.path.normpath("C:/repo/plain")) is None
    assert wtc.wt_dir_for_repo("") is None
    assert wtc.repo_root_from_wt_dir("") is None


def test_list_child_dirs():
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtdisk_")
    try:
        wt = os.path.join(base, "repo.wt")
        os.makedirs(os.path.join(wt, "a"))
        os.makedirs(os.path.join(wt, "b"))
        with open(os.path.join(wt, "loose.txt"), "w") as f:    # Datei -> kein Kind-Ordner
            f.write("x")
        got = sorted(os.path.basename(d) for d in wtc.list_child_dirs(wt))
        assert got == ["a", "b"]
        assert wtc.list_child_dirs(os.path.join(base, "nope")) == []   # kein Verzeichnis -> leer
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_remove_orphan_dir_guard_and_removal():
    """remove_orphan_dir raeumt einen '.wt'-Rest GANZ OHNE .git ab (den remove_worktree
    verweigert), verweigert aber JEDES Verzeichnis, das noch ein .git enthaelt – egal ob
    .git-VERZEICHNIS (echter Checkout) oder .git-DATEI (Submodul/separate-git-dir/worktree).
    Ohne repo laeuft kein git -> reiner rmtree, in den Tests unkritisch."""
    import tempfile, shutil
    base = tempfile.mkdtemp(prefix="wtorphan_")
    try:
        # (1) Rest ohne .git -> wird entfernt.
        leftover = os.path.join(base, "repo.wt", "2701")
        os.makedirs(leftover)
        with open(os.path.join(leftover, "file.txt"), "w") as f:
            f.write("stale")
        assert wtc.remove_orphan_dir(leftover) is True
        assert not os.path.isdir(leftover)
        # (2a) Ordner mit .git-VERZEICHNIS (echter Checkout) -> tabu, bleibt stehen.
        checkout = os.path.join(base, "repo.wt", "real")
        os.makedirs(os.path.join(checkout, ".git"))
        assert wtc.remove_orphan_dir(checkout) is False
        assert os.path.isdir(checkout)
        # (2b) Ordner mit .git-DATEI, die NICHT auf einen worktree zeigt (Submodul/
        #      separate-git-dir) -> ebenfalls tabu (der Fix gegen versehentliches Loeschen).
        submod = os.path.join(base, "repo.wt", "submod")
        os.makedirs(submod)
        with open(os.path.join(submod, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: ../.git/modules/submod\n")
        assert wtc.remove_orphan_dir(submod) is False
        assert os.path.isdir(submod)
        # (3) schon weg -> idempotent True; leerer Pfad -> False.
        assert wtc.remove_orphan_dir(os.path.join(base, "gone")) is True
        assert wtc.remove_orphan_dir("") is False
    finally:
        shutil.rmtree(base, ignore_errors=True)
