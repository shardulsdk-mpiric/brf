# Fair coverage baseline -- stock syzkaller arm

For a credible coverage comparison between the BRF MPTCP harness and
**stock upstream syzkaller** on the *same* kcov-instrumented kernel, the
two fuzzers must be measured with the *same* instrumentation.  This
directory holds the one measurement-only change stock needs, plus a
one-command build helper.

## Why a shim at all (the fairness point)

The fuzzing kernel's BRF kcov patches instrument MPTCP's **softirq**
crypto-gate functions (`subflow_token_join_request`,
`subflow_thmac_valid`, `subflow_hmac_valid`, the option parser,
ADD_ADDR HMAC, HMAC-failure reset).  Those wraps only attribute coverage
to a socket that carries a kcov remote handle, which the BRF harness
sets via `setsockopt(SOL_MPTCP, MPTCP_KCOV_HANDLE, kcov_common_handle())`.
Stock never sets it, so **stock's coverage of exactly that crypto-gate
surface would be invisible** -- a measurement bias at the headline
surface, not a real reachability difference.

The shim makes stock register the same handle on its MPTCP sockets /
accepted fds.  It is **measurement-only**: it sets a kcov handle and
allocates the kcov area; it changes no MPTCP behavior and no
reachability.  Honest framing for any write-up: *both arms get identical
coverage instrumentation; only the harness differs.*

## Files

- `0001-stock-syzkaller-baseline-executor-instrumentation.patch` -- the
  stock `executor/executor.cc` (+ `executor/common_linux.h`) changes,
  carrying TWO independent, measurement-only, build-flag-gated instruments
  (both inert unless built with their flag).  Apply to a fresh stock
  checkout with `git apply`.
  1. **kcov coverage-attribution shim** (`SYZ_MPTCP_KCOV_BASELINE`): sets
     `MPTCP_KCOV_HANDLE` on MPTCP sockets so stock's softirq crypto-gate
     coverage is attributed (see "Why a shim" above).
  2. **MIB telemetry snapshot** (`SYZ_MPTCP_MIB_STATS`): every >=10s,
     appends this VM's `/proc/net/netstat` MPTcpExt counters to a 9p host
     share, for the quantitative "N MP_JOIN attempts / M HMAC failures /
     ~0 passed" comparison.  Reads a STANDARD counter (no kernel
     dependency); runs at the top of `execute_one`, outside the kcov
     window; errno saved/restored.  This same snapshot is also in the BRF
     executor (`open/src/fuzzing/brf/executor/executor.cc`) so BOTH arms
     are measured identically.
     - **Sandbox-mount detail (the `common_linux.h` hunk).** Stock's
       *newer* `do_sandbox_none()` calls `sandbox_common_mount_tmpfs()`,
       which `pivot_root`+`chroot`s into a fresh root before any
       `execute_one` runs -- so a lazy `mount()` of the 9p share from
       inside `execute_one` can never reach the host (the new root has no
       `/mnt`, writes silently fall back to a VM-local `/tmp`).  The
       gated `common_linux.h` hunk mounts the `mibstats` share INTO the
       new root (as `/mibstats`) *before* the pivot, where fork-children
       inherit it.  The snapshot then probes `/mnt/mptcp_mib_stats` (the
       BRF arm's *older* sandbox does NOT chroot, so its lazy mount still
       works there), then `/mibstats` (stock), then a `/tmp` last resort
       -- so the SAME `executor.cc` snapshot works under either sandbox.
- `build_stock_arm.sh` -- builds the stock binaries (manager + fuzzer +
  executor) in one command with BOTH flags, handling the git-ownership guard.
- `build_brf_arm.sh` -- rebuilds the BRF arm's executor with the MIB flag
  only (BRF has its own kcov plumbing).  NOTE: BRF's recipe uses
  `$(CC)`/`CFLAGS`, so the define goes in `CFLAGS` (stock uses
  `CXXFLAGS`); the helper handles this.
- `mib_tally.py` -- host-side: sums the MPTcpExt counters from an arm's
  `mib_stats/` dir (per distinct boot+proc netns).  Run on the host:
  `mib_tally.py <workdir_baseline_{brf,stock}>/mib_stats`.  Deep MIB-only.
