#!/bin/sh
# Run ice40-slave-model.cpp against a built, patched openFPGALoader tree.
#
#   packaging/tests/ice40-slave-model.sh <src>
#
# <src> is a tree that packaging/compile-check.sh has built (in <src>/build,
# with -DENABLE_LIBGPIOD=ON and the iCE40 support). The model is compiled
# with the tree's own flags and linked to its objects in place of main.cpp,
# into <src>/build/ice40-slave-model, and run. No GPIO is touched.
set -eu

src=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
build=$src/build
dir=$build/CMakeFiles/openFPGALoader.dir

fail() { echo "ice40-slave-model: $*" >&2; exit 1; }
[ -f "$dir/flags.make" ] || fail "$build is not a built tree (no $dir/flags.make)"
[ -f "$dir/src/ice40.cpp.o" ] || fail "$build has no ice40.cpp.o (iCE40 support off?)"

flag() { sed -n "s/^$1 = //p" "$dir/flags.make"; }
cd "$build"
eval c++ "$(flag CXX_FLAGS)" "$(flag CXX_DEFINES)" "$(flag CXX_INCLUDES)" \
	-I"$src/src" -c "$here/ice40-slave-model.cpp" -o ice40-slave-model.o
grep -q 'CMakeFiles/openFPGALoader.dir/src/main.cpp.o' "$dir/link.txt" \
	|| fail "no main.cpp.o in $dir/link.txt"
link=$(sed -e 's#CMakeFiles/openFPGALoader.dir/src/main.cpp.o#ice40-slave-model.o#' \
	-e 's#-o openFPGALoader #-o ice40-slave-model #' "$dir/link.txt")
sh -c "$link"
./ice40-slave-model
