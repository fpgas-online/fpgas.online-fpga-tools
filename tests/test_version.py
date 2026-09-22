from __future__ import annotations

import pytest

from fpgatools import cli, pins, version
from fpgatools.cli import FpgatoolsError
from fpgatools.pins import Pin

from .conftest import commit_file, git, make_repo

R = "0.0.post12"


@pytest.fixture(scope="module")
def real_pins() -> pins.Pins:
    return pins.load()


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
        ("openfpgaloader", "master", "1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12"),
        ("openocd", "stable", "0.12.0+fpgasonline.0.0.post12"),
        ("openocd", "master", "0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post12"),
    ],
)
def test_package_version(real_pins, tool, track, expected):
    assert version.package_version(tool, track, real_pins, R) == expected


def test_package_version_rejects_unknown_tool_or_track(real_pins):
    with pytest.raises(FpgatoolsError, match="unknown tool"):
        version.package_version("rp1jtag", "stable", real_pins, R)
    with pytest.raises(FpgatoolsError, match="unknown track"):
        version.package_version("openocd", "beta", real_pins, R)


def test_package_version_master_needs_describe_and_date():
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
        ("openfpgaloader", "master", "v1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12"),
        ("openocd", "stable", "+fpgasonline.0.0.post12"),
        ("openocd", "master", "-01701-gb04ccfef+fpgasonline.0.0.post12"),
    ],
)
def test_tool_version_string(real_pins, tool, track, expected):
    assert version.tool_version_string(tool, track, real_pins, R) == expected


# --- CLI ----------------------------------------------------------------------


def test_cli_version(capsys, real_pins):
    r = version.repo_version()
    assert cli.main(["version", "--repo"]) == 0
    assert capsys.readouterr().out == r + "\n"

    assert cli.main(["version", "openfpgaloader", "stable"]) == 0
    assert capsys.readouterr().out == f"1.1.1+fpgasonline.{r}\n"

    assert cli.main(["version", "openocd", "master", "--tool-string"]) == 0
    assert capsys.readouterr().out == f"-01701-gb04ccfef+fpgasonline.{r}\n"


def test_cli_version_errors(capsys):
    assert cli.main(["version", "openocd", "beta"]) == 1
    assert "unknown track" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["version", "openocd"])  # track missing
