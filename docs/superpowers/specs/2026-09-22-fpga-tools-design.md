# fpgas.online-fpga-tools: patched openFPGALoader and OpenOCD builds

Date: 2026-09-22. Status: design, awaiting Tim's review (written autonomously
from the request below; every decision marked **[decision]** is one Tim may
want to override).

## 1. Request

> Create a new repo under the fpgas-online organization that builds a custom
> version of openfpgaloader and openocd for usage in the fpgas.online images.
> This repo should maintain a patchset against the latest stable and latest
> git upstream for the various extra functionality we want that has not yet
> been upstreamed. Initially this should include:
>  * Using the RP1 for bitbanging JTAG
>  * NeTV2 config files (for both bit banging on old RPi hardware and new
>    style RP1 bit banging)
>  * Tiny Tapeout FPGA emulation board support
>  * SPI flash info and ID number support
>  * Simple traceid and devicedna acquisition
>
> Most of the functionality should already be found in mithro/rp1-jtag and
> mithro/openFPGALoader. Do not develop any new functionality for the tooling,
> just extract the stuff into patch series from these repositories (and
> rebase/update them if needed).
>
> The resulting custom patched binaries should end up as both debs in
> published apt repos and static binaries available on GitHub releases.
> Everything should be properly versioned so that the base / source version
> is clear and that they are modified / custom versions is also clear.

## 2. What exists today (surveyed 2026-09-22)

| Where | What | State |
|---|---|---|
| `mithro/rp1-jtag` `drivers/openfpgaloader/` | `rp1PioJtag.{cpp,hpp}` cable driver + `integrate.py` that text-patches `cable.hpp`, `jtag.cpp`, `CMakeLists.txt` | The copy rp1-jtag builds into the `openfpgaloader-rp1pio` debs the fleet runs (152 lines, NeTV2 default pins, no TDI buffering). The fork branch `feature/rp1-jtag-netv2` carries a different variant (313 lines, TDI word buffering); which is better was not settled here, the fleet-tested one is used |
| `mithro/rp1-jtag` `drivers/openocd/` | `rp1_pio_jtag.c` adapter driver (GPL-2.0-or-later) + `integrate.py` for `configure.ac`, `Makefile.am`, `interface.h`, `interfaces.c`; `netv2_35t.cfg`, `netv2_35t_spi.cfg` | Working; shipped as `openocd-rp1pio` |
| `mithro/rp1-jtag` `.github/workflows/deb.yml` | Builds `openfpgaloader-rp1pio` and `openocd-rp1pio` debs from upstream **HEAD** (`git clone --depth 1`), hand-rolled `dpkg-deb`, publishes per-suite apt to `mith.ro/rp1-jtag` | Green; no stable track, no pin, version `0.0.postN` says nothing about upstream |
| `mithro/rp1-jtag` `static-openfpgaloader.yml` | Alpine static openFPGALoader arm64/armv7/armv6 | Artifacts only, never released |
| `mithro/openFPGALoader` `flash-info` | 14 commits: `--flash-info`, `--flash-info-json`, SFDP parser, unique-ID reads | Rebased onto upstream master `24e46d1` (2026-09-15), 0 behind |
| `mithro/openFPGALoader` `tt-fpga-support` | 7 commits: `tt_fpga` board / `tt_micropython` cable, iCE40UP5K via RP2040/RP2350 MicroPython raw REPL, macOS/Windows | 164 commits behind upstream; upstream since removed `USE_DEVICE_ARG` |
| `mithro/openFPGALoader` `feature/rp1-jtag-netv2` | rp1pio driver (older copy) + `netv2`/`netv2_100` boards + GPIO cable autodetect (rp1pio if `/dev/pio0`, else libgpiod) | 168 behind |
| `mithro/openFPGALoader` `feature/netv2` | The libgpiod-only variant of the NeTV2 boards | Upstream PR trabucayre/openFPGALoader#643, open since 2026-04 |
| `mithro/rpi-hwid` `src/rpi_hwid/fpga.py` (local checkout) | Xilinx 7-series Device DNA over OpenOCD (`irscan 0x32`, 64-bit `drscan`, bit-reverse, keep 57 bits); ECP5 TraceID through Apollo (`UIDCODE_PUB` 0x19 into 8-bit IR, 64-bit DR, keep low 56 bits) | Inline command lists, not config files. The TraceID sequence is cross-checked against ecpdap `read_uid()` and Lattice TN1260, which the patch cites |
| `fpgas-online/fpgas.online-test-designs` `designs/pcie-enumeration/openocd/alphamax-rpi.cfg` | NeTV2 via `linuxgpiod` (Pi 5) or `bcm2835gpio` (Pi 1-4) | Pins 4/17/27/22, SRST 24 |
| `fpgas-online/fpgas.online-infra` PR #48 (merged 2026-09-14) | NFS root installs `openfpgaloader-rp1pio` + `openocd-rp1pio` from `mithro.github.io/rp1-jtag/<suite>/` | Live on the fleet |
| `fpgas-online/apt` | Single shared `pool/` indexed into `bookworm` and `trixie` | Suitable for `Architecture: all` only; a compiled deb built against bookworm libs would be offered to trixie too |

