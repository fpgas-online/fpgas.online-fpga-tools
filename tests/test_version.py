from __future__ import annotations

import pytest

from fpgatools import cli, pins, version
from fpgatools.cli import FpgatoolsError
from fpgatools.pins import Pin

from .conftest import FIXTURE_PINS, commit_file, git, make_repo

R = "0.0.post12"


@pytest.fixture(scope="module")
def fixture_pins() -> pins.Pins:
    return pins.load(FIXTURE_PINS)


# --- repo_version -----------------------------------------------------------


def test_repo_version_no_tag(tmp_path):
    repo = make_repo(tmp_path / "r")
    commit_file(repo, "a", "1\n", "one")
    assert version.repo_version(repo) == "0.0.post1"
    commit_file(repo, "a", "2\n", "two")
    assert version.repo_version(repo) == "0.0.post2"


def test_repo_version_tag_on_head_and_after(tmp_path):
    repo = make_repo(tmp_path / "r")
    commit_file(repo, "a", "1\n", "one")
    git(repo, "tag", "v0.0")
    assert version.repo_version(repo) == "0.0"
    commit_file(repo, "a", "2\n", "two")
    assert version.repo_version(repo) == "0.0.post1"
    git(repo, "tag", "-a", "-m", "release", "v1.2")
    assert version.repo_version(repo) == "1.2"
    commit_file(repo, "a", "3\n", "three")
    assert version.repo_version(repo) == "1.2.post1"


def test_repo_version_ignores_non_series_tags(tmp_path):
    repo = make_repo(tmp_path / "r")
    commit_file(repo, "a", "1\n", "one")
    git(repo, "tag", "v0.0")
    commit_file(repo, "a", "2\n", "two")
    git(repo, "tag", "deploy-2026")  # does not match v[0-9]*.[0-9]*
    assert version.repo_version(repo) == "0.0.post1"


def test_repo_version_invalid_describe_raises_valueerror(tmp_path):
    repo = make_repo(tmp_path / "r")
    commit_file(repo, "a", "1\n", "one")
    git(repo, "tag", "v1.2.3")  # three components: not a series tag
    with pytest.raises(ValueError, match="v1.2.3"):
        version.repo_version(repo)


def test_repo_version_not_a_repo(tmp_path):
    with pytest.raises(FpgatoolsError):
        version.repo_version(tmp_path)


def test_repo_version_of_this_repo_has_the_shape():
    assert version.REPO_VERSION_RE.fullmatch(version.repo_version())


# --- upstream_base / parse_describe ------------------------------------------


def test_upstream_base():
    assert version.upstream_base(Pin(ref="v1.1.1", commit="x" * 40)) == "1.1.1"
    assert version.upstream_base(Pin(ref="0.12.0", commit="x" * 40)) == "0.12.0"
    master = Pin(ref="master", commit="24e46d13" + "0" * 32, describe="v1.1.1-173-g24e46d1")
    assert version.upstream_base(master) == "1.1.1"
    exact = Pin(ref="master", commit="x" * 40, describe="v0.12.0")
    assert version.upstream_base(exact) == "0.12.0"


def test_parse_describe():
    assert version.parse_describe("v0.12.0-1701-gb04ccfef") == ("0.12.0", 1701, "b04ccfef")
    assert version.parse_describe("v1.1.1") == ("1.1.1", 0, None)
    with pytest.raises(ValueError, match="junk"):
        version.parse_describe("junk")
    with pytest.raises(ValueError):
        version.upstream_base(Pin(ref="master", commit="x" * 40))


# --- package_version ----------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "track", "expected"),
    [
        ("openfpgaloader", "stable", "1.1.1+fpgasonline.0.0.post12"),
        ("openfpgaloader", "master", "1.1.1.post173+fpgasonline.0.0.post12"),
        ("openocd", "stable", "0.12.0+fpgasonline.0.0.post12"),
        ("openocd", "master", "0.12.0.post1701+fpgasonline.0.0.post12"),
    ],
)
def test_package_version(fixture_pins, tool, track, expected):
    assert version.package_version(tool, track, fixture_pins, R) == expected


def test_package_version_rejects_unknown_tool_or_track(fixture_pins):
    with pytest.raises(FpgatoolsError, match="unknown tool"):
        version.package_version("rp1jtag", "stable", fixture_pins, R)
    with pytest.raises(FpgatoolsError, match="unknown track"):
        version.package_version("openocd", "beta", fixture_pins, R)


def test_package_version_master_needs_describe():
    bare = pins.Pins.from_dict(
        {
            "openocd": {
                "url": "u",
                "stable": {"ref": "v0.12.0", "commit": "a" * 40},
                "master": {"ref": "master", "commit": "b" * 40},
            }
        }
    )
    with pytest.raises(FpgatoolsError, match="describe"):
        version.package_version("openocd", "master", bare, R)


# --- tool_version_string --------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "track", "expected"),
    [
        ("openfpgaloader", "stable", "v1.1.1+fpgasonline.0.0.post12"),
        ("openfpgaloader", "master", "v1.1.1.post173+fpgasonline.0.0.post12"),
        ("openocd", "stable", "+fpgasonline.0.0.post12"),
        ("openocd", "master", "-01701-gb04ccfef+fpgasonline.0.0.post12"),
    ],
)
def test_tool_version_string(fixture_pins, tool, track, expected):
    assert version.tool_version_string(tool, track, fixture_pins, R) == expected


# --- CLI ----------------------------------------------------------------------