- `baseline_summary.py` -- host-side WHOLE-RUN summarizer (all telemetry,
  not just MIB).  Side-by-side stock-vs-BRF report covering: run status +
  liveness (ledger + bench freshness); the matched coverage/corpus/exec/
  crash headline (from `-bench`); the MP_JOIN crypto-gate funnel (MIB, with
  an exec-normalized "valid joins / 1k execs" fairness rate); per-arm crash
  signatures (flags harness-noise vs real bugs); and the BRF-only struct_ops
  verifier accept/reject tally.  **Scopes to the current run by default**
  (since the last ledger `SESSION_START`), so per-run aggregates match the
  per-run bench window; `--all` for cumulative.  Safe to run mid-run.
  ```
  baseline_summary.py <run_root>            # one-shot (run_root = the syz_manager dir)
  baseline_summary.py <run_root> --watch 30 # refresh every 30s
  baseline_summary.py <run_root> --all      # ignore run scoping (cumulative)
  baseline_summary.py <run_root> --present  # conference-style picture (ASCII funnel)
  ```
  The `--present` view renders the audience-facing story: a monotonic
  "how deep into the MPTCP handshake does each fuzzer reach?" funnel
  (stock stalls at the first SYN; BRF descends to the HMAC gate);
  per-attempt CONVERSION RATES (SYN->established: stock ~0%, BRF much higher --
  the volume-independent rebuttal to "BRF just does more attempts"); the
  scoped-coverage bar; an ASCII coverage-trajectory sparkline + brf/stock
  ratio over the run (shows the lead is stable/widening, not a lucky
  instant); and the pass/fail split at the gate.  `--watch` composes with
  it.  MIB aggregation sums the PEAK of each monotonic
  segment per `(boot, proc)` so executor-restart netns resets are not
  undercounted (matches `mib_tally.py`).
- `baseline_plot.py` -- GRAPHICAL dashboard (PNG) of the same data, for
  when a picture beats a table.  Reuses `baseline_summary.py`'s parsers
  (same current-run scoping) and renders four panels: coverage-over-time
  (both arms), the brf/stock coverage ratio over time (lead stable/widening
  vs a fluke), the MP_JOIN handshake funnel (grouped bars, how deep each
  reaches), and the HMAC gate pass/fail + per-attempt conversion rate.
  Needs `matplotlib`+`numpy`.
  ```
  baseline_plot.py <run_root>                 # -> <run_root>/baseline_plots/dashboard_<ts>.png
  baseline_plot.py <run_root> -o /tmp/d.png   # custom output path
  baseline_plot.py <run_root> --all           # cumulative across runs
  baseline_plot.py <run_root> --split         # one PNG per panel (panel_<name>_<ts>.png)
                                              #   -- handy to drop a single chart into a slide
  baseline_plot.py <run_root> --watch 5       # regenerate every 5 min (live-updating image);
                                              #   pair with a fixed -o so it overwrites one file
  ```
  Panels are factored out (`PANELS` list), so `--split` and the combined
  dashboard render the exact same charts.  `--watch N -o live.png` keeps a
  single file refreshed (open it in any image viewer that auto-reloads).

## Build (in the dev_env VM, for symmetry with the BRF build)

```
/mnt/src/fuzzing/brf/baseline/build_stock_arm.sh
```

That is all you need to remember.  It runs, in the stock tree:

```
git config --global --add safe.directory <stock tree>   # 9p ownership guard
make                                       -j$(nproc)    # host bins + target
make executor CXXFLAGS="-DSYZ_MPTCP_KCOV_BASELINE=1 -DSYZ_MPTCP_MIB_STATS=1" -j$(nproc)   # executor + shim + MIB
```

Notes:
- **Put the shim define in `CXXFLAGS`, NOT `ADDCXXFLAGS`.**  The Makefile's
  `syz-make` step auto-injects the real executor compile flags
  (`-std=c++17 -I. -Iexecutor/_include` ...) *as* `ADDCXXFLAGS` at parse
  time.  Overriding `ADDCXXFLAGS` on the command line drops those include
  paths and the build fails with
  `executor/executor.cc: fatal error: pkg/flatrpc/flatrpc.h: No such file`.
  The recipe is `$(ADDCXXFLAGS) $(CXXFLAGS)` and `syz-make` leaves
  `CXXFLAGS` free, so our define rides there with the includes intact.  No
  `tools/syz-env`/docker needed -- the in-VM `make` self-injects the flags.
- **git "dubious ownership":** the source is on a 9p mount owned by a
  different uid than the VM's root, so git refuses to run (the Makefile
  shells out to git for version info).  The `safe.directory` line above
  fixes it; the script does it for you.  (One-off alternative for the
  whole VM: `git config --global --add safe.directory '*'`.)
- **Go toolchain download:** the first build may fetch the Go version
  pinned in `go.mod` (e.g. `go: downloading go1.26.0`).  That needs
  network in the VM.  If the VM is offline, install/point at a matching
  Go and set `GOTOOLCHAIN=local`.
- **Pin the tip:** the stock checkout tracks upstream and advances.
  Record `git rev-parse HEAD` of the stock tree for the run so the
  comparison is reproducible (the build helper prints it).
- **Revert to pristine stock** when done:
  `git -C <stock tree> checkout executor/executor.cc`.

## Why gated (not default-on)

Keeping the `SYZ_MPTCP_KCOV_BASELINE` flag means the checkout still
builds as true upstream syzkaller for any other use, and the binary we
measure is honestly "stock + an explicit, named measurement flag."  The
build helper carries the flag so it never has to be recalled by hand.
(If an always-on shim is ever preferred, drop the
`defined(SYZ_MPTCP_KCOV_BASELINE)` half of the guard.)

## Attribution

Syzkaller (Vyukov et al., `google/syzkaller`) is the engine; BRF (Hung &
Amiri Sani, arXiv:2305.08782) is the runtime fuzzer this work extends.
See `../findings/methodology.md` Sec 7.
