#!/bin/sh
# Configure, build and smoke-test a patched tree, without packaging it.
#
#   packaging/compile-check.sh openfpgaloader <src> <version> [<rp1jtag-prefix>]
#   packaging/compile-check.sh openocd        <src> <version> [<rp1jtag-prefix>]
#
# <version> is the string the binary must report (fpgatools version --tool-string).
# With a <rp1jtag-prefix> (where packaging/build-rp1jtag.sh installed
# librp1jtag) the rp1pio drivers are built and asserted present; without it
# they are left out, which is what the amd64 compile check in CI does.
#
# Runs inside the Debian build containers and on a developer machine alike;
# every "must" below is a hard failure so a silently dropped backend (the
# way upstream drops libgpiod when its headers are missing) cannot ship.
set -eu

tool=$1
src=$2
version=$3
rp1jtag_prefix=${4:-}

fail() { echo "compile-check: $*" >&2; exit 1; }
need() { # need <file> <pattern> <what>
	grep -q -- "$2" "$1" || fail "$3 missing (no '$2' in $1)"
}

case $tool in
openfpgaloader)
	rp1=OFF
	[ -n "$rp1jtag_prefix" ] && rp1=ON
	cmake -S "$src" -B "$src/build" \
		-DCMAKE_BUILD_TYPE=Release \
		-DCMAKE_PREFIX_PATH="$rp1jtag_prefix" \
		-DENABLE_CABLE_ALL=ON \
		-DENABLE_VENDORS_ALL=ON \
		-DENABLE_LIBGPIOD=ON \
		-DENABLE_UDEV=ON \
		-DENABLE_USB_SCAN=ON \
		-DENABLE_TT_MICROPYTHON=ON \
		-DENABLE_RP1_PIO=$rp1 \
		-DOPENFPGALOADER_VERSION="$version"
	cmake --build "$src/build" --parallel
	bin=$src/build/openFPGALoader
	"$bin" --Version | tee "$src/build/version.txt"
	need "$src/build/version.txt" "$version" "version string"
	"$bin" --list-cables > "$src/build/cables.txt"
	need "$src/build/cables.txt" "libgpiod" "libgpiod cable"
	need "$src/build/cables.txt" "tt_micropython" "tt_micropython cable"
	[ "$rp1" = ON ] && need "$src/build/cables.txt" "rp1pio" "rp1pio cable"
	"$bin" --list-boards > "$src/build/boards.txt"
	need "$src/build/boards.txt" "netv2" "netv2 board"
	need "$src/build/boards.txt" "netv2_100" "netv2_100 board"
	need "$src/build/boards.txt" "tt_fpga" "tt_fpga board"
	"$bin" --help | grep -q -- "--flash-info" || fail "--flash-info option missing"
	;;
openocd)
	rp1flag=--disable-rp1-pio-jtag
	[ -n "$rp1jtag_prefix" ] && rp1flag=--enable-rp1-pio-jtag
	export PKG_CONFIG_PATH="${rp1jtag_prefix:+$rp1jtag_prefix/lib/pkgconfig:}${PKG_CONFIG_PATH:-}"
	export OPENOCD_LOCAL_VERSION="$version"
	"$(dirname "$0")/fetch-jimtcl.sh" "$src"
	(cd "$src" && ./bootstrap nosubmodule)
	(cd "$src" && ./configure \
		--enable-bcm2835gpio --enable-linuxgpiod --enable-sysfsgpio \
		"$rp1flag" --disable-werror \
		--enable-internal-jimtcl --disable-internal-libjaylink \
		--disable-doxygen-html --disable-doxygen-pdf)
	make -C "$src" -j"$(nproc)"
	bin=$src/src/openocd
	"$bin" -v 2>&1 | tee "$src/version.txt"
	need "$src/version.txt" "$version" "version string"
	for drv in bcm2835gpio linuxgpiod sysfsgpio; do
		if "$bin" -c "adapter driver $drv" -c shutdown 2>&1 | grep -qi invalid; then
			fail "$drv adapter driver missing"
		fi
	done
	if [ -n "$rp1jtag_prefix" ]; then
		if "$bin" -c "adapter driver rp1_pio_jtag" -c shutdown 2>&1 | grep -qi invalid; then
			fail "rp1_pio_jtag adapter driver missing"
		fi
	fi
	# Every Tcl file the series adds must be there; the proc-only ones must
	# also parse (the board files need hardware to get past their adapter
	# setup, so they are only checked for presence).
	for cfg in board/netv2-rpi.cfg board/netv2-rpi-rp1pio.cfg \
			board/netv2-rpi-bcm2835gpio.cfg board/netv2-rpi-linuxgpiod.cfg \
			board/netv2-rpi-spiflash.cfg interface/raspberrypi-rp1-pio.cfg \
			fpga/xilinx-dna.cfg fpga/lattice-ecp5-traceid.cfg; do
		[ -f "$src/tcl/$cfg" ] || fail "tcl/$cfg missing"
	done
	for cfg in fpga/xilinx-dna.cfg fpga/lattice-ecp5-traceid.cfg; do
		"$bin" -s "$src/tcl" -c "source [find $cfg]" -c shutdown > "$src/tclcheck.txt" 2>&1 \
			|| { cat "$src/tclcheck.txt"; fail "tcl/$cfg does not parse"; }
	done
	;;
*)
	fail "unknown tool '$tool' (openfpgaloader|openocd)"
	;;
esac
echo "compile-check: $tool OK ($version)"
