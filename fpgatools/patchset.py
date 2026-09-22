"""Patch series: fetch the pinned upstream, apply, export and compare.

The series for `<tool>/<track>` lives in `patches/<tool>/<track>/NNNN-*.patch`
as plain `git format-patch` output and is never edited by hand: `apply`
turns it into commits on top of the pin in `build/src/<tool>-<track>`,
the developer edits those commits, `export` writes the series back.
"""

from __future__ import annotations

import argparse
import re
from email.header import decode_header, make_header
from itertools import zip_longest
from pathlib import Path

from fpgatools import REPO, TRACKS
from fpgatools.cli import FpgatoolsError
from fpgatools.gitutil import git_output, run_git
from fpgatools.pins import DEFAULT_PATH, Pins, load

# Module-level so tests can point them elsewhere.
SRC_ROOT = REPO / "build" / "src"
PATCHES_ROOT = REPO / "patches"
PINS_PATH = DEFAULT_PATH

BUILD_USER_NAME = "fpgas.online build"
BUILD_USER_EMAIL = "builds@fpgas.online"

_SUBJECT_PREFIX_RE = re.compile(r"^\[PATCH[^\]]*\]\s*")


class PatchConflict(FpgatoolsError):
    """`git am` could not apply a patch; the tree is back at the pin."""

    def __init__(self, patch_name: str, tool: str, track: str, detail: str = ""):
        self.patch_name = patch_name
        self.tool = tool
        self.track = track
        self.detail = detail
        message = f"patch {patch_name} does not apply to {tool} {track}"
        if detail:
            message += f"\n{detail}"
        super().__init__(message)


def _pins(pins: Pins | None) -> Pins:
    return pins if pins is not None else load(PINS_PATH)


# --- paths ------------------------------------------------------------------------


def src_dir(name: str, track: str | None = None) -> Path:
    """`build/src/<name>[-<track>]`."""
    return SRC_ROOT / (name if track is None else f"{name}-{track}")


def series_dir(tool: str, track: str) -> Path:
    """`patches/<tool>/<track>`."""
    return PATCHES_ROOT / tool / track


def series(tool: str, track: str) -> list[Path]:
    """The `*.patch` files of a series in application order."""
    d = series_dir(tool, track)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix == ".patch")


# --- fetch --------------------------------------------------------------------------


def _has_commit(dest: Path, commit: str) -> bool:
    r = run_git(["rev-parse", "--verify", "-q", f"{commit}^{{commit}}"], cwd=dest, check=False)
    return r.returncode == 0


def _fetch_commit(dest: Path, url: str, commit: str) -> None:
    if run_git(["remote", "set-url", "origin", url], cwd=dest, check=False).returncode != 0:
        run_git(["remote", "add", "origin", url], cwd=dest)
    # GitHub (and any file:// remote) serves a reachable commit by SHA; no
    # history is needed because describe/date are recorded in upstreams.toml.
    run_git(["fetch", "-q", "--depth", "1", "origin", commit], cwd=dest)


def fetch(
    name: str, track: str | None = None, *, force: bool = False, pins: Pins | None = None
) -> Path:
    """A working tree at the pinned commit in `build/src/<name>[-<track>]`.

    A fresh directory gets `git init`, the origin remote, a depth-1 fetch of
    the pinned commit and a detached checkout of it. The build identity is
    set locally (git am needs a committer). An existing tree whose HEAD
    descends from the pin is left alone so in-progress commits survive,
    unless `force`, which detaches HEAD back at the pin (the commits stay
    in the reflog; nothing is cleaned).
    """
    pins = _pins(pins)
    pin = pins.pin(name, track)
    url = pins.url(name)
    dest = src_dir(name, track)

    if (dest / ".git").exists():
        on_pin = run_git(["merge-base", "--is-ancestor", pin.commit, "HEAD"], cwd=dest, check=False)
        if on_pin.returncode == 0 and not force:
            return dest
        if not _has_commit(dest, pin.commit):
            _fetch_commit(dest, url, pin.commit)
        run_git(["checkout", "-q", "--detach", pin.commit], cwd=dest)
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q"], cwd=dest)
    run_git(["config", "--local", "user.name", BUILD_USER_NAME], cwd=dest)
    run_git(["config", "--local", "user.email", BUILD_USER_EMAIL], cwd=dest)
    _fetch_commit(dest, url, pin.commit)
    run_git(["checkout", "-q", "--detach", "FETCH_HEAD"], cwd=dest)
    return dest


# --- apply / export ----------------------------------------------------------------------


def _failed_patch(dest: Path, patches: list[Path]) -> str:
    """Name of the patch `git am` stopped on, from its rebase-apply state."""
    try:
        state = Path(git_output(["rev-parse", "--git-path", "rebase-apply"], cwd=dest))
        if not state.is_absolute():
            state = dest / state
        index = int((state / "next").read_text().strip())
        return patches[index - 1].name
    except (OSError, ValueError, IndexError, FpgatoolsError):
        return patches[0].name if len(patches) == 1 else "<unknown>"


