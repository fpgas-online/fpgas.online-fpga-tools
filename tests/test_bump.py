import tomllib
from pathlib import Path

import pytest

from fpgatools import REPO, bump
from fpgatools.cli import FpgatoolsError
from fpgatools.pins import Pin, load
from tests.conftest import git


def test_newest_release_tag():
    tags = ["v1.0.0", "v1.1.1", "v2.0.0-rc1", "nightly", "v0.9", "v1.1.0", "v0.13.1"]
    assert bump.newest_release_tag(tags) == "v1.1.1"
    assert bump.newest_release_tag(["v0.12.0", "v0.12.0-rc3", "v0.11.0-rc2"]) == "v0.12.0"
    assert bump.newest_release_tag(["nightly"]) is None
    # numeric, not lexicographic
    assert bump.newest_release_tag(["v0.9.0", "v0.10.0"]) == "v0.10.0"


def _update(pins, name, track, **changes):
    old = pins.pin(name, track)
    new = Pin(ref=changes.get("ref", old.ref), commit=changes.get("commit", old.commit),
              describe=changes.get("describe", old.describe), date=changes.get("date", old.date),
              subdir=old.subdir)
    return bump.PinUpdate(name, track, old, new)


def test_rewrite_pins_real_file():
    text = (REPO / "upstreams.toml").read_text()
    pins = load()
    updates = [
        _update(pins, "openfpgaloader", "master", commit="a" * 40,
                describe="v1.1.1-200-gaaaaaaa", date="2026-10-01"),
        _update(pins, "openfpgaloader", "stable", ref="v1.2.0", commit="b" * 40),
        _update(pins, "openocd", "master"),  # unchanged
        _update(pins, "rp1jtag", None, commit="c" * 40),
    ]
    out = bump.rewrite_pins(text, updates)
    data = tomllib.loads(out)
    assert data["openfpgaloader"]["master"]["commit"] == "a" * 40
    assert data["openfpgaloader"]["master"]["describe"] == "v1.1.1-200-gaaaaaaa"
    assert data["openfpgaloader"]["master"]["date"] == "2026-10-01"
    assert data["openfpgaloader"]["stable"] == {"ref": "v1.2.0", "commit": "b" * 40}
    assert data["openocd"]["master"] == tomllib.loads(text)["openocd"]["master"]
    assert data["rp1jtag"]["commit"] == "c" * 40
    assert data["rp1jtag"]["ref"] == "main"
    # comments and everything else survive
    assert out.count("#") == text.count("#")
    assert out.splitlines()[0] == text.splitlines()[0]
    bump.verify_rewrite(out, updates)


def test_rewrite_pins_missing_table_or_line():
    pins = load()
    u = _update(pins, "openocd", "stable", commit="d" * 40)
    with pytest.raises(FpgatoolsError):
        bump.rewrite_pins("[other]\nref = \"x\"\n", [u])
    with pytest.raises(FpgatoolsError):
        bump.rewrite_pins("[openocd.stable]\nref = \"v0.12.0\"\n", [u])


def test_markdown_report():
    pins = load()
    r = bump.BumpReport(updates=[_update(pins, "openocd", "master", commit="e" * 40,
                                         describe="v0.12.0-1800-geeeeeee", date="2026-10-05")])
    r.applied[("openocd", "master")] = "OK"
    r.applied[("openocd", "stable")] = "CONFLICT: 0002-foo.patch"
    md = r.markdown()
    assert "openocd.master: b04ccfe -> eeeeeee (v0.12.0-1800-geeeeeee, 2026-10-05)" in md
    assert "✅ `openocd/master`: OK" in md
    assert "❌ `openocd/stable`: CONFLICT: 0002-foo.patch" in md
    assert r.changed


def test_bump_against_local_upstreams(tmp_path: Path):
    """End to end with file:// upstreams: pins move to the new head and tag."""
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "master")
    (up / "f").write_text("1\n")
    git(up, "add", "f")
    git(up, "commit", "-q", "-m", "one")
    git(up, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    first = git(up, "rev-parse", "HEAD").strip()
    (up / "f").write_text("2\n")
    git(up, "commit", "-q", "-am", "two")
    git(up, "tag", "-a", "v1.1.0", "-m", "v1.1.0")
    (up / "f").write_text("3\n")
    git(up, "commit", "-q", "-am", "three")
    head = git(up, "rev-parse", "HEAD").strip()
    date = git(up, "log", "-1", "--format=%cd", "--date=short").strip()

    lib = tmp_path / "lib"
    lib.mkdir()
    git(lib, "init", "-q", "-b", "main")
    (lib / "g").write_text("x\n")
    git(lib, "add", "g")
    git(lib, "commit", "-q", "-m", "lib")
    libhead = git(lib, "rev-parse", "HEAD").strip()

    pins_file = tmp_path / "upstreams.toml"
    pins_file.write_text(f"""# test pins
[openfpgaloader]
url = "file://{up}"
[openfpgaloader.stable]
ref = "v1.0.0"
commit = "{first}"
[openfpgaloader.master]
ref = "master"
commit = "{first}"
describe = "v1.0.0"
date = "2020-01-01"

[openocd]
url = "file://{up}"
[openocd.stable]
ref = "v1.0.0"
commit = "{first}"
[openocd.master]
ref = "master"
commit = "{first}"
describe = "v1.0.0"
date = "2020-01-01"

[rp1jtag]
url = "file://{lib}"
ref = "main"
commit = "{'0' * 40}"

[piolib]
url = "file://{lib}"
ref = "main"
commit = "{libhead}"
subdir = "piolib"
""")
    report = bump.bump(pins_file, meta_root=tmp_path / "meta", apply_series=False)
    assert report.changed
    data = tomllib.loads(pins_file.read_text())
    assert data["openfpgaloader"]["master"]["commit"] == head
    assert data["openfpgaloader"]["master"]["describe"].startswith("v1.1.0-1-g")
    assert data["openfpgaloader"]["master"]["date"] == date
    v110 = git(up, "rev-parse", "v1.1.0^{commit}")
    assert data["openfpgaloader"]["stable"] == {"ref": "v1.1.0", "commit": v110}
    assert data["rp1jtag"]["commit"] == libhead
    assert data["piolib"]["commit"] == libhead  # unchanged, still correct
    # a second run is a no-op
    report2 = bump.bump(pins_file, meta_root=tmp_path / "meta", apply_series=False)
    assert not report2.changed
