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
    # An unknown token is left alone (not ours), so this must render.
    debianize.render("openocd", "stable", pins, "0.0", out,
                     templates_root=tmp_path / "templates", date_rfc2822=DATE)
    assert "@NOPE@" in (out / "debian" / "bad").read_text()
