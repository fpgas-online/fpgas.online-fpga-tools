# shellcheck shell=sh
# Shared steps for the static (musl) builds. Sourced by openfpgaloader.sh and
# openocd.sh INSIDE an Alpine container: alpine (aarch64), arm32v7/alpine
# (armv7) or arm32v6/alpine (armv6). Everything is linked statically so the
# resulting executable runs on any Linux of the same architecture, including
# Raspberry Pi OS releases too old for the Debian packages.
#
# Expects: REPO (repository root, default /work), and the fetched sources
# under $REPO/build/src/ (fpgatools fetch/apply already ran on the host or
# earlier in the job).

REPO=${REPO:-/work}
OUT=${OUT:-$REPO/built-static}
PREFIX=${PREFIX:-/usr}

static_arch() {
	# The name used in release asset filenames: the architecture of the
	# Alpine userland being built for. Not `uname -m`: in arm32v7/arm32v6
	# containers on an arm64 host it reports aarch64 (seen on the CI runners
	# and on a Cortex-A72 box). Alpine calls its ARMv6 hard-float port armhf.
	arch=$(apk --print-arch)
	case $arch in
	aarch64) echo arm64 ;;
	armv7) echo armv7 ;;
	armhf) echo armv6 ;;
	x86_64) echo amd64 ;;
	*) echo "ERROR: no asset name for Alpine arch $arch" >&2; exit 1 ;;
	esac
}

static_apk_deps() {
	apk add --no-cache \
		build-base cmake git python3 pkgconf curl file \
		libftdi1-dev libftdi1-static libusb-dev eudev-dev \
		zlib-dev zlib-static hidapi-dev linux-headers argp-standalone \
		autoconf automake libtool texinfo
	# The trees under build/src were fetched on the host by another uid;
	# git inside the container would otherwise refuse them ("dubious
	# ownership") and fetch-jimtcl.sh could not read the submodule commit.
	git config --global --add safe.directory '*'
}

# libgpiod from source, static only. Alpine ships no static libgpiod.
# libgpiod 2.x needs the v2 GPIO chardev ABI (Linux 5.10+); the 32-bit
# builds target older Raspberry Pi OS kernels, so they get the 1.x series.
static_build_libgpiod() {
	case $(uname -m) in
	aarch64 | x86_64) ver=2.2.3 ;;
	*) ver=1.6.5 ;;
	esac
	url="https://www.kernel.org/pub/software/libs/libgpiod/libgpiod-$ver.tar.gz"
	echo "==> libgpiod $ver (static) from $url"
	mkdir -p /tmp/libgpiod && cd /tmp/libgpiod || exit 1
	curl -fsSL "$url" | tar xz
	cd "libgpiod-$ver" || exit 1
	./configure --prefix="$PREFIX" --enable-static --disable-shared \
		--disable-tools --disable-tests \
		--disable-bindings-cxx --disable-bindings-python
	make -j"$(nproc)"
	make install
	cd "$REPO" || exit 1
}

# PIOLib and librp1jtag, static, from the pinned sources.
static_build_rp1jtag() {
	cp -a "$REPO/build/src/piolib" /tmp/piolib
	cp -a "$REPO/build/src/rp1jtag" /tmp/rp1jtag
	rm -rf /tmp/piolib/piolib/build /tmp/rp1jtag/build
	"$REPO/packaging/build-rp1jtag.sh" /tmp/piolib /tmp/rp1jtag "$PREFIX"
}

# Make the linker pick static archives: remove the shared objects of the
# libraries we link, and libftdi1's CMake config, which hardcodes the
# libusb .so path (openFPGALoader then falls back to pkg-config).
static_force_static_libs() {
	rm -rf "$PREFIX/lib/cmake/libftdi1"
	for lib in libftdi1 libusb-1.0 libudev libhidapi-hidraw libhidapi-libusb \
			librp1jtag libpio libgpiod libgpiodcxx; do
		rm -f "$PREFIX"/lib/"$lib".so "$PREFIX"/lib/"$lib".so.*
	done
}

static_assert_static() {
	file "$1" | tee /tmp/file.txt
	grep -qE "statically linked|static-pie linked" /tmp/file.txt \
		|| { echo "ERROR: $1 is not statically linked" >&2; exit 1; }
	# The asset is named by static_arch; the binary must be that architecture.
	case $(static_arch) in
	arm64) elf="ELF 64-bit LSB.*ARM aarch64" ;;
	armv7 | armv6) elf="ELF 32-bit LSB.*ARM" ;;
	amd64) elf="ELF 64-bit LSB.*x86-64" ;;
	esac
	grep -qE "$elf" /tmp/file.txt \
		|| { echo "ERROR: $1 is not a $(static_arch) binary" >&2; exit 1; }
}

static_sha256() {
	(cd "$(dirname "$1")" && sha256sum "$(basename "$1")" > "$(basename "$1").sha256")
}
