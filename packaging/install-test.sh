#!/bin/sh
# Install one tool/track's package and run it: the package must be usable
# with only the suite's libraries, librp1jtag0 and libpio0 alongside. Runs
# INSIDE a container of the suite (or any Debian host) with the repository
# mounted at $REPO (default /work).
#
#   SUITE=<suite> DEBS_DIR=<dir> [LIBS_DIR=<dir>] packaging/install-test.sh <tool> <track>
#
# build-debs.yml's Install test step runs it in a clean container of the
# suite: LIBS_DIR holds that suite and architecture's librp1jtag0 (and
# libpio0 where Raspberry Pi's archive has none), DEBS_DIR the tool's
# package. build-deb.sh runs it too, at the end of a local build, where the
# libraries are already installed (no LIBS_DIR).
set -eu

tool=$1
track=$2
REPO=${REPO:-/work}
SUITE=${SUITE:?set SUITE to the suite being tested}
DEBS_DIR=${DEBS_DIR:?set DEBS_DIR to the directory holding the built package}
LIBS_DIR=${LIBS_DIR:-}
export DEBIAN_FRONTEND=noninteractive
. "$REPO/packaging/libpio.sh"

case $tool in
openfpgaloader) pkg=openfpgaloader-fpgasonline ;;
openocd) pkg=openocd-fpgasonline ;;
*) echo "install-test: unknown tool $tool" >&2; exit 1 ;;
esac
if [ "$track" = master ]; then pkg=$pkg-git; fi

if [ -n "$LIBS_DIR" ]; then
	ls "$LIBS_DIR"/librp1jtag0_*.deb > /dev/null \
		|| { echo "install-test: no librp1jtag0 in LIBS_DIR=$LIBS_DIR" >&2; exit 1; }
	if libpio_from_rpi "$SUITE"; then
		rpi_archive_add "$SUITE"
	else
		apt-get update -q
	fi
	# The runtime libraries only: the -dev packages are for building.
	set -- "$LIBS_DIR"/librp1jtag0_*.deb
	for f in "$LIBS_DIR"/libpio0_*.deb; do
		if [ -e "$f" ]; then set -- "$@" "$f"; fi
	done
	apt-get install -y -q "$@"
fi
# The package's other dependencies come from the suite's archive (and
# libpio0 from Raspberry Pi's, where that is where it comes from).
apt-get install -y -q "$DEBS_DIR/${pkg}"_*.deb
dpkg-query -W "$pkg" librp1jtag0 libpio0

case $tool in
openfpgaloader)
	bin=$(command -v openFPGALoader)
	openFPGALoader --Version
	openFPGALoader --list-cables | grep -E 'rp1pio|libgpiod|tt_micropython'
	;;
openocd)
	bin=$(command -v openocd)
	openocd -v
	test -f /usr/share/openocd/scripts/board/netv2-rpi.cfg
	;;
esac
# The rp1pio driver must reach librp1jtag through the shared library the
# package depends on, not a copy linked in.
libs=$(ldd "$bin")
echo "$libs"
echo "$libs" | grep -q 'librp1jtag\.so\.0 => /' \
	|| { echo "install-test: $bin does not load the shared librp1jtag.so.0" >&2; exit 1; }
dpkg-query -W -f '${Depends}\n' "$pkg" | grep -q 'librp1jtag0 (>= ' \
	|| { echo "install-test: $pkg does not depend on librp1jtag0" >&2; exit 1; }
echo "==> install test $tool/$track ($SUITE $(dpkg --print-architecture)): OK"
