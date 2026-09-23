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
- `packaging/` — debian templates, Alpine static scripts, librp1jtag build helper.
- `.github/workflows/` — `ci.yml`, `debs.yml`, `static.yml`, `daily.yml` (the daily bump, full rebuild and publish).
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
- Versions are derived, never typed: `<upstream>+fpgasonline.<repo version>`
  where the repo version comes from `git describe` against `vX.Y` series tags.
- Debs are built with `dh` from `packaging/debian/<tool>/`, never with
  hand-rolled `dpkg-deb` control files.
- Publishing (Pages apt, Releases) runs only from `main`; pull requests build
  everything but publish nothing.
- Small commits, each individually meaningful; PRs are merged with merge commits.
