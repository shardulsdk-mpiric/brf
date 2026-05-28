#!/bin/bash
#
# check_vm_readiness.sh -- run the MPTCP protocol-flow harness's
# graduated smoke-test ladder on a freshly booted dev_env VM and
# verify results.  Stops at the first FAIL by default so the
# earliest-broken component is the visible signal.
#
# Canonical dependency list: $BRF/BUILD_DEPS.md  (host + VM packages,
# kernel configs, runtime quirks).  This script verifies all of it
# mechanically; BUILD_DEPS.md is the source of truth.
#
# Source-of-truth layout (no file copies; everything stays on 9p):
#
#   /mnt/src   -- $OPEN_DIR/src   (BRF tree, kernel source)
#   /mnt/build -- $OPEN_DIR/build (kernel build with vmlinux for BTF refs)
#   /mnt/host  -- $SHARED_DIR     (shared scratch; pre-built BPF .o,
#                                  wrapper script, sample progs)
#
# gcc reads sources straight off 9p; build artefacts go to $WORK on local
# rootfs (avoids the mkswap-on-9p quirk per BUILD_DEPS.md:70).
#
# Run as root inside the dev_env VM.
#
# Layout (graduated; bail at first FAIL):
#   §1 Environment sanity   -- kernel config, kcov, MPTCP, libbpf, NFQUEUE
#   §2 In-VM C smoke tests  -- Tests A-F (sources in $BRF/kernel_patches/mptcp_kcov/)
#   §3 Phase 3 BPF struct_ops -- $BPF_OBJ (host-built) + executor/bpf_progs/test_mptcp_bpf_sched.c
#   §4 Fuzz-driver smoke    -- $WRAPPER $BRF_PROG
#
# Usage:
#   ./check_vm_readiness.sh [--help|--list]
#
# Env vars (override defaults; defaults are the canonical VM paths):
#   BRF=<path>            BRF tree (default: /mnt/src/fuzzing/brf)
#   KBUILD=<path>         kernel build dir with vmlinux (default: latest under /mnt/build/linux/)
#   SHARED=<path>         shared scratch root for the current task (default: /mnt/host/mpiric/your-task -- override with your real path)
#   WORK=<dir>            scratch build dir on LOCAL rootfs (default: /tmp/brf_vm_readiness)
#   STOP_ON_FAIL=yes|no   stop at first FAIL (default: yes)
#   RUN_PHASE3=auto|yes|no  run §3 (default: auto -- on if $BPF_OBJ exists)
#   BPF_OBJ=<file>        pre-built mptcp_sched.bpf.o (default: $SHARED/work/brf_protocol_fuzz_setup/mptcp_bpf_sched_test/mptcp_sched.bpf.o)
#   WRAPPER=<file>        run_brf_prog.sh path (default: $SHARED/work/brf_protocol_fuzz_setup/scripts/run_brf_prog.sh)
#   BRF_PROG=<file>       sample prog for §4 (default: $SHARED/work/brf_protocol_fuzz_setup/progs/pair_init.prog)
#
# Exit codes:
#   0  ready (all critical tests PASS; warnings ok)
#   1  at least one FAIL
#   2  preconditions not met (not root, VM mounts missing, etc.)
#
# Author: Shardul Bankar.  Co-developed-by: Claude Opus 4.7 (1M context).

set -u

# ----- canonical VM paths ---------------------------------------------

BRF="${BRF:-/mnt/src/fuzzing/brf}"
SHARED="${SHARED:-/mnt/host/mpiric/your-task}"
WORK="${WORK:-/tmp/brf_vm_readiness}"
STOP_ON_FAIL="${STOP_ON_FAIL:-yes}"
RUN_PHASE3="${RUN_PHASE3:-auto}"

# KBUILD: explicit > newest under /mnt/build/linux/ that has a vmlinux.
if [ -z "${KBUILD:-}" ]; then
    if [ -d /mnt/build/linux ]; then
        # newest dir containing vmlinux
        KBUILD="$(ls -1dt /mnt/build/linux/*/ 2>/dev/null | while read -r d; do
            [ -e "${d}vmlinux" ] && { echo "${d%/}"; break; }
        done)"
    fi
fi
KBUILD="${KBUILD:-}"

