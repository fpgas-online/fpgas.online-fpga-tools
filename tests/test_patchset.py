from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from fpgatools import cli, patchset, pins
from fpgatools.cli import FpgatoolsError

from .conftest import commit_file, git, make_repo

TOOL = "openfpgaloader"


@dataclass
class Fixture:
    upstream: Path
    c1: str  # "stable" pin, tagged v1.0.0
    c2: str  # "master" pin
    pins: pins.Pins
    pins_path: Path


def write_pins(path: Path, url: str, stable: str, master: str, other: str) -> None:
    path.write_text(
        f"""
[{TOOL}]
url = "{url}"
[{TOOL}.stable]
ref = "v1.0.0"
commit = "{stable}"
[{TOOL}.master]
ref = "master"
commit = "{master}"
describe = "v1.0.0-1-g{master[:7]}"
date = "2026-01-02"

[rp1jtag]
url = "{url}"
ref = "main"
commit = "{other}"
"""
    )


@pytest.fixture
def fx(tmp_path, monkeypatch) -> Fixture:
    monkeypatch.setattr(patchset, "SRC_ROOT", tmp_path / "build" / "src")
    monkeypatch.setattr(patchset, "PATCHES_ROOT", tmp_path / "patches")
    upstream = make_repo(tmp_path / "upstream")
    c1 = commit_file(upstream, "a.txt", "a\n", "upstream: add a")
    git(upstream, "tag", "v1.0.0")
    c2 = commit_file(upstream, "b.txt", "b\n", "upstream: add b")
    pins_path = tmp_path / "upstreams.toml"
    write_pins(pins_path, upstream.as_uri(), c1, c2, c2)
    monkeypatch.setattr(patchset, "PINS_PATH", pins_path)
    return Fixture(upstream, c1, c2, pins.load(pins_path), pins_path)


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def tree(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{rev}^{{tree}}")


# --- paths --------------------------------------------------------------------


def test_src_dir(fx):
    assert patchset.src_dir(TOOL, "master") == patchset.SRC_ROOT / f"{TOOL}-master"
    assert patchset.src_dir("rp1jtag") == patchset.SRC_ROOT / "rp1jtag"


def test_series_dir_and_series(fx):
    sdir = patchset.series_dir(TOOL, "stable")
    assert sdir == patchset.PATCHES_ROOT / TOOL / "stable"
    assert patchset.series(TOOL, "stable") == []
    sdir.mkdir(parents=True)
    for name in ("0002-b.patch", "0001-a.patch", "README", "0003-c.patch.orig"):
        (sdir / name).write_text("")
    assert [p.name for p in patchset.series(TOOL, "stable")] == ["0001-a.patch", "0002-b.patch"]


# --- fetch --------------------------------------------------------------------


def test_fetch_detaches_at_the_pin(fx):
    d = patchset.fetch(TOOL, "master")
    assert d == patchset.src_dir(TOOL, "master")
    assert head(d) == fx.c2
    assert git(d, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached
    assert (d / "a.txt").read_text() == "a\n" and (d / "b.txt").read_text() == "b\n"
    assert git(d, "config", "--local", "user.name") == "fpgas.online build"
    assert git(d, "config", "--local", "user.email") == "builds@fpgas.online"
    assert git(d, "remote", "get-url", "origin") == fx.upstream.as_uri()

    s = patchset.fetch(TOOL, "stable")
    assert s == patchset.src_dir(TOOL, "stable") and head(s) == fx.c1
    assert not (s / "b.txt").exists()

    u = patchset.fetch("rp1jtag", pins=fx.pins)
    assert u == patchset.src_dir("rp1jtag") and head(u) == fx.c2


def test_fetch_keeps_work_in_progress_unless_forced(fx):
    d = patchset.fetch(TOOL, "master")
    c3 = commit_file(d, "c.txt", "c\n", "local work")
    assert patchset.fetch(TOOL, "master") == d
    assert head(d) == c3  # pin is an ancestor: left alone
    patchset.fetch(TOOL, "master", force=True)
    assert head(d) == fx.c2
    assert not (d / "c.txt").exists()
    assert git(d, "status", "--porcelain") == ""


def test_fetch_moves_to_a_new_pin(fx, tmp_path):
    d = patchset.fetch(TOOL, "master")
    assert head(d) == fx.c2
    moved = tmp_path / "moved.toml"
    write_pins(moved, fx.upstream.as_uri(), fx.c1, fx.c1, fx.c1)  # master now pins c1
    assert patchset.fetch(TOOL, "master", pins=pins.load(moved)) == d
    assert head(d) == fx.c1


def test_fetch_errors(fx):
    with pytest.raises(FpgatoolsError, match="needs a track"):
        patchset.fetch(TOOL)
    with pytest.raises(FpgatoolsError, match="unknown upstream"):
        patchset.fetch("nothing", "master")


# --- export / apply -------------------------------------------------------------


def test_export_apply_round_trip(fx):
    d = patchset.fetch(TOOL, "master")
    sdir = patchset.series_dir(TOOL, "master")
    sdir.mkdir(parents=True)
    (sdir / "0001-stale.patch").write_text("stale\n")

    commit_file(d, "a.txt", "a\nmodified\n", "Fix the a file\n\nLonger explanation.")
    patched_tree = tree(d)

    exported = patchset.export(TOOL, "master")
    assert exported == [sdir / "0001-Fix-the-a-file.patch"]
    assert patchset.series(TOOL, "master") == exported  # the stale one is gone
    text = exported[0].read_text()
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    assert "\nSubject: [PATCH] Fix the a file\n" in text
    assert "\nFrom: Fixture Author <fixture@example.com>\n" in text
    assert "\n-- \n" not in text  # --no-signature
    assert patchset.patch_subject(exported[0]) == "Fix the a file"

    # apply reproduces the tree on a clean checkout of the pin.
    assert patchset.apply(TOOL, "master") == d
    assert head(d) != fx.c2
    assert git(d, "rev-parse", "HEAD~1") == fx.c2
    assert tree(d) == patched_tree
    assert git(d, "log", "-1", "--format=%an <%ae>") == "Fixture Author <fixture@example.com>"
    assert git(d, "log", "-1", "--format=%B").strip() == "Fix the a file\n\nLonger explanation."

    # and again from nothing.
    shutil.rmtree(d)
    patchset.apply(TOOL, "master")
    assert tree(d) == patched_tree


def test_export_numbers_a_series(fx):
    d = patchset.fetch(TOOL, "master")
    commit_file(d, "a.txt", "a2\n", "first change")
    commit_file(d, "b.txt", "b2\n", "second change: with punctuation!")
    names = [p.name for p in patchset.export(TOOL, "master")]
    assert names == ["0001-first-change.patch", "0002-second-change-with-punctuation.patch"]
    subjects = [patchset.patch_subject(p) for p in patchset.series(TOOL, "master")]
    assert subjects == ["first change", "second change: with punctuation!"]


def test_export_requires_a_fetched_tree(fx):
    with pytest.raises(FpgatoolsError, match="fetch"):
        patchset.export(TOOL, "master")


def test_apply_empty_series(fx):
    d = patchset.apply(TOOL, "stable")
    assert head(d) == fx.c1


def test_apply_conflict_aborts_and_names_the_patch(fx, tmp_path):
    d = patchset.fetch(TOOL, "master")
    commit_file(d, "a.txt", "x\n", "a becomes x")
    patchset.export(TOOL, "master")

    patchset.fetch(TOOL, "master", force=True)
    commit_file(d, "a.txt", "y\n", "a becomes y")
    scratch = tmp_path / "scratch"
    git(d, "format-patch", "--zero-commit", "-o", str(scratch), f"{fx.c2}..HEAD")
    conflicting = patchset.series_dir(TOOL, "master") / "0002-a-becomes-y.patch"
    shutil.copy(next(scratch.iterdir()), conflicting)

    with pytest.raises(patchset.PatchConflict) as info:
        patchset.apply(TOOL, "master")
    assert info.value.patch_name == "0002-a-becomes-y.patch"
    assert "0002-a-becomes-y.patch" in str(info.value)
    assert isinstance(info.value, FpgatoolsError)
    assert not (d / ".git" / "rebase-apply").exists()  # aborted
    assert head(d) == fx.c2
    assert git(d, "status", "--porcelain") == ""


# --- subjects / compare ------------------------------------------------------------


def write_patch(path: Path, subject_lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(subject_lines)
    path.write_text(
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        "From: Someone <s@example.com>\n"
        "Date: Thu, 1 Jan 2026 00:00:00 +0000\n"
        f"{body}\n"
        "\n"
        "Body text that mentions Subject: nothing.\n"
        "---\n a | 1 +\n"
    )
    return path


def test_patch_subject_variants(tmp_path):
    p = write_patch(tmp_path / "one.patch", ["Subject: [PATCH] plain subject"])
    assert patchset.patch_subject(p) == "plain subject"
    p = write_patch(tmp_path / "num.patch", ["Subject: [PATCH 2/7] numbered: subject"])
    assert patchset.patch_subject(p) == "numbered: subject"
    p = write_patch(
        tmp_path / "fold.patch",
        ["Subject: [PATCH 12/22] a very long subject that git", " folded onto a second line"],
    )
    assert patchset.patch_subject(p) == "a very long subject that git folded onto a second line"
    p = write_patch(tmp_path / "enc.patch", ["Subject: =?UTF-8?q?[PATCH]=20caf=C3=A9=20fix?="])
    assert patchset.patch_subject(p) == "café fix"
    p = write_patch(tmp_path / "none.patch", ["X-Other: header"])
    with pytest.raises(FpgatoolsError, match="Subject"):
        patchset.patch_subject(p)


def test_compare(fx):
    stable = patchset.series_dir(TOOL, "stable")
    master = patchset.series_dir(TOOL, "master")
    write_patch(stable / "0001-a.patch", ["Subject: [PATCH 1/2] first"])
    write_patch(stable / "0002-b.patch", ["Subject: [PATCH 2/2] second"])
    write_patch(master / "0001-a.patch", ["Subject: [PATCH 1/2] first"])
    write_patch(master / "0002-b.patch", ["Subject: [PATCH 2/2] second"])
    pairs = patchset.compare(TOOL)
    assert pairs == [("first", "first"), ("second", "second")]
    assert patchset.compare_ok(pairs)

    write_patch(master / "0002-b.patch", ["Subject: [PATCH 2/2] second (master)"])
    pairs = patchset.compare(TOOL)
    assert pairs == [("first", "first"), ("second", "second (master)")]
    assert not patchset.compare_ok(pairs)

    write_patch(master / "0003-c.patch", ["Subject: [PATCH 3/3] third"])
    pairs = patchset.compare(TOOL)
    assert pairs[-1] == (None, "third")
    assert not patchset.compare_ok(pairs)
    assert patchset.compare_ok([])


# --- CLI ------------------------------------------------------------------------


def test_cli_round_trip(fx, capsys):
    assert cli.main(["fetch", TOOL, "master"]) == 0
    d = patchset.src_dir(TOOL, "master")
    assert head(d) == fx.c2
    commit_file(d, "a.txt", "cli\n", "cli change")
    assert cli.main(["export", TOOL, "master"]) == 0
    assert [p.name for p in patchset.series(TOOL, "master")] == ["0001-cli-change.patch"]
    assert cli.main(["fetch", TOOL, "master", "--force"]) == 0
    assert head(d) == fx.c2
    assert cli.main(["apply", TOOL, "master"]) == 0
    assert (d / "a.txt").read_text() == "cli\n"
    capsys.readouterr()

    assert cli.main(["compare", TOOL]) == 1  # stable has nothing
    out = capsys.readouterr().out
    assert "cli change" in out

    assert cli.main(["fetch", "rp1jtag"]) == 0
    assert cli.main(["fetch", TOOL]) == 1
    assert "needs a track" in capsys.readouterr().err


def test_cli_compare_ok(fx, capsys):
    for track in ("stable", "master"):
        write_patch(patchset.series_dir(TOOL, track) / "0001-a.patch", ["Subject: [PATCH] same"])
    assert cli.main(["compare", TOOL]) == 0
    out = capsys.readouterr().out
    assert "same" in out and "==" in out