Upstream state: openFPGALoader latest stable `v1.1.1` (2026-03-11), master
`24e46d1` (173 commits past the tag). OpenOCD latest stable `v0.12.0`
(2023-01), master `b04ccfe` (2026-09-20). Debian ships openfpgaloader
0.10.0 (bookworm) / 0.13.1 (trixie) and openocd 0.12.0 everywhere.

## 3. Goals and non-goals

Goals:

1. One repo, `fpgas-online/fpgas.online-fpga-tools`, holding **patch series**
   (plain `git format-patch` output) for openFPGALoader and OpenOCD, one series
   per tool per track, tracks being `stable` (latest upstream release tag) and
   `master` (upstream default branch, pinned to a commit).
2. Reproducible builds: every upstream input is pinned by commit in one file.
3. Debs for Debian bookworm, trixie and sid on arm64 and armhf, published as
   signed per-suite flat apt repositories on this repo's GitHub Pages.
4. Static (musl) binaries for arm64, armv7 and armv6 uploaded to GitHub
   Releases, with the same version string as the debs.
5. Versions that state both the upstream base and the fpgas.online patch level.
6. Automation that notices upstream movement and opens a PR with the pin bump
   and the result of re-applying the series.

Non-goals:

- New tool functionality. Only extraction, rebasing and packaging glue.
- Changing what the fleet installs. Switching `fpgas.online-infra` from the
  rp1-jtag packages to these is a separate PR for Tim to decide (see section 11).
- Retiring the tool builds in `mithro/rp1-jtag`. Also a follow-up.
- Publishing into `fpgas-online/apt` (shared-pool model cannot hold suite-specific
  compiled packages; see section 7).

## 4. Repository layout

```
fpgas.online-fpga-tools/
  README.md                     what this is, patch table, install, versioning
  LICENSE                       Apache-2.0 (the tooling; patches keep upstream licences)
  CLAUDE.md                     working rules for agents
  upstreams.toml                every pinned input (section 6)
  patches/
    openfpgaloader/stable/NNNN-*.patch
    openfpgaloader/master/NNNN-*.patch
    openocd/stable/NNNN-*.patch
    openocd/master/NNNN-*.patch
  packaging/
    debian/openfpgaloader/      control.in, rules, copyright, source/format, *.install
    debian/openocd/             same
    static/                     Alpine build scripts (one per tool)
    apt-index.html              landing page picked up by mithro/apt-repo-action
  fpgatools/                    Python package (stdlib only), `uv run fpgatools ...`
    __main__.py, cli.py, pins.py, patchset.py, version.py, debianize.py, bump.py
  tests/                        pytest for the pure functions (version strings, series checks)
  .github/workflows/
    ci.yml                      lint + tests + apply-all-series + native amd64 compile check
    debs.yml                    matrix build, publish to Pages (main only)
    static.yml                  matrix static build, upload to the series release (main only)
    update-upstream.yml         weekly pin bump PR
  docs/superpowers/specs/       this document
  build/                        (gitignored) upstream working trees
```

