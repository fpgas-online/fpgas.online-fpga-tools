import importlib.util
from pathlib import Path

from fpgatools import REPO

spec = importlib.util.spec_from_file_location("release", REPO / "packaging" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

A = "openFPGALoader-1.1.1+fpgasonline.0.0.post12-linux-arm64.tar.gz"
B = "openFPGALoader-1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12-linux-armv7.tar.gz"
C = "openocd-0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post13-linux-arm64.tar.gz"
D = "openocd-0.12.0+fpgasonline.0.0.post9-linux-armv6.tar.gz"


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


def test_script_is_executable_python(tmp_path: Path):
    assert (REPO / "packaging" / "release.py").read_text().startswith("#!/usr/bin/env python3")
