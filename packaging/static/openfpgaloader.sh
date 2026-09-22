#!/bin/sh
# Static openFPGALoader for one architecture, inside an Alpine container.
#
#   packaging/static/openfpgaloader.sh <track> <version>
#
# <version> is the package version (fpgatools version openfpgaloader <track>);
# the binary reports "v<version>" and is written to
# $OUT/openFPGALoader-<version>-linux-<arch> with a .sha256 beside it.
set -eu
track=$1
version=$2
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

arch=$(static_arch)
mkdir -p "$OUT"
out=$OUT/openFPGALoader-$version-linux-$arch
cp "$bin" "$out"
strip "$out"
static_sha256 "$out"
ls -l "$OUT"
echo "==> openfpgaloader/$track $version static $arch OK"
