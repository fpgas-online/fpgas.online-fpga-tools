#!/bin/sh
# Static OpenOCD for one architecture, inside an Alpine container.
#
#   packaging/static/openocd.sh <track> <version> <local-version>
#
# <version> is the package version (fpgatools version openocd <track>) and
# <local-version> the suffix guess-rev.sh must report (fpgatools version
# openocd <track> --tool-string). OpenOCD is useless without its Tcl tree,
# so the result is a tarball $OUT/openocd-<version>-linux-<arch>.tar.gz of
# bin/openocd + share/openocd/scripts + a README, with a .sha256 beside it.
set -eu
track=$1
version=$2
local_version=$3
# shellcheck source=packaging/static/common.sh
. "$(dirname "$0")/common.sh"

static_apk_deps
static_build_libgpiod
static_build_rp1jtag
static_force_static_libs

src=$REPO/build/src/openocd-$track
[ -f "$src/configure.ac" ] || { echo "ERROR: $src is not a patched tree (run fpgatools apply)" >&2; exit 1; }

# The bundled jimtcl at the commit the tree records (a fetch by commit has
# no submodule contents; Alpine has no static libjim either).
"$REPO/packaging/fetch-jimtcl.sh" "$src"

export OPENOCD_LOCAL_VERSION="$local_version"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
cd "$src"
[ -f Makefile ] && make distclean
./bootstrap nosubmodule
# No static libjaylink on Alpine and no submodule contents in a fetch by
# commit: the static build has no J-Link support.
gpiodflag=$("$REPO/packaging/linuxgpiod-flag.sh" "$src")
./configure --prefix=/usr \
	--enable-internal-jimtcl --disable-internal-libjaylink --disable-shared --enable-static \
	--enable-rp1-pio-jtag --enable-bcm2835gpio "$gpiodflag" --enable-sysfsgpio \
	--enable-remote-bitbang \
	--disable-doxygen-html --disable-doxygen-pdf --disable-werror \
	LDFLAGS="-static"
make -j"$(nproc)"

bin=$src/src/openocd
static_assert_static "$bin"
"$bin" -v 2>&1 | grep -q -- "$local_version" || { echo "ERROR: -v does not report $local_version" >&2; exit 1; }
drivers="rp1_pio_jtag bcm2835gpio"
[ "$gpiodflag" = --enable-linuxgpiod ] && drivers="$drivers linuxgpiod"
for drv in $drivers; do
	if "$bin" -c "adapter driver $drv" -c shutdown 2>&1 | grep -qi invalid; then
		echo "ERROR: adapter driver $drv missing" >&2; exit 1
	fi
done

arch=$(static_arch)
name=openocd-$version-linux-$arch
stage=/tmp/stage/$name
rm -rf /tmp/stage && mkdir -p "$stage/bin" "$stage/share/openocd"
cp "$bin" "$stage/bin/openocd"
strip "$stage/bin/openocd"
cp -a "$src/tcl" "$stage/share/openocd/scripts"
cp "$src/contrib/60-openocd.rules" "$stage/share/openocd/"
cat > "$stage/README.txt" <<EOF
OpenOCD $version (fpgas.online build, $track track, static $arch)

bin/openocd looks for its Tcl configuration tree at /usr/share/openocd/scripts.
Run it from anywhere with either

    OPENOCD_SCRIPTS=\$PWD/share/openocd/scripts bin/openocd -f board/netv2-rpi.cfg ...
or
    bin/openocd -s \$PWD/share/openocd/scripts -f board/netv2-rpi.cfg ...

share/openocd/60-openocd.rules are the udev rules for USB adapters.
Source and patch series: https://github.com/fpgas-online/fpgas.online-fpga-tools
EOF

mkdir -p "$OUT"
tar -C /tmp/stage -czf "$OUT/$name.tar.gz" "$name"
static_sha256 "$OUT/$name.tar.gz"
ls -l "$OUT"
echo "==> openocd/$track $version static $arch OK"
