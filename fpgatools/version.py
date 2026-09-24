"""Version strings: this repo's own version and the derived package versions.

Nothing here is typed by hand (see CLAUDE.md "Versions are derived"):

* `R`, the repo version, comes from `git describe` against `vX.Y` series
  tags: `X.Y` on the tag, `X.Y.postN` N commits later, `0.0.post<count>`
  with no tag at all. This is the fpgas-online `deb-version.py` convention.
* The package (Debian) version is `<upstream base>+fpgasonline.<R>` on the
  stable track and `<upstream base>.post<N>+fpgasonline.<R>` on master,
  where `<upstream base>.post<N>` is the upstream `git describe` recorded in
  `upstreams.toml`: N commits after release tag `<upstream base>`. Master
  always carries `.post<N>`, even at N=0, so a master build can never share a
  version (or a static asset name) with the stable build of the same tag.
* The string each patched binary reports is derived from the same parts.
* librp1jtag0 is versioned the same way from its pin's describe, in the
  fpgas-online convention: `<tag>.post<N>+fpgasonline.<R>` (`<tag>` alone on
  the tag itself), continuing the `0.0.postN` versions mithro/rp1-jtag
  published under the same package name.
* Our libpio0 (sid only; Raspberry Pi's archive has it everywhere else) is the
  one date-versioned package: `<YYYYMMDD>+fpgasonline.<R>.g<sha7>`, the
  date-first shape Raspberry Pi's own libpio0 uses. raspberrypi/utils has no
  tags to describe against. R comes before the sha, so two pins with the same
  commit date order by R, which rises with every commit here.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from fpgatools import LIBS, REPO, TOOLS, TRACKS
from fpgatools.cli import FpgatoolsError
from fpgatools.gitutil import git_output, run_git
from fpgatools.pins import Pin, Pins, load

# `git describe --long` of a series tag: v0.0-12-g07d681f
_SERIES_DESCRIBE_RE = re.compile(r"^v(\d+\.\d+)-(\d+)-g[0-9a-f]+$")
# A repo version as produced by repo_version().
REPO_VERSION_RE = re.compile(r"^\d+\.\d+(\.post\d+)?$")
# An upstream `git describe --tags` result: v1.1.1-173-g24e46d1 or a bare tag v1.1.1.
_UPSTREAM_DESCRIBE_RE = re.compile(r"^v?(\d+(?:\.\d+)+)(?:-(\d+)-g([0-9a-f]+))?$")


class VersionError(FpgatoolsError, ValueError):
    """A version string that does not have the expected shape."""


def repo_version(repo: Path = REPO) -> str:
    """This repo's version `R` in the fpgas-online git-describe convention."""
    described = run_git(
        ["describe", "--tags", "--long", "--match", "v[0-9]*.[0-9]*"], cwd=repo, check=False
    )
    if described.returncode != 0:
        # No series tag reachable (or no repo at all: rev-list then raises).
        count = git_output(["rev-list", "--count", "HEAD"], cwd=repo)
        return f"0.0.post{count}"
    text = described.stdout.strip()
    m = _SERIES_DESCRIBE_RE.match(text)
    if not m:
        raise VersionError(
            f"git describe gave {text!r}; series tags must look like vX.Y (two numeric components)"
        )
    base, distance = m.group(1), int(m.group(2))
    return base if distance == 0 else f"{base}.post{distance}"


def parse_describe(describe: str) -> tuple[str, int, str | None]:
    """Split `v1.1.1-173-g24e46d1` into `("1.1.1", 173, "24e46d1")`.

    A bare tag (`v1.1.1`, distance 0) gives `("1.1.1", 0, None)`.
    """
    m = _UPSTREAM_DESCRIBE_RE.match(describe.strip())
    if not m:
        raise VersionError(f"cannot parse upstream describe {describe!r}")
    base, distance, sha = m.groups()
    return base, int(distance or 0), sha


def upstream_base(pin: Pin) -> str:
    """The upstream release the pin is based on, sans `v`: `1.1.1`.

    A pin with `describe` (master track) uses the tag part of the describe;
    otherwise the pin's `ref` must itself be the release tag.
    """
    return parse_describe(pin.describe if pin.describe is not None else pin.ref)[0]


def _check(tool: str, track: str) -> None:
    if tool not in TOOLS:
        raise FpgatoolsError(f"unknown tool {tool!r} (known: {', '.join(TOOLS)})")
    if track not in TRACKS:
        raise FpgatoolsError(f"unknown track {track!r} (known: {', '.join(TRACKS)})")