**[decision] Patches, not a fork branch, not `integrate.py`.** The request
asks for a patchset; `git format-patch` files are reviewable in the repo,
diffable between tracks with `git range-diff`, and apply with `git am`. The
`integrate.py` anchor-string approach in rp1-jtag breaks silently when an
anchor moves and cannot express a tracked upstream base.

**[decision] Two full series, one per track.** Simpler than a shared series
with per-track overrides. `fpgatools compare <tool>` lists the two series
side by side and flags patches present in only one, so divergence is
visible; CI runs it and fails on a mismatch unless the patch name carries a
`.track-only` marker in its subject line (not expected initially).

## 5. Patch series contents

### openFPGALoader (both tracks; order = least likely to conflict first)

| # | Patch | Source | Licence |
|---|---|---|---|
| 1-14 | The `flash-info` series (14 commits, subjects kept verbatim) | `mithro/openFPGALoader@flash-info` | Apache-2.0 |
| 15 | `Add netv2 and netv2_100 board definitions with auto-detected cable` | `feature/rp1-jtag-netv2` `55badb9`: rp1pio if `/dev/pio0`, else libgpiod | Apache-2.0 |
| 16 | `Add rp1pio cable driver for RP1 PIO JTAG on RPi 5` | `rp1-jtag/drivers/openfpgaloader/` (the fleet-deployed copy, byte-identical) + the CMake/cable/jtag glue `integrate.py` applies, via `pkg_check_modules(rp1jtag)` | Apache-2.0 |
| 17-23 | Tiny Tapeout FPGA Demo Board series: core (`ttMicropython.{cpp,hpp}`), board + cable defs, `COMM_TT_MICROPYTHON` dispatch, `ENABLE_TT_MICROPYTHON` cmake option, RP2350 hi-Z after programming, code-review fixes, macOS/Windows serial | `tt-fpga-support`, all seven commits kept (the fix-up commit touches two earlier commits, so folding it would have meant rewriting history the fork never had) | Apache-2.0 |
| 24 | `cmake: allow the reported version string to be overridden` | new, 4 lines: `OPENFPGALOADER_VERSION` cache var, default `v${PROJECT_VERSION}` | Apache-2.0 |

Patch 15 depends on 16 only at runtime (`#ifdef ENABLE_RP1_PIO`), so either
order compiles. Patches 17-23 were rebased over the upstream removal of
`USE_DEVICE_ARG` (upstream made `--device` unconditional); v1.1.1 still has
the gate, so the stable copy of patch 20 extends it with
`ENABLE_TT_MICROPYTHON` as the fork did. On v1.1.1 the flash-info series
is mapped back onto the pre-rename `SPIInterface` / `spiInterface.*` names
(upstream renamed them to `FlashInterface` after the release) and drops
master-only context (`--force-terminal-mode`).

`--read-dna` / `--read-xadc` / `--read-register` are upstream since v1.0 and
need no patch. The Device DNA half of "traceid and devicedna" is therefore
covered by openFPGALoader `--read-dna` on Xilinx and by the OpenOCD proc below
where openFPGALoader cannot reach the chain.

### OpenOCD (both tracks)

