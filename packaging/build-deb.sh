#!/bin/sh
# Build one tool/track as a Debian package. Runs INSIDE a debian:<suite>
# container (or any Debian host) with the repository mounted at $REPO
# (default /work) and writes the .deb files to $OUT (default $REPO/built-debs).
#
#   SUITE=<suite> [LIBS_DIR=<dir>] packaging/build-deb.sh <tool> <track>
#
# Steps: install the shared librp1jtag0/librp1jtag-dev (and libpio) the
# package builds against and depends on, from LIBS_DIR when build-libs.sh
# already made them for this suite and architecture (debs.yml) or by running
# build-libs.sh first (a one-off or CI build); fetch the pinned upstream and
# apply the patch series (fpgatools); render debian/ (fpgatools debianize);
# dpkg-buildpackage; then install the result and run the binary once so a
# broken package fails here and not on a Pi.
set -eu

tool=$1
track=$2
REPO=${REPO:-/work}
OUT=${OUT:-$REPO/built-debs}
SUITE=${SUITE:?set SUITE to the Debian suite being built for}
LIBS_DIR=${LIBS_DIR:-}
export DEBIAN_FRONTEND=noninteractive
export REPO SUITE
. "$REPO/packaging/libpio.sh"

apt-get update -q
apt-get install -y -q --no-install-recommends \
	build-essential devscripts debhelper equivs fakeroot \
	cmake git pkg-config python3 ca-certificates

# The container runs as root over a bind mount owned by the runner user.
git config --global --add safe.directory '*'
git config --global user.name "fpgas.online build"
git config --global user.email "builds@fpgas.online"

cd "$REPO"
fpgatools() { PYTHONPATH="$REPO" python3 -m fpgatools "$@"; }

version=$(fpgatools version "$tool" "$track")
echo "==> $tool/$track $version"

if [ -n "$LIBS_DIR" ]; then
	ls "$LIBS_DIR"/librp1jtag-dev_*.deb > /dev/null \
		|| { echo "build-deb: no librp1jtag-dev in LIBS_DIR=$LIBS_DIR" >&2; exit 1; }
	libpio_from_rpi "$SUITE" && rpi_archive_add "$SUITE"
	apt-get install -y -q "$LIBS_DIR"/*.deb
else
	packaging/build-libs.sh
fi

fpgatools apply "$tool" "$track"
src=$REPO/build/src/$tool-$track
echo "==> patched tree at $src"

[ "$tool" = openocd ] && packaging/fetch-jimtcl.sh "$src"

fpgatools debianize "$tool" "$track"

# Build-Depends from the rendered control file, nothing duplicated here.
cd "$src"
apt-get build-dep -y -q ./
# The source and binary package share one name (debian/changelog has it).
# dpkg-buildpackage writes into build/src/, which every tool/track shares,
# so only this package's files are cleared, copied and installed.
pkg=$(dpkg-parsechangelog -S Source)
rm -f ../"${pkg}"_*.deb ../"${pkg}"-dbgsym_*.deb
FPGATOOLS_REPO=$REPO dpkg-buildpackage -us -uc -b

mkdir -p "$OUT"
cp ../"${pkg}"_*.deb ../"${pkg}"-dbgsym_*.deb "$OUT/"
ls -l "$OUT"

# Install what was just built and run it: the package must be usable with
# only Debian's libraries, librp1jtag0 and libpio0 alongside.
apt-get install -y -q ../"${pkg}"_*.deb
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
ldd "$bin" | tee "$OUT/ldd-$tool-$track.txt"
grep -q 'librp1jtag\.so\.0 => /' "$OUT/ldd-$tool-$track.txt" \
	|| { echo "build-deb: $bin does not load the shared librp1jtag.so.0" >&2; exit 1; }
rm "$OUT/ldd-$tool-$track.txt"
dpkg-query -W -f '${Depends}\n' "$pkg" | grep -q 'librp1jtag0 (>= ' \
	|| { echo "build-deb: $pkg does not depend on librp1jtag0" >&2; exit 1; }
echo "==> $tool/$track $version OK"
