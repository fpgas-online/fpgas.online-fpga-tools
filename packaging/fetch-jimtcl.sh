#!/bin/sh
# Populate the jimtcl submodule of an OpenOCD tree at the commit the tree
# records, for --enable-internal-jimtcl.
#
#   packaging/fetch-jimtcl.sh <openocd-src>
#
# fpgatools fetches upstream by commit with --depth 1, which brings no
# submodule contents, and the distribution jimtcl cannot be relied on: the
# 0.12.0 release does not compile against jimtcl 0.83 (trixie/sid), while
# master needs a newer one than bookworm ships. Each OpenOCD commit names the
# jimtcl it was developed against, so that is what gets built in.
set -eu
src=$1
[ -f "$src/configure.ac" ] || { echo "fetch-jimtcl: $src is not an OpenOCD tree" >&2; exit 1; }
if [ -f "$src/jimtcl/configure" ]; then
	echo "fetch-jimtcl: $src/jimtcl already populated"
	exit 0
fi
commit=$(cd "$src" && git ls-tree HEAD jimtcl | awk '{print $3}')
[ -n "$commit" ] || { echo "fetch-jimtcl: no jimtcl submodule recorded in $src" >&2; exit 1; }
rm -rf "$src/jimtcl"
git init -q "$src/jimtcl"
git -C "$src/jimtcl" fetch -q --depth 1 https://github.com/msteveb/jimtcl.git "$commit"
git -C "$src/jimtcl" checkout -q --detach FETCH_HEAD
echo "fetch-jimtcl: jimtcl at $commit"
