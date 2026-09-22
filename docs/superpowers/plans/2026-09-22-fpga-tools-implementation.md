# fpgas.online-fpga-tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A repo that turns pinned upstream openFPGALoader/OpenOCD plus a
maintained patch series into versioned debs (per-suite Pages apt repo) and
static binaries (GitHub Releases), on two tracks (stable, master).

**Architecture:** `upstreams.toml` pins every input; `patches/<tool>/<track>/`
holds `git format-patch` series; the stdlib-only `fpgatools` CLI fetches,
applies, exports, versions and debianizes; three workflows build (debs,
static, CI) and one bumps pins weekly.

**Tech Stack:** Python 3.11+ stdlib (`tomllib`, `subprocess`), `uv`, pytest,
ruff; debhelper 13 `dh` (cmake / autotools); Alpine musl for static builds;
`mithro/apt-repo-action` reusable workflow; GitHub Actions `ubuntu-24.04-arm`
with Docker `--platform` + binfmt.

**Spec:** `docs/superpowers/specs/2026-09-22-fpga-tools-design.md`

## Global Constraints

- Tracks are exactly `stable` and `master`; tools are exactly `openfpgaloader` and `openocd`.
- Patches are plain `git format-patch --zero-commit --no-signature` output, numbered `NNNN-`.
- Deb version: stable `<tag sans v>+fpgasonline.<R>`; master `<last tag sans v>+git<YYYYMMDD>.<sha7>+fpgasonline.<R>`; `R` = `X.Y` at a `vX.Y` tag, `X.Y.postN` after, `0.0.post<count>` with no tag.
- Binary package names: `openfpgaloader-fpgasonline`, `openocd-fpgasonline`, and `-git` suffixed for master.
- Suites: bookworm trixie sid. Deb arches: arm64 armhf. Static arches: arm64 armv7 armv6.
- librp1jtag + piolib are built from pinned source and linked statically; debs depend only on Debian libraries.
- Python tooling: `uv run`, stdlib only, ruff clean, pytest green.
- No new tool functionality: only extraction, rebasing, version plumbing and packaging glue.
- Commits are small and individually meaningful; every commit ends with the session's attribution lines.

Pins (measured 2026-09-22):

| name | ref | commit | describe / date |
|---|---|---|---|
| openfpgaloader stable | v1.1.1 | 85be4fa02b2dd6a83716d7dfac3d25bbd260ff7b | |
| openfpgaloader master | master | 24e46d13bb8f2bc9371e9ca8443ece2fafc4b20d | v1.1.1-173-g24e46d1, 2026-09-15 |
| openocd stable | v0.12.0 | 9ea7f3d647c8ecf6b0f1424002dfc3f4504a162c | |
| openocd master | master | b04ccfeff73fb01b62e52e3d95882e8ff426016d | v0.12.0-1701-gb04ccfef, 2026-09-20 |
| rp1jtag | main | d9d7d8d186f103bb16a3eafb9c7296d5e2a33c47 | |
| piolib (raspberrypi/utils) | master | ebc4a56bac3a896d5c14e56fe27dcd6cb36dd373 | |

---

### Task 1: Project scaffold, pins file, CLAUDE.md

**Files:**
- Create: `pyproject.toml`, `fpgatools/__init__.py`, `fpgatools/__main__.py`, `fpgatools/cli.py`, `upstreams.toml`, `CLAUDE.md`, `tests/__init__.py`

**Interfaces:**
- Produces: `uv run fpgatools <subcommand>` entry point (`fpgatools.cli:main`); `upstreams.toml` schema below.

- [ ] `pyproject.toml`: project `fpgatools` version 0 (versioning is git-derived), `requires-python = ">=3.11"`, `[project.scripts] fpgatools = "fpgatools.cli:main"`, dev deps `pytest`, `ruff`; `[tool.ruff] line-length = 100`.
- [ ] `upstreams.toml` exactly:

