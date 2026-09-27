#!/bin/sh
# Build one tool/track as a Debian package. Runs INSIDE a debian:<suite>
# container (or any Debian host) with the repository mounted at $REPO
# (default /work) and writes the .deb files to $OUT (default $REPO/built-debs).
#
#   SUITE=<suite> [LIBS_DIR=<dir>] [DEB_VERSION=<v>] [INSTALL_TEST=no] \
#     packaging/build-deb.sh <tool> <track>
#
# Steps: install the shared librp1jtag0/librp1jtag-dev (and libpio) the
# package builds against and depends on, from LIBS_DIR when build-libs.sh
# already made them for this suite and architecture (deb.yml) or by running
# build-libs.sh first (a one-off build); fetch the pinned upstream and
# apply the patch series (fpgatools); render debian/ (fpgatools debianize,
# with DEB_VERSION: the shared deb-version.py's, which CI passes);
# dpkg-buildpackage; then install-test.sh, so a broken package fails here
# and not on a Pi. CI sets INSTALL_TEST=no and runs install-test.sh in a
# clean container of the suite instead (build-debs.yml's Install test).
set -eu

tool=$1
track=$2
REPO=${REPO:-/work}
OUT=${OUT:-$REPO/built-debs}
SUITE=${SUITE:?set SUITE to the Debian suite being built for}
LIBS_DIR=${LIBS_DIR:-}
DEB_VERSION=${DEB_VERSION:-}
INSTALL_TEST=${INSTALL_TEST:-yes}
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

version=${DEB_VERSION:-$(fpgatools version "$tool" "$track")}
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

fpgatools debianize "$tool" "$track" ${DEB_VERSION:+--version "$DEB_VERSION"}

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

if [ "$INSTALL_TEST" = no ]; then
	echo "==> $tool/$track $version built (install test left to the caller)"
	exit 0
fi
# The libraries are already installed, from LIBS_DIR or by build-libs.sh.
cd "$REPO"
DEBS_DIR=$OUT LIBS_DIR='' packaging/install-test.sh "$tool" "$track"
echo "==> $tool/$track $version OK"
