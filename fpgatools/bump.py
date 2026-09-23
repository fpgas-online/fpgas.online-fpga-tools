"""Move the upstream pins forward and report whether the series still apply.

`fpgatools bump` is what the daily.yml workflow runs. It looks at
each upstream with a metadata-only clone (commits and trees, no blobs), moves
every [name.master] entry to the default branch head, moves [name.stable] to
the newest release tag, moves the untracked pins (librp1jtag, piolib) to their
branch heads, rewrites upstreams.toml in place and then tries to apply every
patch series so the run can say which ones need a rebase.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fpgatools import REPO, TOOLS, TRACKS
from fpgatools.cli import FpgatoolsError
from fpgatools.gitutil import run_git
from fpgatools.pins import Pin, Pins, load
from fpgatools.version import parse_describe

PINS_PATH = REPO / "upstreams.toml"
META_ROOT = REPO / "build" / "meta"

# A release tag: v, digits and dots only. Excludes v2.0.0-rc1, nightly, ...
RELEASE_TAG_RE = re.compile(r"^v\d+(\.\d+)+$")


def newest_release_tag(tags: list[str]) -> str | None:
    """The highest vX.Y[.Z] tag by numeric version order, or None."""
    releases = [t for t in tags if RELEASE_TAG_RE.match(t)]
    if not releases:
        return None
    return max(releases, key=lambda t: tuple(int(p) for p in t[1:].split(".")))


@dataclass
class PinUpdate:
    name: str
    track: str | None
    old: Pin
    new: Pin

    @property
    def changed(self) -> bool:
        return self.old != self.new

    def describe_change(self) -> str:
        where = f"{self.name}.{self.track}" if self.track else self.name
        if not self.changed:
            return f"{where}: unchanged ({self.old.ref} {self.old.commit[:7]})"
        parts = []
        if self.old.ref != self.new.ref:
            parts.append(f"{self.old.ref} -> {self.new.ref}")
        if self.old.commit != self.new.commit:
            parts.append(f"{self.old.commit[:7]} -> {self.new.commit[:7]}")
        if self.new.describe and self.old.describe != self.new.describe:
            parts.append(f"({self.new.describe}, {self.new.date})")
        return f"{where}: " + " ".join(parts)


@dataclass
class BumpReport:
    updates: list[PinUpdate] = field(default_factory=list)
    applied: dict[tuple[str, str], str] = field(default_factory=dict)  # "OK" or "CONFLICT: x"

    @property
    def changed(self) -> bool:
        return any(u.changed for u in self.updates)

    def conflicts(self) -> list[str]:
        """"<tool>/<track>: <status>" for each series that no longer applies."""
        return [f"{tool}/{track}: {status}"
                for (tool, track), status in sorted(self.applied.items())
                if status != "OK"]

    def markdown(self) -> str:
        lines = ["## Upstream pins", ""]
        lines += [f"- {u.describe_change()}" for u in self.updates]
        if self.applied:
            lines += ["", "## Patch series against the new pins", ""]
            for (tool, track), status in sorted(self.applied.items()):
                mark = "✅" if status == "OK" else "❌"
                lines.append(f"- {mark} `{tool}/{track}`: {status}")
        lines += [
            "",
            "A ❌ above means that series needs rebasing, and the pins stay where",
            "they are until it does: `fpgatools apply <tool> <track>` reproduces",
            "the conflict locally, and CLAUDE.md says how to re-export the series.",
        ]
        return "\n".join(lines) + "\n"


# --- upstream metadata ---------------------------------------------------------


def meta_repo(name: str, url: str, root: Path = META_ROOT) -> Path:
    """A blobless bare clone of `url`, fetched fresh."""
    path = root / f"{name}.git"
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "-q", "--bare", "--filter=blob:none", url, str(path)], cwd=root)
    else:
        run_git(["fetch", "-q", "--prune", "--tags", "--force", "origin",
                 "+refs/heads/*:refs/heads/*"], cwd=path)
    return path


def branch_head(meta: Path, ref: str) -> str:
    return run_git(["rev-parse", f"refs/heads/{ref}^{{commit}}"], cwd=meta).stdout.strip()


def describe(meta: Path, commit: str) -> str:
    """`git describe` against release tags only, validated.

    OpenOCD tags release candidates (v0.12.0-rc1) on master; `--match v[0-9]*`
    alone would pick one and the resulting `v0.13.0-rc1-45-gabc` is not a
    version fpgatools can turn into a package version. Excluding them here
    means a bad describe fails at bump time with a clear message instead of
    breaking every build on the bump PR.
    """
    out = run_git(["describe", "--tags", "--match", "v[0-9]*", "--exclude", "*-rc*",
                   "--exclude", "*rc[0-9]*", commit], cwd=meta).stdout.strip()
    try:
        parse_describe(out)
    except FpgatoolsError as exc:
        raise FpgatoolsError(f"{meta.name}: unusable describe for {commit[:7]}: {exc}") from exc
    return out


def commit_date(meta: Path, commit: str) -> str:
    return run_git(["log", "-1", "--format=%cd", "--date=short", commit], cwd=meta).stdout.strip()


def tags(meta: Path) -> list[str]:
    out = run_git(["tag", "-l"], cwd=meta).stdout.split()
    return out


def tag_commit(meta: Path, tag: str) -> str:
    return run_git(["rev-parse", f"{tag}^{{commit}}"], cwd=meta).stdout.strip()


def new_pins(pins: Pins, root: Path = META_ROOT) -> list[PinUpdate]:
    updates: list[PinUpdate] = []
    for name in pins.names:
        meta = meta_repo(name, pins.url(name), root)
        if pins.tracked(name):
            old = pins.pin(name, "master")
            head = branch_head(meta, old.ref)
            updates.append(PinUpdate(name, "master", old, Pin(
                ref=old.ref, commit=head, describe=describe(meta, head),
                date=commit_date(meta, head), subdir=old.subdir)))
            old = pins.pin(name, "stable")
            tag = newest_release_tag(tags(meta)) or old.ref
            updates.append(PinUpdate(name, "stable", old, Pin(
                ref=tag, commit=tag_commit(meta, tag), subdir=old.subdir)))
        else:
            old = pins.pin(name)
            head = branch_head(meta, old.ref)
            updates.append(PinUpdate(name, None, old, Pin(
                ref=old.ref, commit=head, date=commit_date(meta, head), subdir=old.subdir)))
    return updates


# --- rewriting upstreams.toml --------------------------------------------------


def rewrite_pins(text: str, updates: list[PinUpdate]) -> str:
    """Rewrite ref/commit/describe/date lines inside the affected TOML tables.

    Line-oriented on purpose: tomllib has no writer and the file's comments and
    ordering are worth keeping. Only the four value lines change; a table that
    gains a `describe`/`date` it did not have is an error (the layout is fixed).
    """
    lines = text.splitlines(keepends=True)
    for u in updates:
        if not u.changed:
            continue
        header = f"[{u.name}.{u.track}]" if u.track else f"[{u.name}]"
        start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
        if start is None:
            raise FpgatoolsError(f"upstreams.toml: table {header} not found")
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        wanted = {"ref": u.new.ref, "commit": u.new.commit}
        if u.new.describe is not None:
            wanted["describe"] = u.new.describe
        if u.new.date is not None:
            wanted["date"] = u.new.date
        seen = set()
        for i in range(start + 1, end):
            m = re.match(r'^(\s*)(ref|commit|describe|date)(\s*=\s*)"[^"]*"(.*)$', lines[i])
            if m and m.group(2) in wanted:
                key = m.group(2)
                lines[i] = f'{m.group(1)}{key}{m.group(3)}"{wanted[key]}"{m.group(4)}\n'
                seen.add(key)
        missing = set(wanted) - seen
        if missing:
            raise FpgatoolsError(
                f"upstreams.toml: {header} has no {', '.join(sorted(missing))} line"
            )
    return "".join(lines)


def verify_rewrite(text: str, updates: list[PinUpdate]) -> None:
    data = tomllib.loads(text)
    for u in updates:
        table = data[u.name][u.track] if u.track else data[u.name]
        if table["commit"] != u.new.commit or table["ref"] != u.new.ref:
            raise FpgatoolsError(f"upstreams.toml rewrite did not take for {u.name}")


# --- the command --------------------------------------------------------------


def bump(pins_path: Path = PINS_PATH, *, meta_root: Path = META_ROOT,
         apply_series: bool = True, write: bool = True) -> BumpReport:
    pins = load(pins_path)
    report = BumpReport(updates=new_pins(pins, meta_root))
    if report.changed and write:
        text = rewrite_pins(pins_path.read_text(), report.updates)
        verify_rewrite(text, report.updates)
        pins_path.write_text(text)
    if apply_series and (report.changed or not write):
        from fpgatools import patchset

        new = load(pins_path) if write else pins
        for tool in TOOLS:
            for track in TRACKS:
                try:
                    patchset.apply(tool, track, pins=new)
                    report.applied[(tool, track)] = "OK"
                except patchset.PatchConflict as exc:
                    report.applied[(tool, track)] = f"CONFLICT: {exc.patch_name}"
    return report


def _cmd(args: argparse.Namespace) -> int:
    report = bump(apply_series=not args.no_apply, write=not args.dry_run)
    for u in report.updates:
        print(u.describe_change())
    for (tool, track), status in sorted(report.applied.items()):
        print(f"{tool}/{track}: {status}")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report.markdown())
    if not report.changed:
        print("no pin changed")
    if args.fail_on_conflict and report.conflicts():
        print("series that no longer apply:", file=sys.stderr)
        for line in report.conflicts():
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("bump", help="move upstream pins forward and re-apply every series")
    p.add_argument("--dry-run", action="store_true", help="report, but leave upstreams.toml alone")
    p.add_argument("--no-apply", action="store_true", help="skip re-applying the patch series")
    p.add_argument("--report", metavar="FILE", help="write a Markdown report (PR body) here")
    p.add_argument("--fail-on-conflict", action="store_true",
                   help="exit non-zero if a series no longer applies (daily.yml wants this)")
    p.set_defaults(func=_cmd)
