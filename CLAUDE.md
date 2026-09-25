# CLAUDE.md

Patched openFPGALoader and OpenOCD builds for the fpgas.online Raspberry Pi
images. Read `docs/superpowers/specs/2026-09-22-fpga-tools-design.md` before
changing structure; `README.md` is the user-facing description.

## Layout

- `upstreams.toml` — every external input pinned by commit (tools, librp1jtag, piolib).
- `patches/<tool>/<track>/NNNN-*.patch` — `git format-patch` series; tools are
  `openfpgaloader` and `openocd`, tracks are `stable` (latest upstream release)
  and `master` (upstream default branch).
- `fpgatools/` — stdlib-only Python CLI: `fetch`, `apply`, `export`, `compare`,
  `version`, `debianize`, `bump`.
- `packaging/` — debian templates (the two tools, and the shared libraries
  `rp1jtag` → librp1jtag0 and `piolib` → libpio0), Alpine static scripts,
  `build-libs.sh` / `build-deb.sh`, `libpio.sh` (libpio0 comes from Raspberry
  Pi's archive on bookworm/trixie, from here elsewhere).
- `.github/workflows/` — `ci.yml`, `debs.yml`, `static.yml`, `daily.yml` (the daily bump, full rebuild and publish).
  The build matrices are `build-debs.yml` / `build-static.yml`, reusable and
  read-only; publishing lives only in `debs.yml` / `static.yml`. Never give a
  reusable workflow a job that needs more than `contents: read`: `daily.yml`
  calls them, and a callee asking for more than the caller grants fails the
  whole run at startup.
- `build/` (gitignored) — upstream working trees under `build/src/<name>[-<track>]`.

## Commands

```bash
uv run pytest -q                     # tooling tests
uv run ruff check                    # lint
uv run fpgatools apply openocd master   # fetch pinned upstream + git am the series
uv run fpgatools version openfpgaloader stable   # the Debian version string
shellcheck packaging/**/*.sh
```

## Working on the patch series

Never edit a `.patch` file by hand. The series is the export of a git branch:

1. `uv run fpgatools apply <tool> <track>` gives a tree in `build/src/<tool>-<track>`
   at the pinned upstream commit with the series applied as commits.
2. Change the commits there (`git commit --amend`, `git rebase -i`, new commits).
3. `uv run fpgatools export <tool> <track>` rewrites `patches/<tool>/<track>/`.
4. Do the same for the other track, then `uv run fpgatools compare <tool>`
   must report the two series in step (same subjects, same order).
5. Build-check both trees before committing (see README "Building locally").

Patch authorship stays with the original author; a patch you adapt keeps
their `From:` and gains nothing else. New glue patches are authored by
whoever writes them.

## Rules

- No new tool functionality: this repo extracts, rebases and packages.
  Feature work belongs in the upstream projects or their forks.
- Versions are derived, never typed, and follow `git describe` (`X.Y.postN`):
  `<upstream>+fpgasonline.<repo version>`, where `<upstream>` is the release
  tag or, on master and for librp1jtag0, `<tag>.post<N>` from the pin's
  recorded describe, and the repo version is this repo's own describe
  against `vX.Y` series tags. The one date-versioned package is our sid
  libpio0, which follows Raspberry Pi's scheme (Tim, 2026-09-24). Any other
  scheme needs Tim's approval first.
- Debs are built with `dh` from `packaging/debian/<name>/`, never with
  hand-rolled `dpkg-deb` control files.
- The Debian tools link librp1jtag shared (librp1jtag0); the static release
  binaries link it and PIOLib statically. Keep both working.
- Publishing (Pages apt, Releases) runs only from `main`; pull requests build
  everything but publish nothing.
- Small commits, each individually meaningful; PRs are merged with merge commits.
