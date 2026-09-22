from __future__ import annotations

import pytest

from fpgatools import REPO, pins
from fpgatools.cli import FpgatoolsError

OFL_STABLE = "85be4fa02b2dd6a83716d7dfac3d25bbd260ff7b"
OCD_MASTER = "b04ccfeff73fb01b62e52e3d95882e8ff426016d"


@pytest.fixture(scope="module")
def real_pins() -> pins.Pins:
    return pins.load()


def test_load_default_path_is_repo_upstreams_toml(real_pins):
    assert pins.load(REPO / "upstreams.toml") == real_pins


def test_names_in_file_order(real_pins):
    assert real_pins.names == ("openfpgaloader", "openocd", "rp1jtag", "piolib")


def test_url_and_tool(real_pins):
    assert real_pins.url("openfpgaloader") == "https://github.com/trabucayre/openFPGALoader.git"
    assert real_pins.tool("openocd") == pins.Upstream(
        url="https://github.com/openocd-org/openocd.git"
    )


def test_tracked_pins(real_pins):
    stable = real_pins.pin("openfpgaloader", "stable")
    assert stable == pins.Pin(ref="v1.1.1", commit=OFL_STABLE)
    assert stable.describe is None and stable.date is None and stable.subdir is None

    master = real_pins.pin("openocd", "master")
    assert master.ref == "master"
    assert master.commit == OCD_MASTER
    assert master.describe == "v0.12.0-1701-gb04ccfef"
    assert master.date == "2026-09-20"


def test_untracked_pins(real_pins):
    rp1 = real_pins.pin("rp1jtag")
    assert rp1.ref == "main"
    assert rp1.commit == "d9d7d8d186f103bb16a3eafb9c7296d5e2a33c47"
    assert rp1.subdir is None
    assert real_pins.pin("piolib").subdir == "piolib"
    assert real_pins.tracked("openocd") is True
    assert real_pins.tracked("piolib") is False


@pytest.mark.parametrize(
    ("name", "track", "fragment"),
    [
        ("nope", None, "unknown upstream 'nope'"),
        ("openocd", None, "needs a track"),
        ("openocd", "beta", "unknown track 'beta'"),
        ("rp1jtag", "stable", "has no tracks"),
    ],
)
def test_pin_errors(real_pins, name, track, fragment):
    with pytest.raises(FpgatoolsError, match=fragment):
        real_pins.pin(name, track)


def test_url_unknown_name(real_pins):
    with pytest.raises(FpgatoolsError, match="unknown upstream 'nope'"):
        real_pins.url("nope")


def test_load_from_string_and_missing_fields(tmp_path):
    good = tmp_path / "good.toml"
    good.write_text(
        '[thing]\nurl = "file:///x"\n[thing.stable]\nref = "v1"\ncommit = "abc"\n'
        '[thing.master]\nref = "m"\ncommit = "def"\ndescribe = "v1-3-gdef"\ndate = "2026-01-01"\n'
    )
    loaded = pins.load(good)
    assert loaded.pin("thing", "master").describe == "v1-3-gdef"
    assert loaded.pin("thing", "stable").commit == "abc"

    no_url = tmp_path / "nourl.toml"
    no_url.write_text('[thing]\nref = "v1"\ncommit = "abc"\n')
    with pytest.raises(FpgatoolsError, match="thing.*url"):
        pins.load(no_url)

    no_commit = tmp_path / "nocommit.toml"
    no_commit.write_text('[thing]\nurl = "u"\n[thing.stable]\nref = "v1"\n')
    with pytest.raises(FpgatoolsError, match="thing.stable.*commit"):
        pins.load(no_commit)

    unknown_track = tmp_path / "track.toml"
    unknown_track.write_text('[thing]\nurl = "u"\n[thing.beta]\nref = "v1"\ncommit = "a"\n')
    with pytest.raises(FpgatoolsError, match="thing.beta"):
        pins.load(unknown_track)


def test_load_missing_file(tmp_path):
    with pytest.raises(FpgatoolsError, match="upstreams"):
        pins.load(tmp_path / "missing.toml")