| # | Patch | Source | Licence |
|---|---|---|---|
| 1 | `guess-rev.sh: honour OPENOCD_LOCAL_VERSION` | new, 4 lines: if set, print it in place of the git-derived suffix | GPL-2.0-or-later |
| 2 | `jtag/drivers: add rp1_pio_jtag adapter (Raspberry Pi 5 RP1 PIO)` | `rp1-jtag/drivers/openocd/rp1_pio_jtag.c` + exactly the `configure.ac`, `src/jtag/drivers/Makefile.am`, `interface.h`, `interfaces.c` edits `integrate.py` makes, + `tcl/interface/raspberrypi-rp1-pio.cfg`, + a `doc/openocd.texi` adapter entry | GPL-2.0-or-later |
| 3 | `tcl/board: Kosagi NeTV2 driven from a Raspberry Pi 40-pin header` | `netv2-rpi-rp1pio.cfg` (Pi 5, from rp1-jtag `netv2_35t.cfg` generalised to any 7-series), `netv2-rpi-bcm2835gpio.cfg` (Pi 1-4 direct GPIO, Alphamax wiring; on master built on `interface/raspberrypi-native.cfg`'s device-tree SoC detection and CPU-clock delay calibration, on 0.12.0 the same detection inline because that release's file is fixed to a Pi 1), `netv2-rpi-linuxgpiod.cfg` (any Pi; `NETV2_GPIOCHIP` variable, default 0), `netv2-rpi.cfg` (picks rp1pio when `/dev/pio0` exists, linuxgpiod on any other Pi 5, else bcm2835gpio), `netv2-rpi-spiflash.cfg` (jtagspi proxy, from rp1-jtag `netv2_35t_spi.cfg`). The 7-series config is `fpga/xlnx/xc7.cfg` on master and `cpld/xilinx-xc7.cfg` on 0.12.0 | GPL-2.0-or-later |
| 4 | `tcl/fpga: read a Lattice ECP5 TraceID` | `proc ecp5_read_traceid {tap}` (UIDCODE_PUB 0x19 / 8-bit IR / 64-bit DR / low 56 bits), refusing all-zero or all-one shifts. **No Device DNA patch is needed**: upstream OpenOCD has shipped `fpga/xilinx-dna.cfg` (`xc7_get_dna`, `xilinx_print_dna`) since before 0.12.0, so the survey's plan to transcribe rpi-hwid's FUSE_DNA sequence was dropped in favour of the upstream file | GPL-2.0-or-later |

Pin order reminder written into every NeTV2 file: OpenOCD `jtag_nums` is
TCK TMS TDI TDO (`4 17 27 22`); openFPGALoader `--pins` is TDI:TDO:TCK:TMS
(`27:22:4:17`).

## 6. Pinning: `upstreams.toml`

```toml
[openfpgaloader]
url = "https://github.com/trabucayre/openFPGALoader.git"
[openfpgaloader.stable]
ref = "v1.1.1"
commit = "85be4fa..."
[openfpgaloader.master]
ref = "master"
commit = "24e46d13bb8f2bc9371e9ca8443ece2fafc4b20d"
describe = "v1.1.1-173-g24e46d1"     # git describe --tags at that commit
date = "2026-09-15"                    # committer date, for +gitYYYYMMDD

[openocd]
url = "https://github.com/openocd-org/openocd.git"
[openocd.stable]  ref = "v0.12.0"  commit = "..."
[openocd.master]  ref = "master"   commit = "b04ccfe..."  describe = "v0.12.0-NNNN-gb04ccfe"  date = "2026-09-20"

[rp1jtag]        # librp1jtag, linked statically into both tools
url = "https://github.com/mithro/rp1-jtag.git"
commit = "d9d7d8d..."                 # origin/main

[piolib]         # raspberrypi/utils piolib, the /dev/pio0 backend of librp1jtag
url = "https://github.com/raspberrypi/utils.git"
commit = "..."
```

`fpgatools fetch <name> [<track>]` does `git init; git fetch --depth 1 <url>
<commit>; git checkout FETCH_HEAD` into `build/src/<name>[-<track>]` (GitHub
serves any reachable commit by SHA). No history is needed at build time
because `describe` and `date` are recorded in the pin file by the bump tool.

**[decision] librp1jtag and piolib are built from pinned source and linked
statically** into openFPGALoader and OpenOCD in every build form. The debs
then depend only on Debian libraries and the fleet needs one apt source,
not two. rp1-jtag's own `librp1jtag0` packages stay unaffected.

## 7. Versioning

Let `R` be this repo's own git-describe version in the fpgas-online
convention (`vX.Y` series tag on a commit → `X.Y`, N commits later →
`X.Y.postN`; `v0.0` on the root commit), i.e. a port of
`packaging/deb-version.py`.

