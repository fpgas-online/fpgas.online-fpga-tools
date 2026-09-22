"""`upstreams.toml`: every external input of the build, pinned by commit.

Two shapes of table live in the file. A *tracked* upstream (the tools) has
`url` at the top and one sub-table per track, `[name.stable]` and
`[name.master]`. An *untracked* upstream (libraries) has `url`, `ref` and
`commit` directly at the top level.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpgatools import REPO, TRACKS
from fpgatools.cli import FpgatoolsError

DEFAULT_PATH = REPO / "upstreams.toml"


@dataclass(frozen=True)
class Upstream:
    url: str


@dataclass(frozen=True)
class Pin:
    ref: str
    commit: str
    describe: str | None = None  # `git describe --tags` at `commit` (master pins)
    date: str | None = None  # committer date YYYY-MM-DD (master pins)
    subdir: str | None = None  # only this subdirectory is built


@dataclass(frozen=True)
class Pins:
    upstreams: dict[str, Upstream]
    # key: (name, track); track is None for an untracked upstream.
    entries: dict[tuple[str, str | None], Pin]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.upstreams)

    def tool(self, name: str) -> Upstream:
        try:
            return self.upstreams[name]
        except KeyError:
            raise FpgatoolsError(
                f"unknown upstream {name!r} (known: {', '.join(self.names)})"
            ) from None

    def url(self, name: str) -> str:
        return self.tool(name).url

    def tracked(self, name: str) -> bool:
        self.tool(name)
        return (name, None) not in self.entries

    def pin(self, name: str, track: str | None = None) -> Pin:
        if self.tracked(name):
            if track is None:
                raise FpgatoolsError(f"upstream {name!r} needs a track ({' or '.join(TRACKS)})")
            try:
                return self.entries[(name, track)]
            except KeyError:
                raise FpgatoolsError(
                    f"unknown track {track!r} for {name!r} (known: {', '.join(TRACKS)})"
                ) from None
        if track is not None:
            raise FpgatoolsError(f"upstream {name!r} has no tracks (asked for {track!r})")
        return self.entries[(name, None)]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pins:
        upstreams: dict[str, Upstream] = {}
        entries: dict[tuple[str, str | None], Pin] = {}
        for name, table in data.items():
            if not isinstance(table, dict) or "url" not in table:
                raise FpgatoolsError(f"upstreams: [{name}] has no url")
            upstreams[name] = Upstream(url=str(table["url"]))
            sub_tables = {k: v for k, v in table.items() if isinstance(v, dict)}
            if "commit" in table:
                if sub_tables:
                    raise FpgatoolsError(
                        f"upstreams: [{name}] mixes a top-level commit with track tables"
                    )
                entries[(name, None)] = _pin(f"[{name}]", table)
                continue
            if not sub_tables:
                raise FpgatoolsError(f"upstreams: [{name}] has neither commit nor track tables")
            for track, sub in sub_tables.items():
                if track not in TRACKS:
                    raise FpgatoolsError(
                        f"upstreams: [{name}.{track}] is not a track ({' or '.join(TRACKS)})"
                    )
                entries[(name, track)] = _pin(f"[{name}.{track}]", sub)
        return cls(upstreams=upstreams, entries=entries)


def _pin(where: str, table: dict[str, Any]) -> Pin:
    for key in ("ref", "commit"):
        if key not in table:
            raise FpgatoolsError(f"upstreams: {where} has no {key}")
    return Pin(
        ref=str(table["ref"]),
        commit=str(table["commit"]),
        describe=_opt(table, "describe"),
        date=_opt(table, "date"),
        subdir=_opt(table, "subdir"),
    )


def _opt(table: dict[str, Any], key: str) -> str | None:
    value = table.get(key)
    return None if value is None else str(value)


def load(path: Path = DEFAULT_PATH) -> Pins:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise FpgatoolsError(f"cannot read upstreams file {path}: {exc.strerror}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FpgatoolsError(f"{path}: {exc}") from None
    return Pins.from_dict(data)
