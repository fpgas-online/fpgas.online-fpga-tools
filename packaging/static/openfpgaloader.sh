#!/bin/sh
# Static openFPGALoader for one architecture, inside an Alpine container.
#
#   packaging/static/openfpgaloader.sh <track> <version>
#
# <version> is the package version (fpgatools version openfpgaloader <track>);
# the binary reports "v<version>". The result is a tarball
# $OUT/openFPGALoader-<version>-linux-<arch>.tar.gz containing
# bin/openFPGALoader and share/openFPGALoader/ (the spiOverJtag/bpiOverJtag
# bridge bitstreams), with a .sha256 beside it.
#
# The bridges are runtime data, not compiled in: every SPI-flash access on a
# JTAG-attached FPGA (including --detect -f and --flash-info) loads one, and
# a bare executable fails with "missing device-package information". The
# binary looks in the compiled-in /usr/share/openFPGALoader unless
# OPENFPGALOADER_SOJ_DIR points elsewhere (upstream behaviour), so the
# README in the tarball says to set that to the unpacked share/openFPGALoader.
set -eu
track=$1
version=$2
# shellcheck source=packaging/static/common.sh
. "$(dirname "$0")/common.sh"

static_apk_deps
static_build_libgpiod
static_build_rp1jtag
static_force_static_libs

src=$REPO/build/src/openfpgaloader-$track
[ -f "$src/CMakeLists.txt" ] || { echo "ERROR: $src is not a patched tree (run fpgatools apply)" >&2; exit 1; }
rm -rf "$src/build-static"
cmake -S "$src" -B "$src/build-static" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX=/usr \
	-DCMAKE_PREFIX_PATH="$PREFIX" \
	-DBUILD_STATIC=ON \
	-DCMAKE_EXE_LINKER_FLAGS="-static" \
	-DENABLE_CABLE_ALL=ON \
	-DENABLE_VENDORS_ALL=ON \
	-DENABLE_LIBGPIOD=ON \
	-DENABLE_UDEV=ON \
	-DENABLE_USB_SCAN=ON \
	-DENABLE_TT_MICROPYTHON=ON \
	-DENABLE_RP1_PIO=ON \
	-DOPENFPGALOADER_VERSION="v$version"
cmake --build "$src/build-static" --parallel

bin=$src/build-static/openFPGALoader
static_assert_static "$bin"
"$bin" --Version | grep -q -- "v$version" || { echo "ERROR: wrong --Version" >&2; exit 1; }
"$bin" --list-cables > /tmp/cables.txt
for cable in rp1pio libgpiod tt_micropython; do
	grep -qw "$cable" /tmp/cables.txt || { echo "ERROR: cable $cable missing" >&2; exit 1; }
done

# Stage with upstream's own install rules (bin/ + share/openFPGALoader/*.gz),
# so the data directory is exactly what a normal install would have.
arch=$(static_arch)
name=openFPGALoader-$version-linux-$arch
stage=/tmp/stage/$name
rm -rf /tmp/stage
DESTDIR=$stage cmake --install "$src/build-static" --prefix /
strip "$stage/bin/openFPGALoader"
n=$(find "$stage/share/openFPGALoader" -name 'spiOverJtag_*.gz' | wc -l)
[ "$n" -gt 50 ] || { echo "ERROR: only $n spiOverJtag bitstreams staged" >&2; exit 1; }
[ -f "$stage/share/openFPGALoader/spiOverJtag_xc7a100tfgg484.bit.gz" ] \
	|| { echo "ERROR: NeTV2 bridge spiOverJtag_xc7a100tfgg484.bit.gz missing" >&2; exit 1; }
cat > "$stage/README.txt" <<EOF
openFPGALoader $version (fpgas.online build, $track track, static $arch)

bin/openFPGALoader is fully static. SPI-flash operations on a JTAG-attached
FPGA (--detect -f, --flash-info, --write-flash, ...) load a bridge bitstream
from share/openFPGALoader/; the binary looks in /usr/share/openFPGALoader
unless told otherwise, so either install this tree under /usr or run:

    OPENFPGALOADER_SOJ_DIR=\$PWD/share/openFPGALoader bin/openFPGALoader -b netv2_100 --detect -f

Source and patch series: https://github.com/fpgas-online/fpgas.online-fpga-tools
EOF

mkdir -p "$OUT"
tar -C /tmp/stage -czf "$OUT/$name.tar.gz" "$name"
static_sha256 "$OUT/$name.tar.gz"
ls -l "$OUT"
echo "==> openfpgaloader/$track $version static $arch OK ($n bridges)"