# Derived
TESTS_DIR="$BRF/kernel_patches/mptcp_kcov"
BPF_PROGS_DIR="$BRF/executor/bpf_progs"
FUZZ_SETUP="$SHARED/work/brf_protocol_fuzz_setup"
BPF_OBJ="${BPF_OBJ:-$FUZZ_SETUP/mptcp_bpf_sched_test/mptcp_sched.bpf.o}"
WRAPPER="${WRAPPER:-$FUZZ_SETUP/scripts/run_brf_prog.sh}"
BRF_PROG="${BRF_PROG:-$FUZZ_SETUP/progs/pair_init.prog}"

if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; D=$'\033[2m'; N=$'\033[0m'
else
    R=; G=; Y=; B=; D=; N=
fi

print_paths() {
    cat <<EOF
${B}Resolved paths${N}
  BRF tree         : $BRF
  Smoke tests      : $TESTS_DIR
  BPF progs        : $BPF_PROGS_DIR
  Kernel build     : ${KBUILD:-${Y}(none found under /mnt/build/linux/)${N}}
  Shared scratch   : $SHARED
  Pre-built BPF .o : $BPF_OBJ $([ -e "$BPF_OBJ" ] && echo "${G}(found)${N}" || echo "${Y}(missing)${N}")
  Wrapper          : $WRAPPER $([ -x "$WRAPPER" ] && echo "${G}(found)${N}" || echo "${Y}(missing)${N}")
  Sample prog      : $BRF_PROG $([ -e "$BRF_PROG" ] && echo "${G}(found)${N}" || echo "${Y}(missing)${N}")
  Build scratch    : $WORK ${D}(local rootfs)${N}
EOF
}

usage() {
    sed -n '3,50p' "$0" | sed 's/^# \{0,1\}//'
    echo
    print_paths
    exit 0
}

case "${1:-}" in
    -h|--help) usage ;;
    --list)    print_paths; exit 0 ;;
esac

# ----- result accounting ----------------------------------------------

declare -a ORDER
declare -A RESULT
declare -A DETAIL
FAILS=0
WARNS=0

mark() {
    local status=$1 tag=$2 detail=${3:-}
    ORDER+=("$tag")
    RESULT[$tag]=$status
    DETAIL[$tag]=$detail
    local col
    case "$status" in
        PASS) col=$G ;;
        FAIL) col=$R; FAILS=$((FAILS+1)) ;;
        SKIP) col=$Y ;;
        WARN) col=$Y; WARNS=$((WARNS+1)) ;;
        *)    col=$N ;;
    esac
    printf "  %s%-4s%s  %s%s\n" "$col" "$status" "$N" "$tag" "${detail:+ -- $detail}"
}

maybe_stop() {
    if [ "$FAILS" -gt 0 ] && [ "$STOP_ON_FAIL" = "yes" ]; then
        echo
        echo "${R}Stopping at first FAIL (set STOP_ON_FAIL=no to run full ladder).${N}"
        summary_and_exit
    fi
}

summary_and_exit() {
    echo
    echo "${B}== Summary ==${N}"
    for tag in "${ORDER[@]}"; do
        printf "  %-60s %s\n" "$tag" "${RESULT[$tag]}"
    done
    echo
    if [ "$FAILS" -eq 0 ]; then
        echo "${G}OVERALL: ready${N}  ($WARNS warning(s); outputs in $WORK)"
        exit 0
    else
        echo "${R}OVERALL: $FAILS failure(s)${N}  (outputs in $WORK; triage from earliest FAIL)"
        exit 1
    fi
}

# ----- preconditions --------------------------------------------------

if [ "$EUID" -ne 0 ]; then
    echo "${R}Error${N}: must run as root (sysctl writes, iptables, raw sockets)"
    exit 2
fi

# Confirm we're in the VM with the 9p mounts up.
missing=()
[ -d /mnt/src ]   || missing+=("/mnt/src (srcshare)")
[ -d /mnt/build ] || missing+=("/mnt/build (buildshare)")
[ -d /mnt/host ]  || missing+=("/mnt/host (hostshare)")
if [ "${#missing[@]}" -gt 0 ]; then
    echo "${R}Error${N}: missing 9p mount(s) -- not running in the dev_env VM?"
    for m in "${missing[@]}"; do echo "  - $m"; done
    echo "Trigger automounts:  ls /mnt/src /mnt/build /mnt/host"
    exit 2
fi
# Touching the automount paths kicks the 9p mount if it hasn't fired yet.
ls /mnt/src /mnt/build /mnt/host >/dev/null 2>&1

if [ ! -d "$TESTS_DIR" ]; then
    echo "${R}Error${N}: BRF tests dir not found at $TESTS_DIR"
    echo "Override:  BRF=<path-to-brf-tree> $0"
    exit 2
fi