def apply(tool: str, track: str, *, pins: Pins | None = None) -> Path:
    """Re-check out the pin and `git am` the series onto it."""
    dest = fetch(tool, track, force=True, pins=pins)
    patches = series(tool, track)
    if not patches:
        return dest
    # -c commit.gpgsign=false: the commits become patches, a developer's
    # signing setup must not be able to stall an automated build.
    am = run_git(
        [
            "-c",
            "commit.gpgsign=false",
            "am",
            "--3way",
            "--whitespace=nowarn",
            *(str(p) for p in patches),
        ],
        cwd=dest,
        check=False,
    )
    if am.returncode != 0:
        failed = _failed_patch(dest, patches)
        detail = "\n".join(filter(None, [am.stdout.strip(), am.stderr.strip()]))
        run_git(["am", "--abort"], cwd=dest, check=False)
        raise PatchConflict(failed, tool, track, detail)
    return dest


def export(tool: str, track: str, *, pins: Pins | None = None) -> list[Path]:
    """Rewrite `patches/<tool>/<track>/` from the commits above the pin."""
    pin = _pins(pins).pin(tool, track)
    src = src_dir(tool, track)
    if not (src / ".git").exists():
        raise FpgatoolsError(f"{src} is not a git tree; run `fpgatools fetch {tool} {track}` first")
    if not _has_commit(src, pin.commit):
        raise FpgatoolsError(f"{src} does not contain the pinned commit {pin.commit}")
    if run_git(
        ["merge-base", "--is-ancestor", pin.commit, "HEAD"], cwd=src, check=False
    ).returncode:
        raise FpgatoolsError(f"HEAD of {src} does not descend from the pinned commit {pin.commit}")

    d = series_dir(tool, track)
    d.mkdir(parents=True, exist_ok=True)
    for old in series(tool, track):
        old.unlink()
    run_git(
        [
            "format-patch",
            "--zero-commit",
            "--no-signature",
            "-o",
            str(d),
            f"{pin.commit}..HEAD",
        ],
        cwd=src,
    )
    return series(tool, track)


# --- subjects / compare ------------------------------------------------------------------


def patch_subject(path: Path) -> str:
    """The patch's Subject with the `[PATCH n/m] ` prefix removed."""
    lines: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if line == "":
                break  # end of headers
            if lines:
                if line[:1] in (" ", "\t"):
                    lines.append(line.strip())
                    continue
                break
            if line.lower().startswith("subject:"):
                lines.append(line[len("subject:") :].strip())
    if not lines:
        raise FpgatoolsError(f"{path}: no Subject: header")
    subject = " ".join(lines)
    if "=?" in subject:
        subject = str(make_header(decode_header(subject)))
    return _SUBJECT_PREFIX_RE.sub("", subject, count=1).strip()


def compare(tool: str) -> list[tuple[str | None, str | None]]:
    """Subjects of the stable and master series, paired by position."""
    stable, master = ([patch_subject(p) for p in series(tool, track)] for track in TRACKS)
    return list(zip_longest(stable, master))


def compare_ok(pairs: list[tuple[str | None, str | None]]) -> bool:
    return all(a is not None and a == b for a, b in pairs)


def format_compare(pairs: list[tuple[str | None, str | None]]) -> str:
    """Two-column table, `==` between matching subjects and `!=` otherwise."""
    left = [a or "-" for a, _ in pairs]
    width = max([len(TRACKS[0]), *(len(s) for s in left)])
    rows = [f"{TRACKS[0]:<{width}}     {TRACKS[1]}"]
    for (a, b), shown in zip(pairs, left, strict=True):
        mark = "==" if a is not None and a == b else "!="
        rows.append(f"{shown:<{width}}  {mark} {b or '-'}")
    return "\n".join(rows)


# --- CLI ------------------------------------------------------------------------------------


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "fetch", help="check out a pinned upstream into build/src/<name>[-<track>]"
    )
    p.add_argument("name", help="upstream name from upstreams.toml")
    p.add_argument("track", nargs="?", help="stable or master (tools only)")
    p.add_argument(
        "--force",
        action="store_true",
        help="detach HEAD back at the pin even if the tree has commits on top",
    )
    p.set_defaults(func=_run_fetch)

    p = subparsers.add_parser("apply", help="fetch the pin and git am the patch series onto it")
    p.add_argument("tool")
    p.add_argument("track")
    p.set_defaults(func=_run_apply)

    p = subparsers.add_parser(
        "export", help="rewrite patches/<tool>/<track>/ from the commits in build/src"
    )
    p.add_argument("tool")
    p.add_argument("track")
    p.set_defaults(func=_run_export)

    p = subparsers.add_parser(
        "compare", help="show the stable and master series side by side; exit 1 on mismatch"
    )
    p.add_argument("tool")
    p.set_defaults(func=_run_compare)


def _run_fetch(args: argparse.Namespace) -> int:
    dest = fetch(args.name, args.track, force=args.force)
    print(f"{dest}: {git_output(['rev-parse', 'HEAD'], cwd=dest)}")
    return 0


def _run_apply(args: argparse.Namespace) -> int:
    dest = apply(args.tool, args.track)
    n = len(series(args.tool, args.track))
    print(f"{dest}: {n} patch{'es' if n != 1 else ''} applied")
    return 0


def _run_export(args: argparse.Namespace) -> int:
    for p in export(args.tool, args.track):
        print(p.relative_to(REPO) if p.is_relative_to(REPO) else p)
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    pairs = compare(args.tool)
    print(format_compare(pairs))
    if compare_ok(pairs):
        return 0
    print(f"{args.tool}: the stable and master series differ", flush=True)
    return 1


__all__ = [
    "PatchConflict",
    "apply",
    "compare",
    "compare_ok",
    "export",
    "fetch",
    "patch_subject",
    "series",
    "series_dir",
    "src_dir",
]
