# fpgas.online-fpga-tools

Patched builds of [openFPGALoader](https://github.com/trabucayre/openFPGALoader)
and [OpenOCD](https://openocd.org/) for the fpgas.online Raspberry Pi images:
a maintained patch series against the latest upstream release and against
upstream git, built as Debian packages (signed apt repository on GitHub Pages)
and as static binaries (GitHub Releases).

Design: `docs/superpowers/specs/2026-09-22-fpga-tools-design.md`.

Part of the [fpgas.online](https://fpgas.online) platform. Tooling is
Apache-2.0; the patches carry their upstream project's licence
(openFPGALoader Apache-2.0, OpenOCD GPL-2.0-or-later).