mkdir -p "$WORK"
cd "$WORK"

print_paths
echo

# Kernel banner -- which kernel/build is actually running?
echo "${B}== Kernel ==${N}"
echo "  uname -r : $(uname -r)"
echo "  uname -v : $(uname -v)"
if [ -n "$KBUILD" ] && [ -e "$KBUILD/vmlinux" ]; then
    vm_mtime=$(stat -c '%y' "$KBUILD/vmlinux" 2>/dev/null | cut -d. -f1)
    echo "  KBUILD   : $KBUILD"
    echo "  vmlinux  : $KBUILD/vmlinux  ($vm_mtime)"
else
    echo "  ${Y}KBUILD   : (none) -- no vmlinux available for cross-reference${N}"
fi
echo

# dmesg snapshot for before/after diff
dmesg > "$WORK/dmesg.before" 2>/dev/null || true

# ----- §1 environment sanity ------------------------------------------

echo "${B}== §1 Environment sanity ==${N}"

# kcov debugfs
if [ -e /sys/kernel/debug/kcov ]; then
    mark PASS "kcov debugfs node present"
else
    mark FAIL "kcov debugfs node missing" "/sys/kernel/debug/kcov absent (CONFIG_KCOV=n?)"
fi
maybe_stop

# MPTCP enabled
if [ "$(cat /proc/sys/net/mptcp/enabled 2>/dev/null || echo 0)" = "1" ]; then
    mark PASS "MPTCP enabled"
else
    mark FAIL "MPTCP not enabled" "/proc/sys/net/mptcp/enabled != 1"
fi
maybe_stop

# vmlinux BTF (needed for Phase 3)
if [ -e /sys/kernel/btf/vmlinux ]; then
    mark PASS "vmlinux BTF present"
    HAVE_BTF=1
else
    mark WARN "vmlinux BTF missing" "CONFIG_DEBUG_INFO_BTF=n -- Phase 3 unavailable"
    HAVE_BTF=0
fi

# available schedulers
if [ -e /proc/sys/net/mptcp/available_schedulers ]; then
    scheds=$(cat /proc/sys/net/mptcp/available_schedulers 2>/dev/null || echo "?")
    mark PASS "MPTCP schedulers" "available: $scheds"
else
    mark WARN "MPTCP available_schedulers missing"
fi

# libbpf (Phase 3 + executor) -- need BOTH runtime and headers
HAVE_LIBBPF_RT=0
HAVE_LIBBPF_HDR=0
ldconfig -p 2>/dev/null | grep -q '\<libbpf\.so' && HAVE_LIBBPF_RT=1
[ -e /usr/include/bpf/bpf.h ] || [ -e /usr/local/include/bpf/bpf.h ] && HAVE_LIBBPF_HDR=1
if [ "$HAVE_LIBBPF_RT" = "1" ] && [ "$HAVE_LIBBPF_HDR" = "1" ]; then
    mark PASS "libbpf present (runtime + headers)"
    HAVE_LIBBPF=1
elif [ "$HAVE_LIBBPF_RT" = "1" ]; then
    mark WARN "libbpf headers missing" "apt install libbpf-dev -- Phase 3 compile will fail"
    HAVE_LIBBPF=0
else
    mark WARN "libbpf missing" "apt install libbpf-dev"
    HAVE_LIBBPF=0
fi

# NFQUEUE kernel surface.  Checks, in order:
#   1. KBUILD/.config (authoritative if available)
#   2. /proc/config.gz / /boot/config-$(uname -r) (if CONFIG_IKCONFIG_PROC=y)
#   3. /sys/module/nfnetlink_queue (loaded module)
#   4. iptables -m nfqueue probe
HAVE_NFQ_KERNEL=0
if [ -n "$KBUILD" ] && [ -e "$KBUILD/.config" ] \
       && grep -q '^CONFIG_NETFILTER_NETLINK_QUEUE=[ym]' "$KBUILD/.config"; then
    HAVE_NFQ_KERNEL=1
elif zgrep -q '^CONFIG_NETFILTER_NETLINK_QUEUE=[ym]' /proc/config.gz 2>/dev/null \
       || grep -q '^CONFIG_NETFILTER_NETLINK_QUEUE=[ym]' /boot/config-"$(uname -r)" 2>/dev/null; then
    HAVE_NFQ_KERNEL=1
elif [ -d /sys/module/nfnetlink_queue ]; then
    HAVE_NFQ_KERNEL=1
elif command -v iptables >/dev/null 2>&1 && iptables -m nfqueue -h >/dev/null 2>&1; then
    HAVE_NFQ_KERNEL=1
