"""Render packaging/debian/<tool>/ into a patched source tree as debian/.

The templates carry @PLACEHOLDER@ tokens for everything that differs between
tools, tracks and builds, so one set of templates serves both tracks and the
binary package name, version and Conflicts are derived rather than typed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from fpgatools import REPO, TOOLS, TRACKS
from fpgatools.cli import FpgatoolsError
from fpgatools.gitutil import run_git
from fpgatools.pins import Pins, load
from fpgatools.version import package_version, repo_version, tool_version_string

TEMPLATES_ROOT = REPO / "packaging" / "debian"
MAINTAINER = "Tim 'mithro' Ansell <me@mith.ro>"
PACKAGE_STEM = {"openfpgaloader": "openfpgaloader-fpgasonline", "openocd": "openocd-fpgasonline"}


def package_name(tool: str, track: str) -> str:
    """openfpgaloader-fpgasonline for stable, openfpgaloader-fpgasonline-git for master."""
    if tool not in PACKAGE_STEM:
        raise FpgatoolsError(f"unknown tool '{tool}' (known: {', '.join(TOOLS)})")
    if track not in TRACKS:
        raise FpgatoolsError(f"unknown track '{track}' (known: {', '.join(TRACKS)})")
    stem = PACKAGE_STEM[tool]
    return stem if track == "stable" else f"{stem}-git"


def sibling_packages(tool: str, track: str) -> list[str]:
    """The same tool's packages on the other tracks: they all ship the same binary."""
    return [package_name(tool, t) for t in TRACKS if t != track]


@dataclass(frozen=True)
class Substitutions:
    package: str
    version: str
    track: str
    conflicts: str
    upstream_url: str
    upstream_ref: str
    upstream_commit: str
    tool_version: str

    def as_dict(self) -> dict[str, str]:
        return {
            "@PACKAGE@": self.package,
            "@VERSION@": self.version,
            "@TRACK@": self.track,
            "@CONFLICTS@": self.conflicts,
            "@UPSTREAM_URL@": self.upstream_url,
            "@UPSTREAM_REF@": self.upstream_ref,
            "@UPSTREAM_COMMIT@": self.upstream_commit,
            "@TOOL_VERSION@": self.tool_version,
        }


def substitutions(tool: str, track: str, pins: Pins, repo_ver: str) -> Substitutions:
    pin = pins.pin(tool, track)
    return Substitutions(
        package=package_name(tool, track),
        version=package_version(tool, track, pins, repo_ver),
        track=track,
        conflicts=", ".join(sibling_packages(tool, track)),
        upstream_url=pins.url(tool),
        upstream_ref=pin.ref,
        upstream_commit=pin.commit[:12],
        tool_version=tool_version_string(tool, track, pins, repo_ver),
    )


def substitute(text: str, subs: Substitutions) -> str:
    for token, value in subs.as_dict().items():
        text = text.replace(token, value)
    if "@" in text and any(f"@{w}@" in text for w in _known_tokens()):
        raise FpgatoolsError("template still contains an unsubstituted token")
    return text


def _known_tokens() -> list[str]:
    return [k.strip("@") for k in Substitutions.__dataclass_fields__]


def changelog(subs: Substitutions, date_rfc2822: str) -> str:
    return (
        f"{subs.package} ({subs.version}) unstable; urgency=medium\n"
        "\n"
        f"  * Rolling fpgas.online build ({subs.track} track) of {subs.upstream_ref}"
        f" {subs.upstream_commit} plus the fpgas.online patch series.\n"
        "\n"
        f" -- {MAINTAINER}  {date_rfc2822}\n"
    )


def head_date_rfc2822(repo: Path = REPO) -> str:
    """The HEAD commit date of this repository, so a rebuild is byte-identical."""
    return run_git(["log", "-1", "--format=%cd", "--date=rfc2822"], cwd=repo).stdout.strip()


def render(
    tool: str,
    track: str,
    pins: Pins,
    repo_ver: str,
    dest: Path,
    *,
    templates_root: Path = TEMPLATES_ROOT,
    date_rfc2822: str | None = None,
) -> Path:
    """Write <dest>/debian/ from the templates; returns the debian/ directory."""
    src = templates_root / tool
    if not src.is_dir():
        raise FpgatoolsError(f"no debian templates for '{tool}' under {templates_root}")
    subs = substitutions(tool, track, pins, repo_ver)
    debian = dest / "debian"
    debian.mkdir(parents=True, exist_ok=True)
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = path.relative_to(src)
        out_name = rel.name[: -len(".in")] if rel.name.endswith(".in") else rel.name
        out = debian / rel.parent / out_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(substitute(path.read_text(), subs))
        if out_name == "rules":
            out.chmod(0o755)
    (debian / "changelog").write_text(changelog(subs, date_rfc2822 or head_date_rfc2822()))
    return debian


def _cmd(args: argparse.Namespace) -> int:
    from fpgatools.patchset import src_dir

    pins = load()
    dest = Path(args.dest) if args.dest else src_dir(args.tool, args.track)
    if not dest.is_dir():
        raise FpgatoolsError(
            f"{dest} does not exist: run `fpgatools apply {args.tool} {args.track}`"
        )
    debian = render(args.tool, args.track, pins, repo_version(), dest)
    subs = substitutions(args.tool, args.track, pins, repo_version())
    print(f"{debian}: {subs.package} {subs.version}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "debianize",
        help="render packaging/debian/<tool>/ into a patched tree as debian/",
    )
    p.add_argument("tool", choices=TOOLS)
    p.add_argument("track", choices=TRACKS)
    p.add_argument("--dest", help="tree to write debian/ into (default build/src/<tool>-<track>)")
    p.set_defaults(func=_cmd)
