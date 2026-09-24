from __future__ import annotations

import pytest

from fpgatools import REPO, pins
from fpgatools.cli import FpgatoolsError

from .conftest import FIXTURE_PINS

OFL_STABLE = "85be4fa02b2dd6a83716d7dfac3d25bbd260ff7b"
OCD_MASTER = "b04ccfeff73fb01b62e52e3d95882e8ff426016d"


@pytest.fixture(scope="module")
def fixture_pins() -> pins.Pins:
    return pins.load(FIXTURE_PINS)


def test_load_default_path_is_repo_upstreams_toml():
    assert pins.load() == pins.load(REPO / "upstreams.toml")


def test_real_pins_have_what_the_versions_need():
    """The real file, whatever the daily bump has moved it to."""
    real = pins.load()
    assert real.names == ("openfpgaloader", "openocd", "rp1jtag", "piolib")
    for tool in ("openfpgaloader", "openocd"):
        assert real.pin(tool, "master").describe is not None
        assert real.pin(tool, "stable").ref.startswith("v")
    assert real.pin("rp1jtag").describe is not None
    assert real.pin("piolib").date is not None


def test_names_in_file_order(fixture_pins):
    assert fixture_pins.names == ("openfpgaloader", "openocd", "rp1jtag", "piolib")


def test_url_and_tool(fixture_pins):
    assert fixture_pins.url("openfpgaloader") == "https://github.com/trabucayre/openFPGALoader.git"
    assert fixture_pins.tool("openocd") == pins.Upstream(
        url="https://github.com/openocd-org/openocd.git"
    )


def test_tracked_pins(fixture_pins):
    stable = fixture_pins.pin("openfpgaloader", "stable")
    assert stable == pins.Pin(ref="v1.1.1", commit=OFL_STABLE)
    assert stable.describe is None and stable.date is None and stable.subdir is None

    master = fixture_pins.pin("openocd", "master")
    assert master.ref == "master"
    assert master.commit == OCD_MASTER
    assert master.describe == "v0.12.0-1701-gb04ccfef"
    assert master.date == "2026-09-20"


def test_untracked_pins(fixture_pins):
    rp1 = fixture_pins.pin("rp1jtag")
    assert rp1.ref == "main"
    assert rp1.commit == "f91dfc702a433e115dcc2ced5accf176b1f189fc"
    assert rp1.describe == "v0.0-95-gf91dfc7" and rp1.date is None
    assert rp1.subdir is None
    assert fixture_pins.pin("piolib").subdir == "piolib"
    assert fixture_pins.tracked("openocd") is True
    assert fixture_pins.tracked("piolib") is False


@pytest.mark.parametrize(
    ("name", "track", "fragment"),
    [
        ("nope", None, "unknown upstream 'nope'"),
        ("openocd", None, "needs a track"),
        ("openocd", "beta", "unknown track 'beta'"),
        ("rp1jtag", "stable", "has no tracks"),
    ],
)
def test_pin_errors(fixture_pins, name, track, fragment):
    with pytest.raises(FpgatoolsError, match=fragment):
        fixture_pins.pin(name, track)


def test_url_unknown_name(fixture_pins):
    with pytest.raises(FpgatoolsError, match="unknown upstream 'nope'"):
        fixture_pins.url("nope")


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
