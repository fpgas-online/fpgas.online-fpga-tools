#!/bin/sh
# Run the iCE40 SPI slave load models against a built, patched openFPGALoader tree.
#
#   packaging/tests/ice40-slave-model.sh <src>
#
# <src> is a tree that packaging/compile-check.sh has built (in <src>/build,
# with -DENABLE_LIBGPIOD=ON and the iCE40 support). Each model is compiled
# with the tree's own flags and linked to its objects in place of main.cpp,
# into <src>/build/<model>, and run. No GPIO is touched.
#
#   ice40-slave-model.cpp    Ice40::program_cram_slave() against a model of
#                            an iCE40 and its lines (any backend)
#   ice40-gpiomem-model.cpp  the /dev/gpiomem backend against a model of a
#                            Raspberry Pi's GPIO registers, the iCE40 and
#                            its flash; the backend selection
set -eu

src=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
build=$src/build
dir=$build/CMakeFiles/openFPGALoader.dir

fail() { echo "ice40-slave-model: $*" >&2; exit 1; }
[ -f "$dir/flags.make" ] || fail "$build is not a built tree (no $dir/flags.make)"
[ -f "$dir/src/ice40.cpp.o" ] || fail "$build has no ice40.cpp.o (iCE40 support off?)"
grep -q 'CMakeFiles/openFPGALoader.dir/src/main.cpp.o' "$dir/link.txt" \
	|| fail "no main.cpp.o in $dir/link.txt"

flag() { sed -n "s/^$1 = //p" "$dir/flags.make"; }
cd "$build"
for model in ice40-slave-model ice40-gpiomem-model; do
	[ -f "$here/$model.cpp" ] || fail "no $here/$model.cpp"
	echo "ice40-slave-model: $model"
	eval c++ "$(flag CXX_FLAGS)" "$(flag CXX_DEFINES)" "$(flag CXX_INCLUDES)" \
		-I"$src/src" -c "$here/$model.cpp" -o "$model.o"
	link=$(sed -e "s#CMakeFiles/openFPGALoader.dir/src/main.cpp.o#$model.o#" \
		-e "s#-o openFPGALoader #-o $model #" "$dir/link.txt")
	sh -c "$link"
	"./$model"
done
