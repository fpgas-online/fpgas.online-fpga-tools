# shellcheck shell=sh
# Where the Debian builds get PIOLib (libpio0 / libpio-dev) from. Sourced by
# build-libs.sh and build-deb.sh inside a debian:<suite> container.
#
# Raspberry Pi's archive (archive.raspberrypi.com, source raspi-utils)
# already ships libpio0 and libpio-dev for the suites Raspberry Pi OS is
# built on, and every Pi runs with that archive configured. Where it does,
# librp1jtag0 is built against it and depends on it; everywhere else this
# repository builds and publishes its own libpio0 from the pinned
# raspberrypi/utils (packaging/debian/piolib/).

# Suites and architectures Raspberry Pi's archive publishes libpio0 for,
# checked against its Packages indexes on 2026-09-23 (bookworm
# 20251002-1~bookworm, trixie 20260626-1, arm64 and armhf each).
libpio_from_rpi() { # libpio_from_rpi <suite>
	case "$1/$(dpkg --print-architecture)" in
	bookworm/arm64 | bookworm/armhf | trixie/arm64 | trixie/armhf) return 0 ;;
	*) return 1 ;;
	esac
}

# Add Raspberry Pi's archive for <suite>, trusted through the keyring kept
# in this repository (packaging/keys/, from raspberrypi-archive-keyring
# 2025.1+rpt1: CF8A1AF502A2AA2D763BAE7E82B129927FA3303E signs bookworm and
# trixie). Pinned to priority 100 so the build takes only what Debian itself
# lacks (libpio) from it and keeps every other package Debian's.
rpi_archive_add() { # rpi_archive_add <suite>
	install -D -m 0644 "$REPO/packaging/keys/raspberrypi-archive-keyring.gpg" \
		/etc/apt/keyrings/raspberrypi-archive-keyring.gpg
	echo "deb [signed-by=/etc/apt/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.com/debian $1 main" \
		> /etc/apt/sources.list.d/raspberrypi.list
	printf 'Package: *\nPin: origin archive.raspberrypi.com\nPin-Priority: 100\n' \
		> /etc/apt/preferences.d/raspberrypi
	apt-get update -q
}