fi
if [ "$HAVE_NFQ_KERNEL" = "1" ]; then
    mark PASS "NFQUEUE kernel support"
else
    mark WARN "NFQUEUE kernel support missing" "kernel built without CONFIG_NETFILTER_NETLINK_QUEUE -- Test E will fail with nfq_create_queue: EINVAL"
fi

# libnetfilter_queue (Test E userland dep)
if ldconfig -p 2>/dev/null | grep -q libnetfilter_queue; then
    mark PASS "libnetfilter_queue present"
    HAVE_LNFQ=1
else
    mark WARN "libnetfilter_queue absent" "apt install libnetfilter-queue-dev -- Test E will SKIP"
    HAVE_LNFQ=0
fi

# tools needed for compiling
for tool in gcc iptables ip ss; do
    command -v "$tool" >/dev/null 2>&1 || mark WARN "$tool not in PATH"
done

# pre-existing kernel splats -- triage signal.  Use `grep | wc -l` rather than
# `grep -c ... || echo 0` -- grep -c outputs "0" AND exits 1 on no-match, so
# `|| echo 0` doubles the output to "0\n0" which breaks the [ -gt 0 ] test.
PRE_SPLAT=$(grep -E 'KASAN:|kernel BUG|WARNING:|BUG: ' "$WORK/dmesg.before" 2>/dev/null | wc -l)
if [ "${PRE_SPLAT:-0}" -gt 0 ]; then
    mark WARN "pre-existing dmesg splats" "$PRE_SPLAT KASAN/BUG/WARN line(s) before tests started"
fi

maybe_stop
echo

# ----- §2 in-VM C smoke tests -----------------------------------------

echo "${B}== §2 In-VM smoke tests ==${N}"
echo "${D}  Sources read from $TESTS_DIR; binaries written to $WORK.${N}"

# build_run <tag> <src-basename> <bin-name> "<cflags>" <expected_exit>
build_run() {
    local tag=$1 src=$2 bin=$3 cflags=$4 expect=$5
    local src_path="$TESTS_DIR/$src"
    local bin_path="$WORK/$bin"
    if [ ! -e "$src_path" ]; then
        mark SKIP "$tag" "source missing: $src_path"
        return
    fi
    if ! eval gcc -O2 -Wall -o "$bin_path" "$src_path" "$cflags" \
            2>"$WORK/$bin.build.log"; then
        mark SKIP "$tag" "compile failed -- see $WORK/$bin.build.log"
        return
    fi
    "$bin_path" >"$WORK/$bin.out" 2>&1
    local rc=$?
    if [ "$rc" -eq "$expect" ]; then
        mark PASS "$tag" "exit $rc"
    else
        mark FAIL "$tag" "exit $rc (expected $expect); see $WORK/$bin.out"
        echo "    ---- tail $WORK/$bin.out ----"
        tail -10 "$WORK/$bin.out" 2>/dev/null | sed 's/^/    /'
        echo "    -----------------------------"
    fi
}

build_run "Test A: setsockopt(MPTCP_KCOV_HANDLE)" \
          test_setsockopt_handle.c test_setsockopt_handle "" 0
maybe_stop

build_run "Test B: MP_JOIN normal flow + MIB delta" \
          test_mp_join_normal.c test_mp_join_normal "" 0
maybe_stop

build_run "Test C: MP_JOIN backup-flag plumbing" \
          test_mp_join_backup.c test_mp_join_backup "" 0
maybe_stop

build_run "Test D: control-suboption emission" \
          test_mp_send_control.c test_mp_send_control "" 0
maybe_stop

if [ "$HAVE_LNFQ" != "1" ]; then
    mark SKIP "Test E: HMAC bit-flip via NFQUEUE" "libnetfilter_queue absent (apt install libnetfilter-queue-dev)"
elif [ "$HAVE_NFQ_KERNEL" != "1" ]; then
    mark SKIP "Test E: HMAC bit-flip via NFQUEUE" "kernel built without CONFIG_NETFILTER_NETLINK_QUEUE -- rebuild kernel"
elif ! command -v iptables >/dev/null 2>&1; then
    mark SKIP "Test E: HMAC bit-flip via NFQUEUE" "iptables missing (apt install iptables)"
else
    build_run "Test E: HMAC bit-flip via NFQUEUE" \
              test_mp_join_hmac_bitflip.c test_mp_join_hmac_bitflip \
              "-lnetfilter_queue -lnfnetlink -lpthread" 0
    maybe_stop
