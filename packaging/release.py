#!/usr/bin/env python3
"""Upload build assets to the series' rolling GitHub Release or to the build's own.

    uv run python packaging/release.py --assets built-static [--series vX.Y] [--dry-run]
    uv run python packaging/release.py --assets <dir> --build-release [--dry-run]

The release hangs off the nearest vX.Y series tag (the repo's tag ruleset only
admits that shape; v0.0 sits on the root commit) and is created as a
prerelease if missing, with the repository's own GITHUB_TOKEN (`gh` must be
authenticated). Asset filenames carry the full package version, so they never
collide and are uploaded once; only latest.json is replaced on every run. It
maps track -> tool -> arch -> {asset, version} so scripts can find the newest
build without parsing the release page.

--build-release uploads instead to the release of this build alone, tagged
build-<repo version> (build-0.0.post62) at HEAD and created if missing: every
Debian package of the build, debug symbols included, and the static
tarballs, so each build is complete in one place (a release holds at most
1000 assets; the rolling series release would fill up). The .debs are named
<suite>_<file>.deb because every suite's build produces the same Debian
filename. debs.yml and static.yml both upload to it, whichever finishes
first creating it, and neither touches latest.json there. The tag must not
start with v<digit>: repo_version()'s git describe would take it for a
series tag.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Both tools ship as tarballs (binary plus the data directory it needs at
# run time: bridge bitstreams for openFPGALoader, the Tcl tree for OpenOCD).
ASSET_RE = re.compile(
    r"^(?P<file>(?P<tool>openFPGALoader|openocd)-(?P<version>[^/]+?)-linux-(?P<arch>arm64|armv7|armv6|amd64)"
    r"\.tar\.gz)(?P<sha>\.sha256)?$"
)
TOOL_KEY = {"openFPGALoader": "openfpgaloader", "openocd": "openocd"}
# <suite>_<package>_<version>_<arch>.deb: a Debian filename behind its suite.
DEB_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*_[a-z0-9][a-z0-9.+-]*_[^_/]+_[a-z0-9]+\.deb$")
# The upstream part of a master-track version: `1.1.1.post173` (always with
# .postN, see fpgatools/version.py), or `1.1.1+git20260915.24e46d1` on assets
# published before versions followed git describe.
MASTER_UPSTREAM_RE = re.compile(r"(\.post\d+|\+git\d{8}\.[0-9a-f]+)$")


def track_of(version: str) -> str:
    upstream = version.rsplit("+fpgasonline.", 1)[0]
    return "master" if MASTER_UPSTREAM_RE.search(upstream) else "stable"


def classify(name: str) -> dict | None:
    """{'tool','track','version','arch','file'} for a build asset, None for others."""
    m = ASSET_RE.match(name)
    if not m or m.group("sha"):
        return None
    version = m.group("version")
    return {
        "tool": TOOL_KEY[m.group("tool")],
        "track": track_of(version),
        "version": version,
        "arch": m.group("arch"),
        "file": m.group("file"),
    }


def version_key(version: str) -> tuple:
    """Order by the fpgas.online patchset revision (the part after +fpgasonline.).

    Within one track the upstream base only ever moves forward together with
    the patchset revision, so the repo version alone orders builds correctly.
    """
    tail = version.rsplit("+fpgasonline.", 1)[-1]
    nums = [int(x) for x in re.findall(r"\d+", tail)]
    return tuple(nums)


def is_asset(name: str) -> bool:
    """A file this script uploads: a build tarball, its .sha256, or a suite-named .deb."""
    return bool(classify(name.removesuffix(".sha256")) or DEB_RE.match(name))


def latest_index(names: list[str]) -> dict:
    """track -> tool -> arch -> {"asset": ..., "version": ...} for the newest of each."""
    index: dict = {}
    for name in names:
        info = classify(name)
        if not info:
            continue
        slot = index.setdefault(info["track"], {}).setdefault(info["tool"], {})
        cur = slot.get(info["arch"])
        if cur is None or version_key(info["version"]) > version_key(cur["version"]):
            slot[info["arch"]] = {"asset": info["file"], "version": info["version"]}
    return index


def collect_assets(assets_dir: Path) -> list[Path]:
    """Build assets and their .sha256 files anywhere under assets_dir, by name.

    Each build job's artifact is downloaded into its own subdirectory, so two
    builds that claim one asset name (as the 32-bit builds once did, naming
    themselves arm64) are both still on disk; refuse to upload either.
    """
    found: dict[str, list[Path]] = {}
    for p in assets_dir.rglob("*"):
        if not p.is_file():
            continue
        if is_asset(p.name):
            found.setdefault(p.name, []).append(p)
    clashes = {n: ps for n, ps in found.items() if len(ps) > 1}
    if clashes:
        lines = [f"  {n}: {', '.join(p.parent.name for p in ps)}"
                 for n, ps in sorted(clashes.items())]
        sys.exit("asset names produced by more than one build:\n" + "\n".join(lines))
    return [found[n][0] for n in sorted(found)]


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def series_tag() -> str:
    tag = sh("git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", check=False).strip()
    if not tag:
        sys.exit("no vX.Y series tag reachable from HEAD; push one (e.g. v0.0 on the root commit)")
    return tag


SERIES_NOTES = (
    "Rolling builds of the fpgas.online openFPGALoader and OpenOCD: one set of static "
    "binaries per green commit on main, named by package version. latest.json maps "
    "track -> tool -> arch to the newest asset. Each build's complete set, Debian packages "
    "included, is its own build-<version> release.")
BUILD_NOTES = (
    "Everything build {version} of fpgas.online openFPGALoader and OpenOCD produced: the "
    "Debian packages for every suite and architecture, named <suite>_<file>.deb and debug "
    "symbols included, and the static binaries. The apt repository at "
    "https://fpgas.online/fpgas.online-fpga-tools/ is an easier way to install the same "
    "packages (it leaves out -dbgsym packages over 10 MB).")


def build_tag() -> str:
    from fpgatools.version import repo_version
    return f"build-{repo_version()}"


def ensure_release(tag: str, title: str, notes: str, dry_run: bool, target: str = "") -> None:
    view = ["gh", "release", "view", tag]
    if subprocess.run(view, capture_output=True).returncode == 0:
        return
    print(f"creating prerelease {tag}")
    if dry_run:
        return
    create = ["gh", "release", "create", tag, "--prerelease", "--title", title, "--notes", notes]
    if target:
        create += ["--target", target]
    r = subprocess.run(create, capture_output=True, text=True)
    # debs.yml and static.yml both create a build's release; one of them loses.
    if r.returncode != 0 and subprocess.run(view, capture_output=True).returncode != 0:
        sys.exit(f"{' '.join(create)}\n{r.stderr.strip()}")


def existing_assets(tag: str) -> list[str]:
    out = sh("gh", "release", "view", tag, "--json", "assets", "--jq", ".assets[].name",
             check=False)
    return out.split()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--assets", required=True, help="directory of built assets to upload")
    ap.add_argument("--series", help="series tag (default: nearest vX.Y tag)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--build-release", action="store_true",
                    help="upload to this build's own build-<version> release; no latest.json")
    args = ap.parse_args()

    assets_dir = Path(args.assets)
    local = collect_assets(assets_dir)
    if not local:
        sys.exit(f"no build assets under {assets_dir}")
    if args.build_release:
        tag = build_tag()
        version = tag.removeprefix("build-")
        ensure_release(tag, f"Build {version}", BUILD_NOTES.format(version=version),
                       args.dry_run, target=sh("git", "rev-parse", "HEAD").strip())
    else:
        tag = args.series or series_tag()
        ensure_release(tag, f"Static binaries and packages (rolling, series {tag})",
                       SERIES_NOTES, args.dry_run)
    have = set(existing_assets(tag)) if not args.dry_run else set()

    to_upload = [p for p in local if p.name not in have]
    for p in to_upload:
        print(f"upload {p.name}")
    if to_upload and not args.dry_run:
        sh("gh", "release", "upload", tag, *[str(p) for p in to_upload])
    skipped = [p.name for p in local if p.name in have]
    if skipped:
        print(f"already present, skipped: {len(skipped)}")

    if args.build_release:
        return 0
    names = sorted(have | {p.name for p in local})
    index = {"series": tag, "latest": latest_index(names)}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "latest.json"
        path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
        print(path.read_text())
        if not args.dry_run:
            sh("gh", "release", "upload", tag, str(path), "--clobber")
    return 0


if __name__ == "__main__":
    sys.exit(main())
