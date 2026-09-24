import importlib.util
from pathlib import Path

import pytest

from fpgatools import REPO

spec = importlib.util.spec_from_file_location("release", REPO / "packaging" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

A = "openFPGALoader-1.1.1+fpgasonline.0.0.post12-linux-arm64.tar.gz"
B = "openFPGALoader-1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12-linux-armv7.tar.gz"
C = "openocd-0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post13-linux-arm64.tar.gz"
D = "openocd-0.12.0+fpgasonline.0.0.post9-linux-armv6.tar.gz"
E = "openocd-0.12.0.post1701+fpgasonline.0.0.post49-linux-arm64.tar.gz"


def test_classify():
    assert release.classify(A) == {"tool": "openfpgaloader", "track": "stable",
                                   "version": "1.1.1+fpgasonline.0.0.post12", "arch": "arm64",
                                   "file": A}
    assert release.classify(B)["track"] == "master"
    assert release.classify(C) == {"tool": "openocd", "track": "master",
                                   "version": "0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post13",
                                   "arch": "arm64", "file": C}
    assert release.classify(A + ".sha256") is None
    assert release.classify("latest.json") is None
    assert release.classify("openocd-foo-linux-mips.tar.gz") is None
    # a bare binary is no longer published
    assert release.classify("openFPGALoader-1.1.1+fpgasonline.0.0.post1-linux-arm64") is None


def test_track_of_describe_and_legacy_date_versions():
    assert release.track_of("1.1.1+fpgasonline.0.0.post12") == "stable"
    assert release.track_of("0.12.0+fpgasonline.0.1") == "stable"
    assert release.track_of("1.1.1.post173+fpgasonline.0.0.post49") == "master"
    assert release.track_of("1.1.1.post0+fpgasonline.0.0.post49") == "master"
    # published before the switch to git-describe versions
    assert release.track_of("0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post13") == "master"
    # the repo's own .postN is not the upstream's
    assert release.track_of("1.1.1+fpgasonline.0.0.post9") == "stable"


def test_version_key_orders_by_patchset_revision():
    k = release.version_key
    assert k("1.1.1+fpgasonline.0.0.post12") < k("1.1.1+fpgasonline.0.0.post13")
    assert k("1.1.1+fpgasonline.0.0.post9") < k("1.1.1+fpgasonline.0.0.post12")
    assert k("1.1.1+fpgasonline.0.0.post99") < k("1.1.1+fpgasonline.0.1")
    assert k("0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post13") == (0, 0, 13)


def test_latest_index_picks_newest_per_track_tool_arch():
    older = "openocd-0.12.0+fpgasonline.0.0.post8-linux-armv6.tar.gz"
    idx = release.latest_index([A, B, C, D, older, A + ".sha256", "latest.json"])
    assert idx["stable"]["openfpgaloader"]["arm64"] == {
        "asset": A, "version": "1.1.1+fpgasonline.0.0.post12"}
    assert idx["master"]["openfpgaloader"]["armv7"]["asset"] == B
    assert idx["master"]["openocd"]["arm64"]["asset"] == C
    assert idx["stable"]["openocd"]["armv6"]["asset"] == D
    assert "arm64" not in idx["stable"]["openocd"]
    # a describe-versioned master build replaces the older date-versioned one
    idx = release.latest_index([C, E])
    assert idx["master"]["openocd"]["arm64"]["asset"] == E


def test_collect_assets_walks_one_directory_per_artifact(tmp_path: Path):
    for art, name in [("static-a", A), ("static-b", B)]:
        (tmp_path / art).mkdir()
        (tmp_path / art / name).write_text("x")
        (tmp_path / art / (name + ".sha256")).write_text("x")
    (tmp_path / "static-a" / "notes.txt").write_text("x")
    got = [p.name for p in release.collect_assets(tmp_path)]
    assert got == sorted([A, A + ".sha256", B, B + ".sha256"])


def test_collect_assets_refuses_two_builds_with_one_name(tmp_path: Path):
    # Two builds claiming one asset name (the armv7 build calling itself
    # arm64) must stop the upload, not let one overwrite the other.
    for art in ("static-openfpgaloader-stable-arm64", "static-openfpgaloader-stable-armv7"):
        (tmp_path / art).mkdir()
        (tmp_path / art / A).write_text(art)
    with pytest.raises(SystemExit, match="more than one build"):
        release.collect_assets(tmp_path)


DBG = "bookworm_openfpgaloader-fpgasonline-dbgsym_1.1.1+fpgasonline.0.0.post62_arm64.deb"


def test_dbgsym_debs_are_assets_but_not_indexed():
    assert release.is_asset(DBG)
    assert release.is_asset("raspbian-trixie_openocd-fpgasonline-git-dbgsym_"
                            "0.12.0.post1701+fpgasonline.0.0.post62_armhf.deb")
    assert release.is_asset(A) and release.is_asset(A + ".sha256")
    # without the suite prefix every suite's build would claim one name
    assert not release.is_asset(DBG.removeprefix("bookworm_"))
    # only debug symbols: the packages themselves are in the apt repository
    assert not release.is_asset(DBG.replace("-dbgsym_", "_"))
    assert release.classify(DBG) is None
    assert release.latest_index([A, DBG]) == release.latest_index([A])


def test_collect_assets_takes_dbgsym_debs(tmp_path: Path):
    (tmp_path / "dbgsym-bookworm-arm64-openfpgaloader-stable").mkdir()
    (tmp_path / "dbgsym-bookworm-arm64-openfpgaloader-stable" / DBG).write_text("x")
    assert [p.name for p in release.collect_assets(tmp_path)] == [DBG]


def test_script_is_executable_python(tmp_path: Path):
    assert (REPO / "packaging" / "release.py").read_text().startswith("#!/usr/bin/env python3")