| Track | Debian version | Example |
|---|---|---|
| stable | `<upstream tag sans v>+fpgasonline.<R>` | `1.1.1+fpgasonline.0.0.post12` |
| master | `<last tag sans v>+git<YYYYMMDD>.<sha7>+fpgasonline.<R>` | `1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12` |

Rules: Debian's `+git<date>.<sha>` snapshot convention marks a git build;
`+fpgasonline.` marks it as ours and carries the patchset revision; all
characters are legal in a Debian upstream version and there is no `-`, so
source format `3.0 (native)` applies. Both tracks strictly out-version the
Debian archive (`1.1.1+... > 0.13.1-1`; `0.12.0+git... > 0.12.0-4`) so a
plain `apt install` from a host with both sources prefers ours.

The binaries report the same string: openFPGALoader prints
`openFPGALoader v1.1.1+fpgasonline.0.0.post12` (via patch 1),
OpenOCD prints `Open On-Chip Debugger 0.12.0+dev-01234-gb04ccfe+fpgasonline.0.0.post12`
(via patch 1; for stable `0.12.0+fpgasonline.0.0.post12`).

**[decision] Binary package names.** `openfpgaloader-fpgasonline` and
`openocd-fpgasonline` for the stable track, `openfpgaloader-fpgasonline-git`
and `openocd-fpgasonline-git` for master. Each declares
`Provides/Conflicts/Replaces` on the Debian name (`openfpgaloader`, `openocd`),
on the rp1-jtag name (`openfpgaloader-rp1pio`, `openocd-rp1pio`) and on its
sibling track, so any of them can be swapped in with one `apt install`.
Distinct names make "this is the modified build" unmistakable in `dpkg -l`
and let the fleet choose a track explicitly.

## 8. Building

### Debs (`debs.yml`)

Matrix: tool {openfpgaloader, openocd} × track {stable, master} × suite
{bookworm, trixie, sid} × arch {arm64, armhf} = 24 jobs on `ubuntu-24.04-arm`,
each `docker run --platform` in `debian:<suite>` (armhf via the runner's
binfmt, as rp1-jtag does). Steps inside the container:

1. `fpgatools fetch` piolib, rp1jtag, the tool; `fpgatools apply <tool> <track>`
   (`git am --3way`; conflict = job failure).
2. Build piolib (library target only) and librp1jtag (static) into `/usr/local`.
3. `fpgatools debianize <tool> <track>` renders `packaging/debian/<tool>/` into
   the source tree with package name, version and changelog.
4. `dpkg-buildpackage -us -uc -b` with debhelper 13: `dh` drives cmake or
   autotools, computes `${shlibs:Depends}`, strips, ships udev rules and, for
   OpenOCD, `/usr/share/openocd/scripts`.
5. Assert the result: `openFPGALoader --list-cables` names `rp1pio`,
   `libgpiod`, `tt_micropython`; `openocd -c 'adapter driver rp1_pio_jtag' -c shutdown`
   does not say "invalid"; `--Version` / `-v` contains `fpgasonline`.

Configure flags: openFPGALoader `ENABLE_CABLE_ALL=ON ENABLE_VENDORS_ALL=ON
ENABLE_LIBGPIOD=ON ENABLE_UDEV=ON ENABLE_RP1_PIO=ON ENABLE_TT_MICROPYTHON=ON`;
OpenOCD `--enable-rp1-pio-jtag --enable-bcm2835gpio --enable-linuxgpiod
--enable-sysfsgpio --disable-werror` plus the default USB adapters.

Artifacts `debs-<suite>-<arch>-<tool>-<track>`; the publish job (main only)
calls `mithro/apt-repo-action/.github/workflows/publish-apt.yml@main` with
`suites: "bookworm trixie sid"`, `architectures: "arm64 armhf"`,
`keyring-name: fpgas-online-fpga-tools.gpg`, secret `APT_GPG_PRIVATE_KEY`.
Result: `https://fpgas-online.github.io/fpgas.online-fpga-tools/<suite>/ ./`.

**[decision] Own Pages apt repo, not `fpgas-online/apt`.** See section 2:
the shared pool cannot distinguish a bookworm build from a trixie build of the
same package. This matches rp1-jtag and Tim's 2026-09-01 apt standardisation
programme (per-suite, per-repo signed Pages via `mithro/apt-repo-action`).

