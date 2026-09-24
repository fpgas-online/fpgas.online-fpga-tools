"""Tooling for the fpgas.online patched openFPGALoader / OpenOCD builds.

Everything here is stdlib-only so it runs unchanged inside the minimal
Debian and Alpine build containers as well as on a developer machine.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TOOLS = ("openfpgaloader", "openocd")
TRACKS = ("stable", "master")
# The libraries packaged as shared Debian libraries alongside the tools. Each
# has one pin (no tracks) in upstreams.toml.
LIBS = ("rp1jtag", "piolib")