def test_cli_version(capsys):
    """The CLI reads the real upstreams.toml."""
    r = version.repo_version()
    real = pins.load()
    assert cli.main(["version", "--repo"]) == 0
    assert capsys.readouterr().out == r + "\n"

    assert cli.main(["version", "openfpgaloader", "stable"]) == 0
    expected = version.package_version("openfpgaloader", "stable", real, r)
    assert capsys.readouterr().out == expected + "\n"

    assert cli.main(["version", "openocd", "master", "--tool-string"]) == 0
    expected = version.tool_version_string("openocd", "master", real, r)
    assert capsys.readouterr().out == expected + "\n"


def test_cli_version_errors(capsys):
    assert cli.main(["version", "openocd", "beta"]) == 1
    assert "unknown track" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["version", "openocd"])  # track missing


def _dpkg_gt(a: str, b: str) -> bool:
    import shutil
    import subprocess

    if shutil.which("dpkg") is None:
        pytest.skip("needs dpkg --compare-versions")
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def _with_pin(name, track, **changes):
    """The real pins with one pin's fields replaced."""
    import dataclasses

    real = pins.load(FIXTURE_PINS)
    data = {}
    for n in real.names:
        entry = {"url": real.url(n)}
        if real.tracked(n):
            for t in ("stable", "master"):
                pin = real.pin(n, t)
                if (n, t) == (name, track):
                    pin = dataclasses.replace(pin, **changes)
                entry[t] = {k: v for k, v in dataclasses.asdict(pin).items() if v is not None}
        else:
            pin = real.pin(n)
            if (n, None) == (name, track):
                pin = dataclasses.replace(pin, **changes)
            entry.update({k: v for k, v in dataclasses.asdict(pin).items() if v is not None})
        data[n] = entry
    return pins.Pins.from_dict(data)


def test_master_versions_upgrade_the_published_date_versions(fixture_pins):
    """Before versions followed git describe, master builds were published as
    <tag>+git<date>.<sha7>+fpgasonline.<R>; the describe versions replace them."""
    assert _dpkg_gt(version.package_version("openocd", "master", fixture_pins, "0.0.post49"),
                    "0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post47")
    assert _dpkg_gt(version.package_version("openfpgaloader", "master", fixture_pins, "0.0.post49"),
                    "1.1.1+git20260915.24e46d1+fpgasonline.0.0.post48")


def test_master_on_a_release_tag_still_says_post0():
    """Master at a tag must not share the stable build's version (or asset name)."""
    on_tag = _with_pin("openfpgaloader", "master", describe="v1.1.1-0-g85be4fa")
    master = version.package_version("openfpgaloader", "master", on_tag, R)
    assert master == f"1.1.1.post0+fpgasonline.{R}"
    assert master != version.package_version("openfpgaloader", "stable", on_tag, R)
    assert _dpkg_gt(master, version.package_version("openfpgaloader", "stable", on_tag, R))


def test_master_orders_by_upstream_commit_count():
    """Two bumps on one day: the later upstream commit has the larger count."""
    older = _with_pin("openocd", "master", describe="v0.12.0-1701-gffffffff")
    newer = _with_pin("openocd", "master", describe="v0.12.0-1702-g00000000")
    assert _dpkg_gt(version.package_version("openocd", "master", newer, R),
                    version.package_version("openocd", "master", older, R))


# --- library_version ------------------------------------------------------------


def test_library_version_rp1jtag(fixture_pins):
    base, distance, _sha = version.parse_describe(fixture_pins.pin("rp1jtag").describe)
    v = version.library_version("rp1jtag", fixture_pins, "0.0.post43")
    assert v == f"{base}.post{distance}+fpgasonline.0.0.post43"


def test_library_version_rp1jtag_on_a_tag():
    on_tag = _with_pin("rp1jtag", None, describe="v0.1")
    assert version.library_version("rp1jtag", on_tag, "0.0.post43") == "0.1+fpgasonline.0.0.post43"


def test_library_version_rp1jtag_sorts_above_rp1_jtags_own_packages(fixture_pins):
    """mithro/rp1-jtag published librp1jtag0 up to 0.0.post87 under the same name."""
    assert _dpkg_gt(version.library_version("rp1jtag", fixture_pins, "0.0"), "0.0.post87")


def test_library_version_rp1jtag_orders_by_upstream_commit_count():
    older = _with_pin("rp1jtag", None, describe="v0.0-95-gfffffff")
    newer = _with_pin("rp1jtag", None, describe="v0.0-96-g0000000")
    assert _dpkg_gt(version.library_version("rp1jtag", newer, "0.0.post49"),
                    version.library_version("rp1jtag", older, "0.0.post49"))


def test_library_version_piolib_orders_by_repo_version_not_sha():
    """Two bumps on one upstream date: the later one, with a higher R, must
    sort higher whatever the shas are."""
    older = _with_pin("piolib", None, commit="f" * 40)
    newer = _with_pin("piolib", None, commit="0" * 40)
    assert _dpkg_gt(version.library_version("piolib", newer, "0.0.post49"),
                    version.library_version("piolib", older, "0.0.post48"))


def test_library_version_piolib(fixture_pins):
    pin = fixture_pins.pin("piolib")
    date = pin.date.replace("-", "")
    v = version.library_version("piolib", fixture_pins, "1.2")
    assert v == f"{date}+fpgasonline.1.2.g{pin.commit[:7]}"


def test_library_version_errors():
    real = pins.load(FIXTURE_PINS)
    with pytest.raises(FpgatoolsError, match="unknown library"):
        version.library_version("gcc", real, "0.0")
    with pytest.raises(FpgatoolsError, match="needs describe"):
        version.library_version("rp1jtag", _with_pin("rp1jtag", None, describe=None), "0.0")
    with pytest.raises(version.VersionError):
        version.library_version("rp1jtag", _with_pin("rp1jtag", None, describe="junk"), "0.0")
    with pytest.raises(FpgatoolsError, match="needs a date"):
        version.library_version("piolib", _with_pin("piolib", None, date=None), "0.0")
