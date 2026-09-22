#!/bin/sh
# Build one tool/track as a Debian package. Runs INSIDE a debian:<suite>
# container (or any Debian host) with the repository mounted at $REPO
# (default /work) and writes the .deb files to $OUT (default $REPO/built-debs).
#
#   packaging/build-deb.sh <tool> <track>
#
# Steps: install the toolchain, fetch the pinned upstreams and apply the
# patch series (fpgatools), build librp1jtag+piolib statically, render
# debian/ (fpgatools debianize), dpkg-buildpackage, then install the result
# and run the binary once so a broken package fails here and not on a Pi.
set -eu

tool=$1
track=$2
REPO=${REPO:-/work}
OUT=${OUT:-$REPO/built-debs}
RP1JTAG_PREFIX=${RP1JTAG_PREFIX:-/usr/local}
export DEBIAN_FRONTEND=noninteractive

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

fpgatools fetch piolib
fpgatools fetch rp1jtag
fpgatools apply "$tool" "$track"
src=$REPO/build/src/$tool-$track
echo "==> patched tree at $src"

packaging/build-rp1jtag.sh build/src/piolib build/src/rp1jtag "$RP1JTAG_PREFIX"
[ "$tool" = openocd ] && packaging/fetch-jimtcl.sh "$src"

fpgatools debianize "$tool" "$track"

# Build-Depends from the rendered control file, nothing duplicated here.
cd "$src"
apt-get build-dep -y -q ./
RP1JTAG_PREFIX=$RP1JTAG_PREFIX FPGATOOLS_REPO=$REPO dpkg-buildpackage -us -uc -b

mkdir -p "$OUT"
cp ../*.deb "$OUT/"
ls -l "$OUT"

# Install what was just built and run it: the package must be usable on a
# clean system, with only Debian's own libraries alongside.
apt-get install -y -q ../*.deb
case $tool in
openfpgaloader)
	openFPGALoader --Version
	openFPGALoader --list-cables | grep -E 'rp1pio|libgpiod|tt_micropython'
	;;
openocd)
	openocd -v
	test -f /usr/share/openocd/scripts/board/netv2-rpi.cfg
	;;
esac
echo "==> $tool/$track $version OK"