```toml
# Every external input of the build, pinned by commit. `fpgatools bump`
# rewrites the master entries; humans move the stable ones.

[openfpgaloader]
url = "https://github.com/trabucayre/openFPGALoader.git"
[openfpgaloader.stable]
ref = "v1.1.1"
commit = "85be4fa02b2dd6a83716d7dfac3d25bbd260ff7b"
[openfpgaloader.master]
ref = "master"
commit = "24e46d13bb8f2bc9371e9ca8443ece2fafc4b20d"
describe = "v1.1.1-173-g24e46d1"
date = "2026-09-15"

[openocd]
url = "https://github.com/openocd-org/openocd.git"
[openocd.stable]
ref = "v0.12.0"
commit = "9ea7f3d647c8ecf6b0f1424002dfc3f4504a162c"
[openocd.master]
ref = "master"
commit = "b04ccfeff73fb01b62e52e3d95882e8ff426016d"
describe = "v0.12.0-1701-gb04ccfef"
date = "2026-09-20"

[rp1jtag]
url = "https://github.com/mithro/rp1-jtag.git"
ref = "main"
commit = "d9d7d8d186f103bb16a3eafb9c7296d5e2a33c47"

[piolib]
url = "https://github.com/raspberrypi/utils.git"
ref = "master"
commit = "ebc4a56bac3a896d5c14e56fe27dcd6cb36dd373"
subdir = "piolib"
```

- [ ] `cli.py`: argparse with subparsers registered by later tasks; `main()` returns exit code.
- [ ] `CLAUDE.md`: build/test commands, the patch workflow (checkout → edit in `build/src/<tool>-<track>` → commit → `export`), the version rules, "never edit patches by hand: edit the commit and re-export".
- [ ] Commit: `build: project scaffold, pinned upstreams and CLAUDE.md`.

### Task 2: `fpgatools.pins` and `fpgatools.version`

**Files:**
- Create: `fpgatools/pins.py`, `fpgatools/version.py`, `tests/test_pins.py`, `tests/test_version.py`

**Interfaces:**
- Produces:
  - `pins.load(path: Path = REPO/"upstreams.toml") -> Pins`; `Pins.tool(name) -> Upstream(url)`; `Pins.pin(name, track=None) -> Pin(ref, commit, describe|None, date|None, subdir|None)`. Names: `openfpgaloader`, `openocd`, `rp1jtag`, `piolib`.
  - `version.repo_version(repo: Path) -> str` (port of deb-version.py: `git describe --tags --long --match 'v[0-9]*.[0-9]*'`).
  - `version.package_version(tool: str, track: str, pins: Pins, repo_version: str) -> str`.
  - `version.tool_version_string(tool, track, pins, repo_version) -> str`: what the binary should print. openfpgaloader → `v<pkgver>`; openocd stable → `+fpgasonline.<R>` (appended by guess-rev to AC_INIT's `0.12.0`), openocd master → `-<NNNNN>-g<sha>+fpgasonline.<R>` built from `describe`.
  - `version.upstream_base(track, pin) -> str`: `1.1.1` for a stable tag `v1.1.1`; for master, the tag part of `describe` (`v1.1.1-173-g24e46d1` → `1.1.1`).

- [ ] Tests first: `package_version("openfpgaloader","stable",pins,"0.0.post12") == "1.1.1+fpgasonline.0.0.post12"`; master → `"1.1.1+git20260915.24e46d1+fpgasonline.0.0.post12"`; openocd master → `"0.12.0+git20260920.b04ccfe+fpgasonline.0.0.post12"`; `repo_version` on a temp git repo: no tag → `0.0.post1`, tag `v0.0` on HEAD → `0.0`, one commit later → `0.0.post1`; invalid describe → `ValueError`.
- [ ] Implement; `uv run pytest -q`; `uv run ruff check`.
- [ ] Wire `fpgatools version <tool> <track>` and `fpgatools version --repo`.
- [ ] Commit: `fpgatools: pins loader and version strings`.

### Task 3: `fpgatools.patchset` (fetch / apply / export / compare)

**Files:**
- Create: `fpgatools/patchset.py`, `fpgatools/gitutil.py`, `tests/test_patchset.py`

**Interfaces:**
- Produces:
  - `patchset.src_dir(name, track=None) -> Path` = `REPO/build/src/<name>[-<track>]`.
  - `patchset.fetch(name, track=None, *, force=False) -> Path`: `git init -q`, `git remote add origin <url>`, `git fetch --depth 1 origin <commit>`, `git checkout -q --detach FETCH_HEAD`; sets local `user.name`/`user.email` to `fpgas.online build`/`builds@fpgas.online` (needed by `git am`). If the dir exists and HEAD's first-parent chain contains the pin, leave it (so a developer's in-progress commits survive) unless `force`.
  - `patchset.series_dir(tool, track) -> Path`; `patchset.series(tool, track) -> list[Path]` sorted.
  - `patchset.apply(tool, track) -> Path`: fetch(force=True) then `git am --3way --whitespace=nowarn <patches>`; on failure run `git am --abort` and raise `PatchConflict(patch_name)`.
  - `patchset.export(tool, track)`: `git format-patch --zero-commit --no-signature --no-numbered? ` NO: numbered `NNNN-` names are wanted, so `git format-patch --zero-commit --no-signature -o <series_dir> <pin>..HEAD`, after deleting existing `*.patch` in the dir.
  - `patchset.compare(tool) -> list[tuple[str|None, str|None]]` pairing subjects of the stable and master series in order; `compare_ok(pairs) -> bool` true iff every pair has both sides equal.
  - Subject extraction: first `Subject:` header, `[PATCH n/m] ` prefix stripped, continuation lines joined.