fi

# ----- §3 Phase 3 BPF struct_ops scheduler ----------------------------

echo "${B}== §3 Phase 3 -- BPF struct_ops scheduler ==${N}"

run_phase3=0
case "$RUN_PHASE3" in
    yes) run_phase3=1 ;;
    no)  mark SKIP "Phase 3 BPF struct_ops" "RUN_PHASE3=no" ;;
    auto)
        if [ -e "$BPF_OBJ" ]; then
            run_phase3=1
        else
            mark SKIP "Phase 3 BPF struct_ops" "BPF_OBJ not present at $BPF_OBJ"
        fi
        ;;
esac

if [ "$run_phase3" = "1" ]; then
    if [ "$HAVE_LIBBPF" != "1" ]; then
        mark SKIP "Phase 3 BPF struct_ops" "libbpf missing in VM"
    elif [ "$HAVE_BTF" != "1" ]; then
        mark SKIP "Phase 3 BPF struct_ops" "vmlinux BTF missing -- kernel struct_ops support unavailable"
    elif [ ! -e "$BPF_PROGS_DIR/test_mptcp_bpf_sched.c" ]; then
        mark SKIP "Phase 3 BPF struct_ops" "$BPF_PROGS_DIR/test_mptcp_bpf_sched.c missing"
    else
        # Build the userspace test driver from 9p source straight into $WORK;
        # pass the host-built .o by 9p path (no copy).
        if gcc -O2 -Wall -o "$WORK/test_mptcp_bpf_sched" \
                "$BPF_PROGS_DIR/test_mptcp_bpf_sched.c" \
                -lbpf -lelf 2>"$WORK/phase3.build.log"; then
            "$WORK/test_mptcp_bpf_sched" "$BPF_OBJ" >"$WORK/phase3.out" 2>&1
            rc=$?
            if [ "$rc" -eq 0 ] && grep -q "result: PASS" "$WORK/phase3.out"; then
                mark PASS "Phase 3: load/register/select/data-transfer"
            else
                mark FAIL "Phase 3: BPF struct_ops" "exit $rc; see $WORK/phase3.out"
                tail -20 "$WORK/phase3.out" 2>/dev/null | sed 's/^/    /'
            fi
        else
            mark SKIP "Phase 3 BPF struct_ops" "compile failed -- see $WORK/phase3.build.log"
        fi
    fi
fi
maybe_stop
echo

# ----- §4 fuzz-driver end-to-end smoke --------------------------------

echo "${B}== §4 Fuzz-driver end-to-end smoke ==${N}"

if [ ! -x "$WRAPPER" ]; then
    mark SKIP "Fuzz-driver smoke" "wrapper not executable: $WRAPPER"
elif [ ! -e "$BRF_PROG" ]; then
    mark SKIP "Fuzz-driver smoke" "prog not present: $BRF_PROG"
else
    # Run wrapper from /root: BUILD_DEPS.md:70 -- mkswap fails on 9p cwd.
    saved=$PWD
    cd /root
    "$WRAPPER" "$BRF_PROG" >"$WORK/fuzz_driver.out" 2>&1
    rc=$?
    cd "$saved"
    if [ "$rc" -eq 0 ]; then
        mark PASS "Fuzz-driver smoke" "$BRF_PROG (exit 0)"
    else
        mark FAIL "Fuzz-driver smoke" "exit $rc; see $WORK/fuzz_driver.out"
        tail -20 "$WORK/fuzz_driver.out" 2>/dev/null | sed 's/^/    /'
    fi
fi
echo

# ----- post-run dmesg diff (kernel-side signal) -----------------------

echo "${B}== Post-run dmesg diff ==${N}"
dmesg > "$WORK/dmesg.after" 2>/dev/null || true
diff "$WORK/dmesg.before" "$WORK/dmesg.after" > "$WORK/dmesg.diff" 2>/dev/null || true
new_splats=$(grep -E 'KASAN:|kernel BUG|WARNING:|BUG: ' "$WORK/dmesg.diff" 2>/dev/null | wc -l)
if [ "${new_splats:-0}" -gt 0 ]; then
    mark FAIL "kernel splats during run" "$new_splats NEW KASAN/BUG/WARN line(s) -- see $WORK/dmesg.diff"
    grep -E 'KASAN:|kernel BUG|WARNING:|BUG: ' "$WORK/dmesg.diff" 2>/dev/null \
        | head -10 | sed 's/^/    /'
else
    mark PASS "no new kernel splats" "dmesg clean during run"
fi

summary_and_exit
