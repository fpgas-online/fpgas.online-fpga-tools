from pathlib import Path

import pytest

from fpgatools import debianize
from fpgatools.cli import FpgatoolsError
from fpgatools.pins import load

DATE = "Tue, 22 Sep 2026 10:00:00 +0930"


@pytest.fixture
def pins():
    return load()


def test_package_names():
    assert debianize.package_name("openfpgaloader", "stable") == "openfpgaloader-fpgasonline"
    assert debianize.package_name("openfpgaloader", "master") == "openfpgaloader-fpgasonline-git"
    assert debianize.package_name("openocd", "stable") == "openocd-fpgasonline"
    assert debianize.package_name("openocd", "master") == "openocd-fpgasonline-git"
    with pytest.raises(FpgatoolsError):
        debianize.package_name("gcc", "stable")
    with pytest.raises(FpgatoolsError):
        debianize.package_name("openocd", "beta")


def test_siblings_conflict():
    assert debianize.sibling_packages("openocd", "stable") == ["openocd-fpgasonline-git"]
    assert debianize.sibling_packages("openocd", "master") == ["openocd-fpgasonline"]


@pytest.mark.parametrize(
    "tool,track,expect_pkg,expect_ver,expect_toolver",
    [
        ("openfpgaloader", "stable", "openfpgaloader-fpgasonline",
         "1.1.1+fpgasonline.0.0.post12", "v1.1.1+fpgasonline.0.0.post12"),
        ("openfpgaloader", "master", "openfpgaloader-fpgasonline-git",
         "1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12",
         "v1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12"),
        ("openocd", "stable", "openocd-fpgasonline",
         "0.12.0+fpgasonline.0.0.post12", "+fpgasonline.0.0.post12"),
        ("openocd", "master", "openocd-fpgasonline-git",
         "0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post12",
         "-01701-gb04ccfef+fpgasonline.0.0.post12"),
    ],
)
def test_render(tmp_path: Path, pins, tool, track, expect_pkg, expect_ver, expect_toolver):
    debian = debianize.render(tool, track, pins, "0.0.post12", tmp_path, date_rfc2822=DATE)
    control = (debian / "control").read_text()
    assert f"Source: {expect_pkg}\n" in control
    assert f"Package: {expect_pkg}\n" in control
    sibling = debianize.sibling_packages(tool, track)[0]
    assert f"Conflicts: {tool}, {tool}-rp1pio, {sibling}\n" in control
    assert "@" not in control.replace("me@mith.ro", "")
    rules = (debian / "rules").read_text()
    assert expect_toolver in rules
    assert (debian / "rules").stat().st_mode & 0o111
    assert (debian / "source" / "format").read_text() == "3.0 (native)\n"
    changelog = (debian / "changelog").read_text()
    assert changelog.splitlines()[0] == f"{expect_pkg} ({expect_ver}) unstable; urgency=medium"
    assert changelog.rstrip().endswith(DATE)
    assert "control.in" not in {p.name for p in debian.iterdir()}


def test_unsubstituted_token_is_an_error(tmp_path: Path, pins):
    templates = tmp_path / "templates" / "openocd"
    templates.mkdir(parents=True)
    (templates / "control.in").write_text("Package: @PACKAGE@\nX: @UPSTREAM_REF@ @TOOL_VERSION@\n")
    (templates / "weird").write_text("@VERSION@ ok but @CONFLICTS@ and @PACKAGE@ fine\n")
    out = tmp_path / "tree"
    debianize.render("openocd", "stable", pins, "0.0", out,
                     templates_root=tmp_path / "templates", date_rfc2822=DATE)
    assert "@" not in (out / "debian" / "weird").read_text()
    (templates / "bad").write_text("@TRACK@ and @NOPE@\n")
    # A token nothing substitutes is a template typo: refuse to render.
    with pytest.raises(FpgatoolsError, match="@NOPE@"):
        debianize.render("openocd", "stable", pins, "0.0", out,
                         templates_root=tmp_path / "templates", date_rfc2822=DATE)
    # An e-mail address is not a token.
    (templates / "bad").write_text("Maintainer: Someone <me@mith.ro>\n")
    debianize.render("openocd", "stable", pins, "0.0", out,
                     templates_root=tmp_path / "templates", date_rfc2822=DATE)


def _lib_tree(tmp_path: Path, name: str) -> Path:
    """A stand-in for the copy of a fetched library tree, with the upstream
    debian/ rp1-jtag carries (which the rendered one must replace)."""
    tree = tmp_path / name
    (tree / "debian").mkdir(parents=True)
    (tree / "debian" / "librp1jtag0.install").write_text("usr/lib/*/librp1jtag.so.*\n")
    (tree / "debian" / "upstream-only").write_text("x\n")
    (tree / "CMakeLists.txt").write_text("project(rp1-jtag VERSION 0.1.0 LANGUAGES C)\n")
    return tree


@pytest.mark.parametrize(
    "name,source,packages,version_prefix",
    [
        ("rp1jtag", "rp1-jtag-fpgasonline", ["librp1jtag0", "librp1jtag-dev"], "0.1.0+git"),
        ("piolib", "piolib-fpgasonline", ["libpio0", "libpio-dev"], "20"),
    ],
)
def test_render_library(tmp_path: Path, pins, name, source, packages, version_prefix):
    tree = _lib_tree(tmp_path, name)
    debian = debianize.render_library(name, pins, "0.0.post43", tree, date_rfc2822=DATE)
    control = (debian / "control").read_text()
    assert f"Source: {source}\n" in control
    for pkg in packages:
        assert f"Package: {pkg}\n" in control
    assert "@" not in control.replace("me@mith.ro", "")
    # rp1-jtag's own debian/ is gone, not merged with ours.
    assert not (debian / "upstream-only").exists()
    assert (debian / "rules").stat().st_mode & 0o111
    assert (debian / "source" / "format").read_text() == "3.0 (native)\n"
    first = (debian / "changelog").read_text().splitlines()[0]
    assert first.startswith(f"{source} ({version_prefix}")
    assert "+fpgasonline.0.0.post43.g" in first
    assert first.endswith(") unstable; urgency=medium")


def test_render_rp1jtag_exports_only_its_api(tmp_path: Path, pins):
    debian = debianize.render_library("rp1jtag", pins, "0.0", _lib_tree(tmp_path, "rp1jtag"),
                                      date_rfc2822=DATE)
    script = (debian / "librp1jtag.map").read_text()
    assert "global: rp1_jtag_*;" in script
    assert "local: *;" in script
    assert "librp1jtag.map" in (debian / "rules").read_text()


def test_render_library_rejects_unknown(tmp_path: Path, pins):
    with pytest.raises(FpgatoolsError, match="unknown library"):
        debianize.render_library("openocd", pins, "0.0", tmp_path, date_rfc2822=DATE)
