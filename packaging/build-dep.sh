#!/bin/sh
# Install the build dependencies of the source tree in the current directory.
# Runs INSIDE the build container, in the tree, as build-deb.sh and
# build-libs.sh do.
#
#   [APT_REPO_ACTION=<dir>] [NOTES=<dir>] SUITE=<suite> packaging/build-dep.sh
#
# With APT_REPO_ACTION (a checkout of mithro/apt-repo-action, as
# build-debs.yml mounts), this is the shared build-deb's own step,
# build-deb/raspbian/build-dep.sh: the suite first and, for a
# raspbian-<codename> suite whose archive alone can't satisfy them (raspbian
# forky in 2026-09, half-copied from staging), Raspbian's own
# <codename>-staging and rebuilds of what its builders haven't built yet,
# verified with the same pinned archive key. What came from staging or was
# rebuilt is listed in $NOTES. Only the build container sees any of it: the
# install test runs in a clean container of the suite alone.
#
# Without it (a local build), plain `apt-get build-dep`.
set -eu
SUITE=${SUITE:?set SUITE to the Debian suite being built for}
if [ -z "${APT_REPO_ACTION:-}" ]; then
	exec apt-get build-dep -y -q ./
fi
case $SUITE in
raspbian-*) codename=${SUITE#raspbian-} ;;
*) codename= ;;
esac
# The shared script starts its notes afresh on every call, and a job can
# call this more than once (build-libs.sh: once per library), so each call
# gets its own directory and adds to the job's notes.
notes=${NOTES:-${REPO:-/work}/build/notes}
mkdir -p "$notes"
call=$(mktemp -d "$notes/call.XXXXXX")
bash "$APT_REPO_ACTION/build-deb/raspbian/build-dep.sh" "$codename" "$call"
for f in from-staging rebuilt; do
	if [ -f "$call/$f" ]; then cat "$call/$f" >> "$notes/$f"; fi
done