- [ ] Tests (no network): build a tiny upstream repo in `tmp_path` with two commits, point a fake `Pins` at it via `file://` url and the second commit, verify `fetch` yields a detached HEAD at the commit; make a change, `export` into a temp series dir, verify the file name pattern and `Subject:` line; `apply` on a fresh fetch reproduces the tree; `compare` on two temp series dirs with matching / mismatching subjects.
- [ ] Wire `fpgatools fetch|apply|export|compare`.
- [ ] Commit: `fpgatools: fetch, apply, export and compare patch series`.

### Task 4: openFPGALoader patch series (stable and master)

**Files:**
- Create: `patches/openfpgaloader/master/0001..0022-*.patch`, `patches/openfpgaloader/stable/0001..0022-*.patch`

Sources are already exported into `tmp/src-flash-info/`, `tmp/src-tt/`, `tmp/src-rp1netv2/` (from `mithro/openFPGALoader`), and `~/github/mithro/rp1-jtag/drivers/openfpgaloader/`.

- [ ] `fpgatools fetch openfpgaloader master`; in `build/src/openfpgaloader-master`:
  1. Commit patch 1 by hand (author Tim Ansell <me@mith.ro>): in `CMakeLists.txt` replace `add_definitions(-DVERSION=\"v${PROJECT_VERSION}\")` with
     ```cmake
     set(OPENFPGALOADER_VERSION "v${PROJECT_VERSION}" CACHE STRING
         "Version string reported by --Version (distributors append their suffix)")
     add_definitions(-DVERSION=\"${OPENFPGALOADER_VERSION}\")
     ```
     Subject `cmake: allow the reported version string to be overridden`.
  2. `git am tmp/src-flash-info/*.patch` (already based on this commit; must apply clean).
  3. `git am --3way tmp/src-rp1netv2/0002-*.patch` (netv2 boards + autodetect). Resolve conflicts in `board.hpp` (neighbouring board lines) and `main.cpp` (`printInfo`/`args.cable` context).
  4. rp1pio driver: copy `rp1PioJtag.{cpp,hpp}` from rp1-jtag into `src/`, then make the edits `integrate.py` makes, but with **current** anchors: `cable.hpp` enum after `MODE_XPCU`, list entry before `#ifdef ENABLE_LIBGPIOD`; `jtag.cpp` include + factory case before the LIBGPIOD ones; `CMakeLists.txt` option block before `# Libgpiod is only available on Linux OS.`, source block before `# libGPIOD support`, link block after the last `endif(ENABLE_LIBGPIOD)`, using `pkg_check_modules(RP1JTAG REQUIRED rp1jtag)` so static `.a` + piolib come from `rp1jtag.pc` when present, else the find_library fallback. Do **not** re-add `USE_DEVICE_ARG`. Also `doc/guide/install.rst`? no; add a `doc/guide/advanced.rst` short section "rp1pio cable" (2 paragraphs from rp1-jtag README) so `--help` and docs agree. Author Tim; subject `Add rp1pio cable driver for RP1 PIO JTAG on RPi 5`.
  5. `git am --3way tmp/src-tt/000{1,2,3,4,5,7}-*.patch` one at a time; fold `0006-tt_fpga-fix-issues-found-in-code-review.patch` into the commit(s) it fixes with `git rebase -i`-free method: apply 0006 then `git reset --soft` is messy — instead apply 0001..0005, apply 0006, then `git commit --fixup` is not available for already-applied commits, so: apply all seven, then run `git rebase --autosquash` is interactive… Use this non-interactive method: apply 0001-0005 and 0007 with `--3way`; for 0006 use `git apply --3way tmp/src-tt/0006-*.patch` (index only, no commit) and `git commit --amend` into the last tt commit if all its hunks touch `ttMicropython.cpp`; otherwise split with `git add -p` guided by file. Also drop `remove mcufw debug print` wording from 0003's subject if the print is gone after folding; keep subjects otherwise. Conflicts to expect: `CMakeLists.txt` `USE_DEVICE_ARG` block no longer exists (drop that hunk), `board.hpp` enum spacing, `main.cpp` dispatch near `COMM_DFU`.
  6. Build check in `debian:trixie` (arm64, native docker): `cmake -B build -DENABLE_CABLE_ALL=ON -DENABLE_VENDORS_ALL=ON -DENABLE_LIBGPIOD=ON -DENABLE_RP1_PIO=OFF -DENABLE_TT_MICROPYTHON=ON` (rp1pio needs librp1jtag: build piolib+librp1jtag first into the container and turn it ON: the full recipe is Task 6's `packaging/build-rp1jtag.sh`; write that script now and use it here). `./build/openFPGALoader --list-cables` shows `rp1pio`, `tt_micropython`, `libgpiod`; `--list-boards | grep -E 'netv2|tt_fpga'`; `--Version` prints the override when `-DOPENFPGALOADER_VERSION=vTEST` is passed.
  7. `fpgatools export openfpgaloader master`; `git add patches/`; commit `patches(openfpgaloader/master): version override, flash-info, NeTV2, rp1pio, Tiny Tapeout FPGA board`.
- [ ] `fpgatools fetch openfpgaloader stable`; `git am --3way patches/openfpgaloader/master/*.patch` one by one; resolve (expect: flash-info against the 173-commit gap in `spiFlash.cpp`/`main.cpp`; XPCU cable absent in v1.1.1 so the enum anchor is `MODE_ESP`); build-check the same way; `fpgatools export openfpgaloader stable`; `fpgatools compare openfpgaloader` must pass; commit `patches(openfpgaloader/stable): backport the master series to v1.1.1`.

### Task 5: OpenOCD patch series (stable and master)

**Files:**
- Create: `patches/openocd/{master,stable}/0001..0004-*.patch`

- [ ] `fpgatools fetch openocd master`; in the tree:
  1. Patch 1 `guess-rev.sh: honour OPENOCD_LOCAL_VERSION`: at the top after `cd "${1:-.}" || usage` insert
     ```sh
     # Distributors building from an exported tree set the local version
     # themselves; it replaces everything this script would derive.
     if [ -n "$OPENOCD_LOCAL_VERSION" ]; then
     	printf '%s' "$OPENOCD_LOCAL_VERSION"
     	exit 0
     fi
     ```
  2. Patch 2 `jtag/drivers: add rp1_pio_jtag adapter for the Raspberry Pi 5 RP1 PIO`: copy `rp1_pio_jtag.c`; apply the exact `configure.ac` / `src/jtag/drivers/Makefile.am` / `src/jtag/interface.h` / `src/jtag/interfaces.c` edits from rp1-jtag `drivers/openocd/integrate.py` (run the script on the tree, then `git add -A`, then diff-review); add `tcl/interface/raspberrypi-rp1-pio.cfg`:
     ```tcl
     # Raspberry Pi 5: JTAG through the RP1 southbridge's PIO block (librp1jtag).
     # Pins are board wiring; set them after sourcing this file, e.g.
     #   rp1_pio_jtag jtag_nums 4 17 27 22   ;# TCK TMS TDI TDO (BCM numbers)
     adapter driver rp1_pio_jtag
     transport select jtag
     ```
     and a `doc/openocd.texi` `@deffn {Interface Driver} {rp1_pio_jtag}` entry next to `linuxgpiod` describing `jtag_nums`, `srst_num`, `trst_num`.
  3. Patch 3 `tcl/board: Kosagi NeTV2 driven from a Raspberry Pi header`: files `tcl/board/netv2-rpi.cfg`, `netv2-rpi-rp1pio.cfg`, `netv2-rpi-bcm2835gpio.cfg`, `netv2-rpi-linuxgpiod.cfg`, `netv2-rpi-spiflash.cfg`. Each header states pins (TCK 4 / TMS 17 / TDI 27 / TDO 22 / SRST 24), the two pin-order conventions, and which Pi generations it serves. `netv2-rpi.cfg`:
     ```tcl
     if {[file exists /dev/pio0]} {
         source [find board/netv2-rpi-rp1pio.cfg]
     } else {
         source [find board/netv2-rpi-bcm2835gpio.cfg]
     }
     ```
     bcm2835gpio variant reads `NETV2_PERIPHERAL_BASE` if set (`if {![info exists NETV2_PERIPHERAL_BASE]} { set NETV2_PERIPHERAL_BASE 0x3F000000 }`), uses `bcm2835gpio peripheral_base`, `bcm2835gpio speed_coeffs 146203 36` (the value rpi-hwid measured for 1 MHz), `bcm2835gpio jtag_nums 4 17 27 22`, `bcm2835gpio srst_num 24`, `reset_config none`, `adapter speed 1000`, `source [find cpld/xilinx-xc7.cfg]`. linuxgpiod variant: `NETV2_GPIOCHIP` default 0, `adapter gpio tck -chip $NETV2_GPIOCHIP 4` etc. rp1pio variant from rp1-jtag `netv2_35t.cfg` (title generalised: any 7-series NeTV2, XC7A35T or XC7A100T; `cpld/xilinx-xc7.cfg` matches both). spiflash variant from `netv2_35t_spi.cfg`, sourcing `netv2-rpi.cfg` then `cpld/jtagspi.cfg`, with the `netv2_spi_init` proc.
  4. Patch 4 `tcl/fpga: read a Xilinx 7-series Device DNA and a Lattice ECP5 TraceID`: `tcl/fpga/xilinx-xc7-dna.cfg`
     ```tcl
     # Xilinx 7-series FUSE_DNA: 6-bit IR 0x32, 64 bits out of the DR, bit-reversed,
     # 57 significant bits. Same sequence as openFPGALoader's xilinx.cpp
     # (dumpDNA) and rpi-hwid's fpga.py. Read-only; leaves configuration alone.
     proc xc7_read_dna {tap} {
         jtag arp_init
         irscan $tap 0x32
         set raw [drscan $tap 64 0]
         set v 0x$raw
         set rev 0
         for {set i 0} {$i < 64} {incr i} {
             set rev [expr {($rev << 1) | (($v >> $i) & 1)}]
         }
         set dna [expr {$rev & 0x1ffffffffffffff}]
         if {$dna == 0 || $dna == 0x1ffffffffffffff} {
             error "xc7_read_dna: chain shifted all zeros or all ones (unpowered or absent device)"
         }
         echo [format "DNA=0x%016x" $dna]
         return $dna
     }
     ```
     and `tcl/fpga/lattice-ecp5-traceid.cfg` with `proc ecp5_read_traceid {tap}`: `irscan $tap 0x19` (UIDCODE_PUB, 8-bit IR), `drscan $tap 64 0`, low 56 bits are factory (`& 0x00ffffffffffffff`), same all-0/all-1 refusal, prints `TRACEID=0x%014x`. Header notes: not hardware-verified here (the only ECP5 on site is reached through Apollo), transcribed from rpi-hwid's Apollo path and ecpdap's `read_uid()`. Check `drscan` result endianness against OpenOCD's documented behaviour (returns a hex string of the value, LSB-first bit order as shifted) and against rpi-hwid's `RAWDNA` handling (`int(m,16)` then bit-reverse) — identical treatment, so the proc reproduces rpi-hwid.
  5. Build check in `debian:trixie`: `./bootstrap nosubmodule && ./configure --enable-rp1-pio-jtag --enable-bcm2835gpio --enable-linuxgpiod --enable-sysfsgpio --disable-werror && make -j`; `OPENOCD_LOCAL_VERSION=+test` must appear in `src/openocd -v`; `src/openocd -s tcl -c 'adapter driver rp1_pio_jtag' -c shutdown` must not say invalid; `src/openocd -s tcl -f board/netv2-rpi-bcm2835gpio.cfg -c 'exit'` must fail only on hardware (mmap), not on syntax — use `-c 'script board/netv2-rpi-bcm2835gpio.cfg'` without `init` and `tcl_port disabled`.
  6. `fpgatools export openocd master`; commit.
- [ ] Stable (v0.12.0): fetch, `git am --3way` the four; expected divergence: `interfaces.c`/`interface.h` layout, `configure.ac` adapter tables, `adapter gpio` command exists in 0.12 (yes, since 0.12.0-rc1), `bcm2835gpio peripheral_base` new syntax exists in 0.12. Build check; export; `compare`; commit.

### Task 6: Build helpers for librp1jtag/piolib and `fpgatools debianize`

**Files:**
- Create: `packaging/build-rp1jtag.sh`, `packaging/debian/openfpgaloader/{control.in,rules,copyright,source/format,install}`, `packaging/debian/openocd/{control.in,rules,copyright,source/format}`, `fpgatools/debianize.py`, `tests/test_debianize.py`

**Interfaces:**
- `packaging/build-rp1jtag.sh <src-piolib> <src-rp1jtag> <prefix>`: cmake piolib (`--target pio`, `--component piolib`, PIC on) then librp1jtag (Release, PIC on, `CMAKE_INSTALL_PREFIX=<prefix>`), installs `rp1jtag.pc`; asserts no "compiled without PIOLib support" string in the static lib.
- `debianize.render(tool, track, pins, repo_version, dest: Path)`: writes `dest/debian/` from `packaging/debian/<tool>/`, substituting `@PACKAGE@`, `@VERSION@`, `@CONFLICTS@` (the other names), `@TRACK@`, `@UPSTREAM_URL@`, `@UPSTREAM_COMMIT@`; writes `debian/changelog` with the HEAD commit date of *this* repo; source name `<package>`; format `3.0 (native)`.
- `control.in` for openfpgaloader: `Section: electronics`, `Build-Depends: debhelper-compat (= 13), cmake, pkg-config, libftdi1-dev, libusb-1.0-0-dev, libhidapi-dev, libudev-dev, libgpiod-dev, zlib1g-dev`, `Provides: openfpgaloader`, `Conflicts: openfpgaloader, openfpgaloader-rp1pio, @CONFLICTS@`, `Replaces:` same, `Description:` names the base version and the five feature areas.
- `rules` openfpgaloader: `dh $@ --buildsystem=cmake` with `override_dh_auto_configure` passing the flag set from the spec plus `-DOPENFPGALOADER_VERSION=v@VERSION@` and `-DCMAKE_PREFIX_PATH=/usr/local` (where build-rp1jtag installed); `override_dh_auto_test:` empty.
- openocd: `Section: embedded`, Build-Depends `debhelper-compat (= 13), autoconf, automake, libtool, pkg-config, texinfo, libusb-1.0-0-dev, libftdi1-dev, libhidapi-dev, libjim-dev, libgpiod-dev, libcapstone-dev`; `rules`: `dh $@ --with autoreconf`, configure flags from the spec plus `--disable-doxygen-html --disable-doxygen-pdf --disable-internal-jimtcl`, `export OPENOCD_LOCAL_VERSION`, udev rules to `/usr/lib/udev/rules.d/60-openocd.rules` via `dh_install`.
- [ ] Tests: `render` on a tmp dir produces `control` with the right `Package:` and `Conflicts:` for both tracks and a valid `changelog` first line (`<pkg> (<ver>) unstable; urgency=medium`).
- [ ] `fpgatools debianize <tool> <track> [--dest build/src/<tool>-<track>]`.
- [ ] Local full deb build in `debian:trixie` (arm64) for both tools, master track: `dpkg-buildpackage -us -uc -b`; `dpkg -I` shows the expected name/version/Depends; `dpkg -i` in a fresh container, `openFPGALoader --list-cables`, `openocd -v`.
- [ ] Commit(s): `packaging: librp1jtag/piolib build helper`, `packaging: debian metadata templates`, `fpgatools: debianize`.

### Task 7: Static build scripts

**Files:**
- Create: `packaging/static/common.sh`, `packaging/static/openfpgaloader.sh`, `packaging/static/openocd.sh`

- [ ] `common.sh`: `apk add` list; libgpiod from kernel.org (`2.2.3` if `uname -m` is aarch64 else `1.6.5`), `--enable-static --disable-shared`; piolib + librp1jtag via `packaging/build-rp1jtag.sh`; the "remove `.so` so the linker picks `.a`" step.
- [ ] `openfpgaloader.sh <src> <version> <out>`: cmake as spec with `-DCMAKE_EXE_LINKER_FLAGS=-static -DBUILD_STATIC=ON -DOPENFPGALOADER_VERSION=v<version>`; `file` must say statically linked; copy to `<out>/openFPGALoader-<version>-linux-<arch>` + `.sha256`.
- [ ] `openocd.sh <src> <version> <out>`: `./bootstrap` (with the jimtcl submodule: `git submodule update --init jimtcl` is impossible on a shallow FETCH_HEAD tree unless `.gitmodules` URL is cloned manually → clone `https://github.com/msteveb/jimtcl` at the recorded submodule commit `git ls-tree HEAD jimtcl`), `./configure --enable-internal-jimtcl --disable-shared LDFLAGS=-static` + adapters from the spec; `make`; `make install DESTDIR=stage prefix=/`; tarball `openocd-<version>-linux-<arch>.tar.gz` of `bin/openocd` + `share/openocd/scripts` + a `README.txt` explaining `OPENOCD_SCRIPTS`.
- [ ] `shellcheck packaging/static/*.sh packaging/build-rp1jtag.sh` clean.
- [ ] Local run in `alpine` (arm64) for both tools via sudo docker; record durations.
- [ ] Commit: `packaging: Alpine static build scripts`.

### Task 8: Workflows

**Files:**
- Create: `.github/workflows/ci.yml`, `debs.yml`, `static.yml`, `update-upstream.yml`, `fpgatools/bump.py`, `tests/test_bump.py`, `packaging/apt-index.html`, `packaging/release.py`

- [ ] `ci.yml` (push, pull_request): jobs `python` (uv sync, ruff, pytest, shellcheck), `apply` (matrix tool×track: `uv run fpgatools apply`, `compare`), `compile-amd64` (matrix tool×track in `debian:trixie` container: openFPGALoader without RP1_PIO, OpenOCD without rp1-pio-jtag; `--Version`/`-v` output shown).
- [ ] `debs.yml`: as spec section 8; container script = `packaging/build-deb.sh <tool> <track>` (checked-in, shellcheck'd) so the workflow YAML stays small; artifact `debs-<suite>-<arch>-<tool>-<track>`; publish job main-only calling `mithro/apt-repo-action/.github/workflows/publish-apt.yml@main` (permissions scoped on the job).
- [ ] `static.yml`: matrix tool×track×arch; `packaging/static/<tool>.sh` inside `alpine` / `arm32v7/alpine` / `arm32v6/alpine`; upload artifacts; `release` job (main-only, `contents: write`): download all, `uv run python packaging/release.py --series $(git describe --tags --abbrev=0 --match 'v[0-9]*')` which creates the prerelease if missing, uploads every asset (`--clobber` only for `latest.json`), regenerates `latest.json` from the assets present.
- [ ] `update-upstream.yml` (`schedule: cron '17 4 * * 1'`, `workflow_dispatch`; permissions `contents: write, pull-requests: write`): `uv run fpgatools bump --report tmp/bump.md`; if `git diff --quiet upstreams.toml` exit; else branch `bump/<date>`, commit, push, `gh pr create --body-file tmp/bump.md`. Body includes the apply report per series and the GITHUB_TOKEN-does-not-trigger-CI note.
- [ ] `bump.py`: `bump(pins_path) -> BumpReport` using `git clone --bare --filter=blob:none` into `build/meta/<name>.git`: master head via `git rev-parse origin/HEAD` (after `git remote set-head origin -a`), describe with `--match 'v[0-9]*'`, date via `git log -1 --format=%cd --date=short`; newest stable = max non-rc `v*` tag by `sort -V`; rewrite the TOML by regex on the `[tool.track]` blocks (tomllib has no writer; the file layout is fixed by Task 1, keep it line-oriented); then `patchset.apply` each series capturing `PatchConflict`. Tests: TOML rewrite on a fixture string; tag selection (`v1.1.1` > `v1.0.0`, `v2.0.0-rc1` excluded).
- [ ] `packaging/apt-index.html`: install instructions per suite (keyring URL, sources line), links to Releases, the version scheme in one paragraph.
- [ ] Commit per workflow.

### Task 9: README and docs

- [ ] README: what/why, patch table with upstream status links (PR #643 for NeTV2), install (apt lines per suite; static download + `OPENOCD_SCRIPTS`), version scheme with the two examples, "updating patches" how-to, "how upstream tracking works", licence note, blocked-on-Tim list mirrored from the spec.
- [ ] Commit: `docs: README`.

### Task 10: PR, CI, hardware check, review

- [ ] Push branch, `gh pr create` (title `Initial implementation: patch series, packaging, workflows`), watch `gh pr checks` until green; fix and iterate.
- [ ] Download the arm64 static artifacts from the PR run; `scp` to `tim@10.1.10.14:/tmp/`; run `sudo ./openFPGALoader -c rp1pio --pins 27:22:4:17 --detect` and `--read-dna`; `sudo OPENOCD_SCRIPTS=... ./openocd -f board/netv2-rpi.cfg -c init -c 'xc7_read_dna xc7.tap' -c shutdown`; DNA values must match. Record the output in the PR description.
- [ ] Dispatch a fresh reviewer agent with: the spec, the patch directories, the workflows, and the goals (`must`: every patch applies to both tracks, versions follow the scheme, no upstream functionality changed beyond the listed patches, workflows least-privilege). Fix findings before declaring ready.
- [ ] Update memory files (new project memory) and report to Tim with the blocked items.
