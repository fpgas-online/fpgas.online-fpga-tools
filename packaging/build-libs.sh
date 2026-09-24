#!/bin/sh
# Build the shared-library packages the Debian tool packages depend on, and
# install them. Runs INSIDE a debian:<suite> container (or any Debian host)
# with the repository mounted at $REPO (default /work); writes the .deb
# files to $LIBS_OUT (default $REPO/built-libs).
#
#   SUITE=<suite> packaging/build-libs.sh
#
# librp1jtag0 / librp1jtag-dev always. libpio0 / libpio-dev only where
# Raspberry Pi's archive does not provide them (see packaging/libpio.sh);
# elsewhere Raspberry Pi's libpio-dev is installed to build against.
set -eu

REPO=${REPO:-/work}
LIBS_OUT=${LIBS_OUT:-$REPO/built-libs}
SUITE=${SUITE:?set SUITE to the Debian suite being built for}
export DEBIAN_FRONTEND=noninteractive
. "$REPO/packaging/libpio.sh"

apt-get update -q
apt-get install -y -q --no-install-recommends \
	build-essential devscripts debhelper equivs fakeroot binutils \
	cmake git pkg-config python3 ca-certificates

# The container runs as root over a bind mount owned by the runner user.
git config --global --add safe.directory '*'

cd "$REPO"
fpgatools() { PYTHONPATH="$REPO" python3 -m fpgatools "$@"; }
fpgatools fetch piolib
fpgatools fetch rp1jtag

# Packages are built in a copy of the fetched tree under build/pkg/: the
# fetch stays pristine (rp1-jtag ships a debian/ of its own, which the
# rendered one replaces) and no stale cmake build/ directory leaks in.
pkgroot=$REPO/build/pkg
build_lib() { # build_lib <rp1jtag|piolib>
	tree=$pkgroot/$1
	rm -rf "$tree"
	mkdir -p "$pkgroot"
	cp -a "$REPO/build/src/$1" "$tree"
	rm -rf "$tree/.git" "$tree/build" "$tree/piolib/build"
	fpgatools debianize "$1" --dest "$tree"
	(cd "$tree" && apt-get build-dep -y -q ./ && dpkg-buildpackage -us -uc -b)
}

# Nothing from an earlier run may be picked up by the globs below.
rm -rf "$pkgroot" "$LIBS_OUT"
mkdir -p "$LIBS_OUT"
if libpio_from_rpi "$SUITE"; then
	echo "==> libpio: Raspberry Pi's archive ($SUITE)"
	rpi_archive_add "$SUITE"
	apt-get install -y -q libpio-dev
else
	echo "==> libpio: built here (Raspberry Pi's archive has none for $SUITE $(dpkg --print-architecture))"
	build_lib piolib
	cp "$pkgroot"/libpio0_*.deb "$pkgroot"/libpio-dev_*.deb "$LIBS_OUT/"
	apt-get install -y -q "$LIBS_OUT"/libpio0_*.deb "$LIBS_OUT"/libpio-dev_*.deb
fi
dpkg-query -W libpio0 libpio-dev

build_lib rp1jtag
cp "$pkgroot"/librp1jtag0_*.deb "$pkgroot"/librp1jtag-dev_*.deb "$LIBS_OUT/"
apt-get install -y -q "$LIBS_OUT"/librp1jtag0_*.deb "$LIBS_OUT"/librp1jtag-dev_*.deb
pkg-config --modversion rp1jtag
ls -l "$LIBS_OUT"
echo "==> libraries for $SUITE $(dpkg --print-architecture) OK"