def _master_parts(tool: str, pin: Pin) -> tuple[str, int, str]:
    if pin.describe is None:
        raise FpgatoolsError(
            f"the {tool} master pin needs describe in upstreams.toml (fpgatools bump records it)"
        )
    base, distance, sha = parse_describe(pin.describe)
    if sha is None:
        raise FpgatoolsError(
            f"the {tool} master pin's describe {pin.describe!r} has no -N-g<sha> suffix"
        )
    return base, distance, sha


def package_version(tool: str, track: str, pins: Pins, repo_version: str) -> str:
    """The Debian version: `1.1.1+fpgasonline.0.0.post12` or
    `1.1.1.post173+fpgasonline.0.0.post12`."""
    _check(tool, track)
    pin = pins.pin(tool, track)
    if track == "stable":
        return f"{upstream_base(pin)}+fpgasonline.{repo_version}"
    base, distance, _sha = _master_parts(tool, pin)
    return f"{base}.post{distance}+fpgasonline.{repo_version}"


def tool_version_string(tool: str, track: str, pins: Pins, repo_version: str) -> str:
    """What the built binary should report.

    openFPGALoader takes the whole string (`v` + package version) through the
    `OPENFPGALOADER_VERSION` cmake variable. OpenOCD's `guess-rev.sh` only
    appends a local suffix to the `AC_INIT` version (`0.12.0` on a release,
    `0.12.0+dev` on master), so for OpenOCD this is that suffix: on stable
    `+fpgasonline.<R>`, on master `-<NNNNN>-g<sha>+fpgasonline.<R>` in the
    same shape guess-rev itself would produce from git.
    """
    _check(tool, track)
    if tool == "openfpgaloader":
        return "v" + package_version(tool, track, pins, repo_version)
    if track == "stable":
        return f"+fpgasonline.{repo_version}"
    _base, distance, sha = _master_parts(tool, pins.pin(tool, track))
    return f"-{distance:05d}-g{sha}+fpgasonline.{repo_version}"


def _pin_date(name: str, pin: Pin) -> str:
    if pin.date is None:
        raise FpgatoolsError(
            f"the {name} pin needs a date in upstreams.toml (fpgatools bump records it)"
        )
    return pin.date.replace("-", "")


def library_version(name: str, pins: Pins, repo_version: str) -> str:
    """The Debian version of a packaged library.

    librp1jtag0: `0.0.post95+fpgasonline.0.0.post49`, from the pin's
    `git describe` (`v0.0-95-gf91dfc7`). It continues the 0.0.postN versions
    mithro/rp1-jtag published under the same package name, and sorts above
    them once the pin is past the last one it published (0.0.post87).

    libpio0: `20260914+fpgasonline.0.0.post49.gebc4a56`. raspberrypi/utils has
    no tags; Raspberry Pi version their libpio0 by date, and so do we.
    """
    if name not in LIBS:
        raise FpgatoolsError(f"unknown library {name!r} (known: {', '.join(LIBS)})")
    pin = pins.pin(name)
    if name == "rp1jtag":
        if pin.describe is None:
            raise FpgatoolsError(
                "the rp1jtag pin needs describe in upstreams.toml (fpgatools bump records it)"
            )
        base, distance, _sha = parse_describe(pin.describe)
        upstream = base if distance == 0 else f"{base}.post{distance}"
        return f"{upstream}+fpgasonline.{repo_version}"
    return f"{_pin_date(name, pin)}+fpgasonline.{repo_version}.g{pin.commit[:7]}"


# --- CLI ------------------------------------------------------------------------


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "version",
        help="print the derived version strings",
        description="Print the Debian package version for a tool/track "
        "(default), the string the binary should report (--tool-string), "
        "or this repo's own version R (--repo).",
    )
    p.add_argument("tool", nargs="?", help="openfpgaloader or openocd")
    p.add_argument("track", nargs="?", help="stable or master")
    p.add_argument(
        "--tool-string",
        action="store_true",
        help="print the version string the binary reports instead of the package version",
    )
    p.add_argument(
        "--repo",
        "--repo-version",
        dest="repo",
        action="store_true",
        help="print this repo's version R alone",
    )
    p.set_defaults(func=_run, parser=p)


def _run(args: argparse.Namespace) -> int:
    r = repo_version()
    if args.repo:
        print(r)
        return 0
    if args.tool is None or args.track is None:
        args.parser.error("version needs <tool> <track> (or --repo)")
    pins = load()
    if args.tool_string:
        print(tool_version_string(args.tool, args.track, pins, r))
    else:
        print(package_version(args.tool, args.track, pins, r))
    return 0