### Static binaries (`static.yml`)

Matrix: tool × track × arch {arm64 (native `alpine`), armv7
(`arm32v7/alpine`), armv6 (`arm32v6/alpine`)} = 12 jobs, on `ubuntu-24.04-arm`
with QEMU for arm32. Same fetch/apply as above, then the rp1-jtag recipe:
static libftdi1, libusb, eudev, hidapi, zlib; libgpiod built from source
(v2.x on arm64, v1.6.x on arm32 for old-kernel chardev ABI); piolib and
librp1jtag static; for OpenOCD additionally the bundled jimtcl submodule
(static by construction) and no capstone. Link `-static`, verify with
`file`, run `--Version`.

Assets, uploaded (main only, after every matrix job succeeded) to the
current series release (`vX.Y` prerelease, created if missing, the
fpgas.online-tt pattern):

- `openFPGALoader-<version>-linux-<arch>` (single static executable) + `.sha256`
- `openocd-<version>-linux-<arch>.tar.gz` containing `bin/openocd` and
  `share/openocd/scripts/` (OpenOCD is useless without its Tcl tree; run with
  `OPENOCD_SCRIPTS=<dir>/share/openocd/scripts`) + `.sha256`
- `latest.json` (clobbered each publish): `{track: {tool: {arch: asset}}}`
  plus the version strings, for scripts that want "the newest".

Versioned filenames never collide, so assets accumulate and nothing is
overwritten except `latest.json`.

### CI (`ci.yml`, push + PR)

`uv run ruff check`, `uv run pytest`, shellcheck, `fpgatools apply` for all
four series (fails on conflict), `fpgatools compare` for both tools, and a
native amd64 **deb build** of each tool/track in `debian:trixie` through the
same `packaging/build-deb.sh` the arm matrix uses (RP1 PIO on: librp1jtag
compiles anywhere, it just has nothing to drive on amd64). PRs also run
`debs.yml` and `static.yml` build jobs (publish jobs are main-only).
`packaging/compile-check.sh` is the developer-side equivalent (configure,
build, assert cables/adapters/version) without packaging.

### Upstream tracking (`update-upstream.yml`, weekly + dispatch)

`fpgatools bump` does `git clone --bare --filter=blob:none` of each upstream,
finds the default-branch head and the newest non-rc `v*` tag, rewrites
`upstreams.toml` (`commit`, `describe`, `date`, stable `ref`/`commit`), runs
`fpgatools apply` for every series and records per-series OK / CONFLICT
(with the failing patch name). If anything changed it opens a PR with that
report in the body. Limitation stated in the workflow: a PR opened with
`GITHUB_TOKEN` does not trigger `pull_request` workflows, so the apply report
in the body is the first signal and CI runs once a human pushes to or
re-opens the PR. A conflicting bump is still a PR so the drift is visible.

## 9. Tooling (`fpgatools`)

Python 3.11+, stdlib only (`tomllib`, `subprocess`, `argparse`), run with
`uv run fpgatools`. Subcommands:

| Command | Does |
|---|---|
| `fetch <name> [<track>]` | working tree at the pinned commit in `build/src/` |
| `apply <tool> <track>` | fetch + `git am --3way patches/<tool>/<track>/*.patch` |
| `export <tool> <track>` | `git format-patch --zero-commit --no-signature -N <pin>..HEAD` from `build/src/<tool>-<track>` into `patches/<tool>/<track>/`, deleting stale files |
| `compare <tool>` | side-by-side subjects of the two tracks; non-zero exit on mismatch |
| `version <tool> <track>` | the Debian version string of section 7 (`--repo-version` prints `R` alone) |
| `debianize <tool> <track>` | render `packaging/debian/<tool>/` into the tree |
| `bump` | section 8, upstream tracking |

Pure functions (version composition, series comparison, `describe` parsing)
are unit-tested; git-touching code is exercised by CI's apply step.

## 10. Verification plan

