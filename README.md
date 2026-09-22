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
| **RP1 PIO JTAG** on a Raspberry Pi 5: the RP1's PIO block shifts data instead of bit-banging PCIe-attached GPIOs (rp1-jtag's benchmarks: 6.5 s instead of 39 s for a 3.8 MB Artix-7 bitstream) | `rp1pio` cable | `rp1_pio_jtag` adapter, `interface/raspberrypi-rp1-pio.cfg` | not upstream; drivers are the copies [mithro/rp1-jtag](https://github.com/mithro/rp1-jtag) ships in its own debs; needs librp1jtag, linked in statically |
| **Kosagi NeTV2** on a Pi header, old-style (Pi 1-4 `bcm2835gpio`) and new-style (Pi 5 RP1 PIO), plus `linuxgpiod`, a picker and a jtagspi flash variant | `netv2`, `netv2_100` boards with GPIO cable auto-detection | `board/netv2-rpi*.cfg` | openFPGALoader boards: [PR #643](https://github.com/trabucayre/openFPGALoader/pull/643) open |
| **Tiny Tapeout FPGA Demo Board** (iCE40UP5K behind an RP2040/RP2350 running MicroPython) | `tt_fpga` board, `tt_micropython` cable | | not upstream ([mithro/openFPGALoader tt-fpga-support](https://github.com/mithro/openFPGALoader/tree/tt-fpga-support)) |
| **SPI flash info and unique ID**: JEDEC id, SFDP parameters, factory unique id, machine-readable output | `--flash-info`, `--flash-info-json` | | not upstream ([mithro/openFPGALoader flash-info](https://github.com/mithro/openFPGALoader/tree/flash-info)) |
| **Device DNA / TraceID**: die-level identifiers | `--read-dna` (upstream) | `fpga/xilinx-dna.cfg` (upstream), `fpga/lattice-ecp5-traceid.cfg` (patch) | DNA is upstream in both; ECP5 TraceID is a patch |
| Version strings that name this build | `--Version` prints `v<version>` | `openocd -v` prints the fpgas.online suffix | packaging glue |

Known limitation of the `stable` OpenOCD track: the 0.12.0 release only
supports libgpiod 1.x, so on trixie and sid (libgpiod 2.x) and in the arm64
static build the `linuxgpiod` adapter is left out. `rp1_pio_jtag` (Pi 5) and
`bcm2835gpio` (Pi 1-4) are unaffected; bookworm and the 32-bit static builds
keep `linuxgpiod`; the `master` track has it everywhere.

Series: [`patches/openfpgaloader/`](patches/openfpgaloader) (24 patches per
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
| master | `<last release>+git<YYYYMMDD>.<sha7>+fpgasonline.<patchset>` | `1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12` |

`<patchset>` is this repository's own version from `git describe` against
its `vX.Y` series tags: `0.0.post12` is twelve commits after `v0.0`. It is
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
curl -fsSL https://fpgas.online/fpgas.online-fpga-tools/fpgas-online-fpga-tools.asc \
  | sudo gpg --dearmor -o /etc/apt/keyrings/fpgas-online-fpga-tools.gpg
echo "deb [signed-by=/etc/apt/keyrings/fpgas-online-fpga-tools.gpg] \
  https://fpgas.online/fpgas.online-fpga-tools/$(. /etc/os-release; echo $VERSION_CODENAME)/ ./" \
  | sudo tee /etc/apt/sources.list.d/fpgas-online-fpga-tools.list
sudo apt update
sudo apt install openfpgaloader-fpgasonline openocd-fpgasonline   # or the -git pair
```

The packages `Provide`/`Conflict`/`Replace` Debian's `openfpgaloader` and
`openocd` (and the earlier `openfpgaloader-rp1pio` / `openocd-rp1pio` from
mithro/rp1-jtag), so one `apt install` swaps them in place.

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
packaging/debian/<tool>/  debhelper templates rendered by `fpgatools debianize`
packaging/static/         Alpine (musl) static build scripts
packaging/build-deb.sh    what the deb workflow runs inside debian:<suite>
packaging/release.py      uploads static assets to the series release
.github/workflows/        ci.yml, debs.yml, static.yml, update-upstream.yml
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
# a Debian package, native architecture
docker run --rm -v "$PWD:/work" -w /work -e REPO=/work debian:trixie \
  sh -ec 'packaging/build-deb.sh openocd stable'          # -> built-debs/*.deb

# a static binary
uv run fpgatools fetch piolib && uv run fpgatools fetch rp1jtag && uv run fpgatools apply openfpgaloader stable
docker run --rm -v "$PWD:/work" -w /work -e REPO=/work alpine:3.21 \
  sh -ec "packaging/static/openfpgaloader.sh stable $(uv run fpgatools version openfpgaloader stable)"
```

`uv run pytest -q` and `uv run ruff check` cover the tooling;
`shellcheck packaging/*.sh packaging/static/*.sh` the scripts.

### Following upstream

`update-upstream.yml` runs `fpgatools bump` weekly: it moves the `master`
pins to the upstream heads, the `stable` pins to the newest release tag,
re-applies every series and opens a pull request whose body says which
series still apply. A series marked ❌ needs a rebase in a build tree as
above. A pull request opened by the workflow's own token does not trigger CI
until someone pushes to it or reopens it.

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
(BSD-3-Clause) are linked in statically.

Design: [`docs/superpowers/specs/2026-09-22-fpga-tools-design.md`](docs/superpowers/specs/2026-09-22-fpga-tools-design.md).
