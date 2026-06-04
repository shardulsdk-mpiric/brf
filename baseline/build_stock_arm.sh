#!/bin/bash
#
# build_stock_arm.sh -- build the stock-syzkaller baseline arm WITH the
# MPTCP kcov measurement shim (see README.md + the 0001-*.patch here).
# Run inside the dev_env VM (same place BRF's executor is built, for a
# symmetric toolchain).  One command; no flags to remember.
#
#   /mnt/src/fuzzing/brf/baseline/build_stock_arm.sh
#
# Override the stock tree location with SYZ_SRC=... if needed.

set -eu

SYZ_SRC="${SYZ_SRC:-/mnt/src/fuzzing/syzkaller}"   # in-VM 9p path; override on host
# The shim define goes in CXXFLAGS, NOT ADDCXXFLAGS: the Makefile's syz-make step
# auto-injects the real executor compile flags (-std=c++17 -I. -Iexecutor/_include
# ...) as ADDCXXFLAGS at parse time; overriding ADDCXXFLAGS drops the include paths
# and the build fails on `pkg/flatrpc/flatrpc.h: No such file`. The recipe uses
# `$(ADDCXXFLAGS) $(CXXFLAGS)`, and syz-make leaves CXXFLAGS free for us.
# Stock arm needs BOTH: the kcov coverage-attribution shim AND the MIB telemetry.
# (The BRF arm needs only the MIB flag -- it already has its own kcov plumbing;
#  build BRF's executor with: make executor CXXFLAGS=-DSYZ_MPTCP_MIB_STATS=1)
FLAG="-DSYZ_MPTCP_KCOV_BASELINE=1 -DSYZ_MPTCP_MIB_STATS=1"

[ -d "$SYZ_SRC" ] || { echo "Stock syzkaller tree not found: $SYZ_SRC (set SYZ_SRC=...)" >&2; exit 1; }

# The 9p-mounted tree is owned by a different uid than the VM root, which
# trips git's ownership guard; the Makefile shells out to git, so allow it.
git config --global --add safe.directory "$SYZ_SRC" 2>/dev/null || true

cd "$SYZ_SRC"

TIP="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "=== Building stock syzkaller WITH the MPTCP kcov measurement shim ==="
echo "    tree: $SYZ_SRC"
echo "    tip : $TIP   <-- record this for the run's reproducibility"
echo "    flag: $FLAG"
echo
echo "(First build may fetch the Go toolchain pinned in go.mod -- needs VM network.)"
echo

# Plain `make` first: lets syz-make inject the real ADDCXXFLAGS (include paths,
# sanitizer-coverage flags, etc.) and builds the host bins (syz-manager) + target.
# Then rebuild the executor with our shim define appended via CXXFLAGS (ADDCXXFLAGS
# stays auto-injected, so the includes survive).
make          -j"$(nproc)"
make executor CXXFLAGS="$FLAG" -j"$(nproc)"

echo
echo "=== Done. ==="
echo "  syz-manager : $SYZ_SRC/bin/syz-manager"
echo "  syz-executor: $SYZ_SRC/bin/linux_amd64/syz-executor (shimmed)"
echo "  Revert to pristine stock later:  git -C $SYZ_SRC checkout executor/executor.cc"