- CI green on the implementation PR: all four series apply, amd64 compile
  check, 24 deb jobs, 12 static jobs.
- Install test inside CI: `dpkg -i` the built deb in a clean `debian:<suite>`
  container, run `openFPGALoader --list-cables`, `openocd -v`, load
  `board/netv2-rpi.cfg` with `-c 'adapter driver rp1_pio_jtag'` guarded as a
  parse check (`openocd -f board/netv2-rpi-bcm2835gpio.cfg -c exit` needs
  hardware, so only `openocd -c 'script board/...' -c exit` syntax checks
  that do not `init`).
- Hardware, read-only, on `rpi5-netv2` (`rpi5-netv2.iot.welland.mithis.com`,
  NeTV2 XC7A100T, the dev box named in rp1-jtag `hw.md`): the arm64 static
  `openFPGALoader -c rp1pio --pins 27:22:4:17 --detect` and `--read-dna`; the
  static `openocd -f board/netv2-rpi.cfg -f fpga/xilinx-dna.cfg -c init -c
  'xilinx_print_dna [xc7_get_dna xc7.tap]' -c shutdown` must print the same
  DNA. **Not done in the first PR**: the arm64 static artifacts come from CI,
  which cannot run until the repository is public (see section 11). ECP5 TraceID cannot be hardware-verified here (the
  only ECP5 sits behind Apollo, not a GPIO harness) and is marked as such in
  the file header.
- Independent reviewer agent over the patches and workflows before the PR is
  declared ready (per Tim's review-before-presenting rule).

## 11. Blocked on Tim / follow-ups

1. **`APT_GPG_PRIVATE_KEY` secret**: generate a per-repo signing key
   (`fpgas-online-fpga-tools apt repository <me@mith.ro>`, rsa4096, no expiry
   as for rp1-jtag) and add it as an Actions secret. Until then the Pages
   publish job fails and the apt repo is empty; debs are still CI artifacts.
2. Branch protection and the LFS-archives UI toggle (classifier-blocked /
   UI-only, per the GitHub.md checklist).
3. Follow-up PR in `fpgas.online-infra`: replace the `mithro.github.io/rp1-jtag`
   source and `*-rp1pio` packages with this repo's source and
   `openfpgaloader-fpgasonline` / `openocd-fpgasonline`. Not done here
   (fix-prod-small / never deploy unasked).
4. Follow-up in `mithro/rp1-jtag`: drop the openFPGALoader/OpenOCD deb and
   static jobs, keep `librp1jtag` (its `drivers/` become the upstream of
   patches here; or move them here and leave rp1-jtag as the library only).
5. Consider upstreaming order: flash-info and the NeTV2 boards are closest to
   mergeable upstream; the rp1pio drivers need librp1jtag packaged somewhere
   upstream can depend on.
6. A GPG-signed `SHA256SUMS` on the release, signed with the apt key, once
   `APT_GPG_PRIVATE_KEY` exists (asked for by rpi-hwid, section 12).

## 12. Published contract (consumers)

`mithro/rpi-hwid` downloads the static openFPGALoader from this repository's
releases and fails closed on any shape change. These are therefore a
contract, changed only with notice to that repo:

- asset name `openFPGALoader-<version>-linux-<arch>` with `<arch>` in
  `arm64`, `armv7`, `armv6`; a `<asset>.sha256` beside it in `sha256sum`
  format (`<hex>  <filename>`);
- `latest.json` on the series release:
  `{"series": "vX.Y", "latest": {track: {tool: {arch: {"asset", "version"}}}}}`;
- every fpgas.online build's version contains `+fpgasonline.` and prints as
  `openFPGALoader v<version>`; that substring is the gate for "has
  `--flash-info`".

Behavioural note recorded for the same consumer: `--flash-info` (like any
`--detect -f`) on a JTAG-attached Xilinx loads the spiOverJtag bridge, so
it replaces the running design, and afterwards `Xilinx::post_flash_access()`
issues JPROGRAM so the FPGA reboots from its flash (or is left unconfigured
if it only had an SRAM design). There is no bridge-free path to the JEDEC id.
