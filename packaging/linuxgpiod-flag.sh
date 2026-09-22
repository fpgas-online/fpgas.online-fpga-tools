#!/bin/sh
# Print the OpenOCD configure flag for the linuxgpiod adapter:
# --enable-linuxgpiod when this tree can be built against the installed
# libgpiod, --disable-linuxgpiod otherwise.
#
#   packaging/linuxgpiod-flag.sh <openocd-src>
#
# The 0.12.0 release only knows the libgpiod 1.x API; its configure accepts
# any libgpiod and the build then fails against 2.x (Debian trixie and sid,
# Alpine 3.21 on 64-bit). Upstream master handles both. Whether a tree
# supports v2 is read from the source itself (a tree that can build against
# either API carries the HAVE_LIBGPIOD_V1 switch), so no per-release table
# is needed.
set -eu
src=$1
drv=$src/src/jtag/drivers/linuxgpiod.c
if [ ! -f "$drv" ]; then
	echo --disable-linuxgpiod
	exit 0
fi
if ! pkg-config --exists libgpiod; then
	echo --disable-linuxgpiod
	exit 0
fi
if pkg-config --max-version 1.99 libgpiod; then
	echo --enable-linuxgpiod
elif grep -q 'HAVE_LIBGPIOD_V1' "$drv"; then
	echo --enable-linuxgpiod
else
	echo "linuxgpiod-flag: libgpiod $(pkg-config --modversion libgpiod) is too new for this OpenOCD; leaving linuxgpiod out" >&2
	echo --disable-linuxgpiod
fi
