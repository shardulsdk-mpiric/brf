#!/bin/bash
#
# build_brf_arm.sh -- rebuild the BRF arm's executor WITH the MPTCP MIB
# telemetry (mirror of build_stock_arm.sh, for the other baseline arm).
# Run inside the dev_env VM (BRF's executor builds in the VM; the host
# build is blocked by a pre-existing BPF-header mismatch).
#
#   /mnt/src/fuzzing/brf/baseline/build_brf_arm.sh
#
# BRF needs ONLY the MIB flag -- it already has its own kcov plumbing, so
# the stock-only kcov shim (SYZ_MPTCP_KCOV_BASELINE) does NOT apply here.
#
# Flag placement: BRF's executor recipe is `$(CC) ... $(ADDCFLAGS) $(CFLAGS)`
# (C, not C++).  The define goes in CFLAGS, NOT CXXFLAGS and NOT ADDCFLAGS:
# syz-make auto-fills ADDCFLAGS with the include paths (-I. / -Iexecutor/...);
# overriding it would drop them and the build would fail.  (Same lesson as
# the stock arm, but stock uses CXXFLAGS/ADDCXXFLAGS; BRF uses CFLAGS/ADDCFLAGS.)
#
# FULL rebuild (not just `make executor`): BRF's older arch ships a syz-fuzzer,
# and syz-manager rejects the run if the fuzzer and executor were built at
# DIFFERENT git revisions ("mismatching fuzzer/executor git revisions").  Our
# baseline commits advanced HEAD, so an executor-only rebuild leaves a stale
# fuzzer -> mismatch.  A full `make CFLAGS=...` rebuilds manager+fuzzer+executor
# at the same revision (executor gets the flag), so they agree.
#
# Parallelism is CAPPED (JOBS, default 4): `sys/linux/gen` is a huge generated
# package and compiling several of its dependents at once OOM-kills the Go
# compiler on a RAM-limited build VM ("signal: killed").  Lower JOBS=2 if it
# still gets killed; raise it if the VM has plenty of RAM.

set -eu

BRF_SRC="${BRF_SRC:-/mnt/src/fuzzing/brf}"   # in-VM 9p path; override on host
FLAG="-DSYZ_MPTCP_MIB_STATS=1"
JOBS="${JOBS:-4}"                            # cap parallelism to avoid OOM on sys/linux/gen

[ -d "$BRF_SRC" ] || { echo "BRF tree not found: $BRF_SRC (set BRF_SRC=...)" >&2; exit 1; }

# 9p-mounted tree owned by a different uid than the VM root trips git's guard;
# the Makefile shells out to git (version stamp), so allow it.
git config --global --add safe.directory "$BRF_SRC" 2>/dev/null || true

cd "$BRF_SRC"

TIP="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "=== Building BRF arm (manager + fuzzer + executor) WITH the MPTCP MIB telemetry ==="
echo "    tree: $BRF_SRC"
echo "    tip : $TIP   <-- record this; fuzzer and executor must share it"
echo "    flag: $FLAG  (in CFLAGS)   jobs: $JOBS"

# Full build so fuzzer and executor share the git revision (manager checks it).
make CFLAGS="$FLAG" -j"$JOBS"

echo
echo "=== Done. ==="
echo "  syz-executor: $BRF_SRC/bin/linux_amd64/syz-executor (MIB telemetry on)"
echo "  syz-fuzzer + syz-executor rebuilt at $TIP (revisions match)."
echo "  Revert the MIB telemetry source later:  git -C $BRF_SRC checkout executor/executor.cc"
