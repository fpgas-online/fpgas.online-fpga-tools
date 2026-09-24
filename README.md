# fpgas.online-fpga-tools

Patched builds of [openFPGALoader](https://github.com/trabucayre/openFPGALoader)
and [OpenOCD](https://openocd.org/) for the [fpgas.online](https://fpgas.online)
Raspberry Pi images. This repository holds a maintained **patch series** for
functionality that is not upstream yet, pins the upstream sources it applies
to, and builds the result as Debian packages (a signed apt repository on
GitHub Pages) and as static binaries (GitHub Releases), on two tracks:

| Track | Upstream base | Debian packages |
|---|---|---|
| `stable` | latest upstream release tag | `openfpgaloader-fpgasonline`, `openocd-fpgasonline` |
| `master` | upstream default branch, pinned to a commit | `openfpgaloader-fpgasonline-git`, `openocd-fpgasonline-git` |

The exact pins are in [`upstreams.toml`](upstreams.toml).

## What the patches add

| Feature | openFPGALoader | OpenOCD | Upstream status |
|---|---|---|---|
| **RP1 PIO JTAG** on a Raspberry Pi 5: the RP1's PIO block shifts data instead of bit-banging PCIe-attached GPIOs (rp1-jtag's benchmarks: 6.5 s instead of 39 s for a 3.8 MB Artix-7 bitstream) | `rp1pio` cable | `rp1_pio_jtag` adapter, `interface/raspberrypi-rp1-pio.cfg` | not upstream; drivers are the copies [mithro/rp1-jtag](https://github.com/mithro/rp1-jtag) ships; they call librp1jtag (the `librp1jtag0` package here, linked in statically in the static binaries) |
| **Kosagi NeTV2** on a Pi header, old-style (Pi 1-4 `bcm2835gpio`) and new-style (Pi 5 RP1 PIO), plus `linuxgpiod`, a picker and a jtagspi flash variant | `netv2`, `netv2_100` boards with GPIO cable auto-detection | `board/netv2-rpi*.cfg` | openFPGALoader boards: [PR #643](https://github.com/trabucayre/openFPGALoader/pull/643) open |
| **Tiny Tapeout FPGA Demo Board** (iCE40UP5K behind an RP2040/RP2350 running MicroPython) | `tt_fpga` board, `tt_micropython` cable | | not upstream ([mithro/openFPGALoader tt-fpga-support](https://github.com/mithro/openFPGALoader/tree/tt-fpga-support)) |
| **SPI flash info and unique ID**: JEDEC id, SFDP parameters, factory unique id, machine-readable output | `--flash-info`, `--flash-info-json` | | not upstream ([mithro/openFPGALoader flash-info](https://github.com/mithro/openFPGALoader/tree/flash-info)) |
| **Device DNA / TraceID**: die-level identifiers | `--read-dna` (upstream) | `fpga/xilinx-dna.cfg` (upstream), `fpga/lattice-ecp5-traceid.cfg` (patch) | DNA is upstream in both; ECP5 TraceID is a patch |
| Version strings that name this build | `--Version` prints `v<version>` | `openocd -v` prints the fpgas.online suffix | packaging glue |

The spiOverJtag bridge bitstreams are upstream's, so the two openFPGALoader
tracks ship different sets. Only the `master` track has `efinix_ti375n484`,
`xcau10p-ffvb676`, `xcku3p-ffvb676`, `xcvu7p-flvb2104` and
`xcvu9p-flgb2104`; only the `stable` track has `xc7a100tfgg676`. A flash
access on a board whose part is missing from one track needs the package or
static tarball of the other.

Known limitation of the `stable` OpenOCD track: the 0.12.0 release only
supports libgpiod 1.x, so on trixie and sid (libgpiod 2.x) and in the arm64
static build the `linuxgpiod` adapter is left out. `rp1_pio_jtag` (Pi 5) and
`bcm2835gpio` (Pi 1-4) are unaffected; bookworm and the 32-bit static builds
keep `linuxgpiod`; the `master` track has it everywhere.

Series: [`patches/openfpgaloader/`](patches/openfpgaloader) (32 patches per
track), [`patches/openocd/`](patches/openocd) (4 per track). Both tracks
carry the same series; where the code differs between the upstream release
and master (a class rename, a moved Tcl file, an older adapter table) the two
copies differ in content but never in what they do.

## Versioning

Every version says which upstream it is and that it is the fpgas.online
build, and orders correctly for `apt`:

| Track | Format | Example |
|---|---|---|
| stable | `<upstream release>+fpgasonline.<patchset>` | `1.1.1+fpgasonline.0.0.post12` |
| master | `<last release>.post<N>+fpgasonline.<patchset>` | `1.1.1.post173+fpgasonline.0.0.post12` |

Both parts follow `git describe`. On master, `1.1.1.post173` is upstream's
commit 173 after its `v1.1.1` tag (`.post0` on the tag itself, so master
never looks like stable). `<patchset>` is this repository's own version from
`git describe` against its `vX.Y` series tags: `0.0.post12` is twelve
commits after `v0.0`. It is
the same string in the package version, in `openFPGALoader --Version`
(`v1.1.1+fpgasonline.0.0.post12`), in `openocd -v`
(`Open On-Chip Debugger 0.12.0+fpgasonline.0.0.post12`, or
`0.12.0+dev-01701-gb04ccfef+fpgasonline.0.0.post12` on master) and in the
static binary filenames.

## Installing

### Debian packages (bookworm, trixie, sid; arm64, armhf)

One flat apt repository per suite, signed with this repository's key.

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://fpgas.online/fpgas.online-fpga-tools/fpgas.online-fpga-tools.gpg \
  | sudo tee /etc/apt/keyrings/fpgas.online-fpga-tools.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/fpgas.online-fpga-tools.gpg] \
  https://fpgas.online/fpgas.online-fpga-tools/$(. /etc/os-release; echo $VERSION_CODENAME)/ ./" \
  | sudo tee /etc/apt/sources.list.d/fpgas.online-fpga-tools.list
sudo apt update
sudo apt install openfpgaloader-fpgasonline openocd-fpgasonline   # or the -git pair
```

The packages `Provide`/`Conflict`/`Replace` Debian's `openfpgaloader` and
`openocd` (and the earlier `openfpgaloader-rp1pio` / `openocd-rp1pio` from
mithro/rp1-jtag), so one `apt install` swaps them in place.

Both depend on two shared libraries:

| Package | Library | Where it comes from |
|---|---|---|
| `librp1jtag0` | RP1 PIO JTAG ([mithro/rp1-jtag](https://github.com/mithro/rp1-jtag)); exports the `rp1_jtag_*` API only | this repository, every suite. Its version (`0.0.post<N>+fpgasonline.<patchset>`, from rp1-jtag's `git describe`) continues the `0.0.postN` packages mithro/rp1-jtag published under the same name and sorts above them, so it upgrades them in place |
| `libpio0` | PIOLib, the `/dev/pio0` user-space API ([raspberrypi/utils](https://github.com/raspberrypi/utils) `piolib/`) | **bookworm, trixie: Raspberry Pi's archive** (`archive.raspberrypi.com`, which every Raspberry Pi OS install has configured). sid: this repository, versioned `<YYYYMMDD>+fpgasonline.<patchset>.g<sha7>`, date-first like Raspberry Pi's own (raspberrypi/utils has no tags to describe against) |

So on bookworm or trixie the packages install on Raspberry Pi OS, or on any
Debian with Raspberry Pi's archive added; plain Debian without it has no
`libpio0`. The static binaries below have no such dependency.

### Static binaries (arm64, armv7, armv6)

On the [Releases](https://github.com/fpgas-online/fpgas.online-fpga-tools/releases)
page, on the rolling release of the current series, named by version:

- `openFPGALoader-<version>-linux-<arch>.tar.gz`: `bin/openFPGALoader` plus
  `share/openFPGALoader/` (the spiOverJtag bridge bitstreams). The bridges
  are runtime data: every SPI-flash access on a JTAG-attached FPGA loads one,
  so a bare binary cannot even read a flash id. The binary looks in
  `/usr/share/openFPGALoader` unless `OPENFPGALOADER_SOJ_DIR` (upstream
  behaviour) points at the unpacked `share/openFPGALoader`.
- `openocd-<version>-linux-<arch>.tar.gz`: `bin/openocd` plus
  `share/openocd/scripts`. OpenOCD needs its Tcl tree; run it as
  `OPENOCD_SCRIPTS=<dir>/share/openocd/scripts bin/openocd ...` or with `-s`.
- `latest.json`: `{"series": "vX.Y", "latest": {track: {tool: {arch: {asset, version}}}}}` for scripts.

Every asset has a `.sha256` beside it.

The `libgpiod` backends (openFPGALoader's `-c libgpiod` cable, OpenOCD's
`linuxgpiod` adapter) speak the kernel's GPIO character device, and the
rule is the libgpiod each build links: **a build linking libgpiod 2.x
needs Linux 5.10 or newer**, because that is where the v2 chardev
interface arrived. On an older kernel libgpiod 2 does not report the
missing interface, it aborts on an assertion in
`gpiod_line_request_set_values_subset`.

Today that means the arm64 builds (libgpiod 2.x, so a Pi 4 or 5 on any
current Raspberry Pi OS) and the armv7/armv6 builds (libgpiod 1.x, which
works on the much older kernels those Pis run — a Pi 3 on stretch, say).
The `rp1pio` and `bcm2835gpio` backends do not use libgpiod and are
unaffected on any kernel.

## Using the additions

```bash
# Pi 5, NeTV2: detect and read the Device DNA over the RP1 PIO
sudo openFPGALoader -c rp1pio --pins 27:22:4:17 --detect      # --pins is TDI:TDO:TCK:TMS
sudo openFPGALoader -b netv2_100 --read-dna                    # cable picked automatically
sudo openocd -f board/netv2-rpi.cfg -f fpga/xilinx-dna.cfg -c init \
    -c "xilinx_print_dna [xc7_get_dna xc7.tap]" -c shutdown     # xilinx-dna.cfg is upstream's

# Any Pi, NeTV2: board/netv2-rpi.cfg picks bcm2835gpio on a Pi 1-4 (Alphamax wiring),
# rp1_pio_jtag on a Pi 5 with /dev/pio0, linuxgpiod on a Pi 5 without.
# PLD naming differs per track: `pld load xc7.pld design.bit` on the git track,
# `pld load 0 design.bit` on the 0.12.0 release (which addresses PLDs by index).

# SPI flash identification (loads the spiOverJtag bridge into the FPGA)
sudo openFPGALoader -b netv2_100 --detect -f --flash-info --flash-info-json flash.json

# Tiny Tapeout FPGA Demo Board over USB
openFPGALoader -b tt_fpga design.bin
```

## Repository layout

```
upstreams.toml            pinned inputs: openFPGALoader, OpenOCD, librp1jtag, piolib
patches/<tool>/<track>/   git format-patch series
fpgatools/                stdlib-only CLI: fetch, apply, export, compare, version, debianize, bump
packaging/debian/<name>/  debhelper templates rendered by `fpgatools debianize`
                          (the two tools, and the libraries rp1jtag and piolib)
packaging/static/         Alpine (musl) static build scripts
packaging/build-libs.sh   builds librp1jtag0 (and libpio0 where Raspberry Pi has none)
packaging/build-deb.sh    what the deb workflow runs inside debian:<suite>
packaging/libpio.sh       which suites take libpio0 from Raspberry Pi's archive
packaging/keys/           Raspberry Pi's archive keyring, for the build containers
packaging/release.py      uploads static assets to the series release
.github/workflows/        ci.yml, debs.yml, static.yml, daily.yml
docs/superpowers/         design spec and implementation plan
```

## Working on the patches

Never edit a `.patch` by hand: each series is the export of a git branch.

```bash
uv run fpgatools apply openfpgaloader master   # pinned upstream + series in build/src/openfpgaloader-master
# ... edit, `git commit --amend`, `git rebase -i`, add commits in that tree ...
uv run fpgatools export openfpgaloader master  # rewrite patches/openfpgaloader/master/
uv run fpgatools apply openfpgaloader stable   # then the same for the other track
uv run fpgatools compare openfpgaloader        # both tracks must carry the same series
```

### Building locally

Everything CI does runs in plain containers, so it runs on a developer box
with Docker:

```bash
# a Debian package, native architecture; builds librp1jtag0 (and libpio0
# where Raspberry Pi's archive has none) first, into built-libs/
docker run --rm -v "$PWD:/work" -w /work -e REPO=/work -e SUITE=trixie debian:trixie \
  sh -ec 'packaging/build-deb.sh openocd stable'          # -> built-debs/*.deb

# a static binary
uv run fpgatools fetch piolib && uv run fpgatools fetch rp1jtag && uv run fpgatools apply openfpgaloader stable
docker run --rm -v "$PWD:/work" -w /work -e REPO=/work alpine:3.21 \
  sh -ec "packaging/static/openfpgaloader.sh stable $(uv run fpgatools version openfpgaloader stable)"
```

`uv run pytest -q` and `uv run ruff check` cover the tooling;
`shellcheck packaging/*.sh packaging/static/*.sh` the scripts.

### Following upstream

`daily.yml` runs every day at 05:23 UTC and needs nobody:

1. `fpgatools bump` moves the `master` pins to the upstream heads, the
   `stable` pins to the newest release tag and the librp1jtag and PIOLib
   pins to their heads, and re-applies every series.
2. The whole deb matrix and the whole static matrix build against those
   candidate pins, on a branch, publishing nothing.
3. Only if every one of those builds passed and a pin actually moved does
   main fast-forward to the candidate, which builds again and publishes.
4. Any failure — a series that stopped applying, a build that broke — leaves
   the pins alone and opens (or updates) one issue naming what broke.

So a `-git` package follows upstream master within a day, and a day when
upstream breaks us is a red issue rather than a broken publish. When no pin
moved the matrix still runs, which catches rot in the base images and
toolchains rather than only upstream changes.

**When things change.** On a day a pin moved, the packages and release
assets change between about 05:25 and 05:50 UTC (the deb matrix takes about
10 minutes and the static one about 4, and a bump day builds twice). On any
other day nothing is published unless someone merges a pull request. A
fleet-wide read that must not see a tool change mid-way should avoid
05:20–06:00 UTC.

A series marked ❌ in the issue needs a rebase in a build tree, as above.
`uv run fpgatools bump --dry-run` reproduces the report locally.

The same run can also be started on demand by a `repository_dispatch` of
event type `rp1jtag-updated`, which mithro/rp1-jtag sends when its main
moves (it needs a token with access to this repository; without one the
next daily run picks the change up anyway). The window above does not apply
to those runs.

## Publishing

Only `main` publishes. `debs.yml` signs and deploys the apt repository to
GitHub Pages through [mithro/apt-repo-action](https://github.com/mithro/apt-repo-action)
(secret `APT_GPG_PRIVATE_KEY`, per-repository signing key); `static.yml`
uploads to the series prerelease with the repository's own token. Pull
requests build everything and publish nothing.

## Licence

The tooling in this repository is Apache-2.0. The patches are contributions
to their upstream projects and carry those licences: openFPGALoader
Apache-2.0, OpenOCD GPL-2.0-or-later. librp1jtag (Apache-2.0) and PIOLib
(BSD-3-Clause) are shared libraries in the Debian packages and linked in
statically in the static binaries.

Design: [`docs/superpowers/specs/2026-09-22-fpga-tools-design.md`](docs/superpowers/specs/2026-09-22-fpga-tools-design.md).
