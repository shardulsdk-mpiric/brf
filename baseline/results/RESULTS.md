# Controlled coverage comparison: stock syzkaller vs BRF MPTCP harness (2026-06)

Research artifact for the fair coverage-comparison campaign run in June 2026.
This is a **time-boxed record** -- it may be removed once it has served its
utility (the figure/numbers feed a netdev BoF demo and the methodology
write-up); deleting `baseline/results/` later is a clean `git rm`.

Attribution: Syzkaller (Vyukov et al., `google/syzkaller`) -> BRF (Hung &
Amiri Sani, UC Irvine, arXiv:2305.08782) -> Mpiric MPTCP protocol-flow
extension.  This compares stock upstream Syzkaller against the BRF MPTCP
harness; it is not a claim about BRF's eBPF engine.

## What was measured

Three independent runs, stock arm vs BRF arm, on the SAME frozen kernel, SAME
per-run resource budget, SAME kcov instrumentation -- only the harness differs.
Fresh (empty) corpus each run (corpus moved aside between runs -> statistically
independent).  Each run stopped on **coverage saturation** (trailing-6h growth
< 3%/6h on both arms), not a fixed clock; within each run both arms ran
identical wall-clock (uptime delta <= 6s).

| run | dur   | stock valid joins | stock REJECT | BRF valid joins | BRF REJECT | MPTCP-scoped cov (stock/BRF) | whole-kernel cov (stock/BRF) |
|-----|-------|-------------------|--------------|-----------------|------------|------------------------------|------------------------------|
| 1   | 24.6h | 17                | 0            | 10,318          | 2,553      | 2,069 / 2,618  (+27%)        | 14,693 / 21,394  (+46%)      |
| 2   | 23.5h | 2,228             | 0            | 13,117          | 2,437      | 2,594 / 2,802  (+8%)         | 14,713 / 21,816  (+48%)      |
| 3   | 36.4h | 0                 | 0            | 15,440          | 5,190      | 2,442 / 2,796  (+14%)        | 15,060 / 22,144  (+47%)      |

"REJECT" = MP_JOIN invalid-HMAC events (`MPJoinAckHMacFailure` +
`MPJoinSynAckHMacFailure`).  Counts are MPTcpExt MIB, summed per (boot, proc)
netns with segment-peak aggregation (handles executor-restart resets).
Coverage is from each arm's `-bench` snapshot at saturation.  Numbers
independently recomputed from raw data and confirmed.

## Findings (in claim-strength order)

1. **Reject/reset path is BRF-EXCLUSIVE (the durable result -- conclusive,
   structural, duration-independent).**  Stock recorded **0** invalid-HMAC
   events in **all three** runs; BRF reached the path every run.  This is not a
   rate or a coverage delta -- it is binary reachability.  It holds because
   forcing an HMAC *failure* requires constructing/mutating the MP_JOIN HMAC
   yourself, which stock cannot do: stock's joins come only via kernel-PM, where
   the kernel computes a *valid* HMAC.  Proven real-not-unwired: in run 2 stock
   recorded 2,228 valid joins + 2,283 join-SYNs, so its MPTcpExt MP_JOIN block
   is parsed and increments; the failure counters are adjacent fields BRF
   incremented -- stock *could* have logged a reject and logged 0.

2. **MPTCP-scoped coverage (cover_filter): BRF +8% to +27% -- modest and
   VARIABLE.**  This is the `^mptcp_/^subflow_/...` filtered metric (the
   contested surface).  It tracks stock's incidental kernel-PM joins: in run 2
   stock's lucky-join run narrowed the gap to +8%.

3. **Whole-kernel source coverage: BRF +46% to +48% -- and this is NOT MPTCP
   coverage.**  It reflects BRF completing connections and so exercising more of
   the wider TCP / crypto / netlink stack.  Different denominator (~21k vs ~2.6k
   MPTCP PCs); do not present it as "MPTCP coverage" or compare it to (2).

4. **Valid-join count is high-variance:** stock 17 / 2,228 / 0 (luck-of-the-
   corpus, via kernel-PM); BRF 10,318-15,440 (consistently high).  The only
   robust statement is "BRF's worst run (10,318) exceeds stock's best (2,228)" --
   never a fixed ratio, never quote stock's 0 or 17 in isolation.

## Boundaries / honesty (binding on anything built on this)

- **Bug-rate superiority is NOT claimed from this comparison.**  Its metric is
  coverage/reachability, not bugs.  Stock produced 0 crashes across all 3 runs,
  which is not a rate.  This campaign's own crash-finding yielded only a *lead*
  (a single WARN still under triage -- real-bug-vs-instrumentation pending a
  pristine-base vs patched-base replay) -- NOT a new confirmed bug.  The
  specific signature and triage notes are held in private working memory until
  it is confirmed.  Confirmed harness bugs are tracked separately in
  `../../findings/` and come from broader fuzzing, not from this controlled
  comparison.
- **Coverage is directional;** present as a band (the per-run numbers above),
  never a single point.  The reject-path result is the conclusive one.
- **BRF-arm crash noise** during the runs (stock had 0) was our own EARLY-DEV
  instrumentation instability -- the v01 cross-subflow shared per-socket kcov
  area racing under concurrency -- plus a BRF-fuzzer userspace panic.  These
  cost BRF exec-time, so they UNDERSTATE its result (kcov is collected per-exec,
  before any crash) -- conservative.  They are being replaced by the
  upstreamable kcov rework (skb-carried handle on the shared per-CPU area).

## Methodology note worth keeping (the coverage-metric trap)

syzkaller's `-bench` emits TWO coverage fields: `"coverage"` = whole-kernel
source coverage (`pkg/corpus` StatCover), and `"filtered coverage"` =
the cover_filter subset (`syz-manager` statCoverFiltered).  With a cover_filter
set to MPTCP functions, the MPTCP-scoped number is **`"filtered coverage"`**
(~2.6k PCs), NOT `"coverage"` (~21k = whole kernel).  Reporting `"coverage"` as
"MPTCP-scoped" overstates the MPTCP delta ~2x.  The analysis tooling here uses
`"filtered coverage"` wherever it labels output "MPTCP-scoped".

## Reproducibility

```
Frozen base (same bzImage for BOTH arms, all 3 runs):
  mptcp_net-next.git mptcp/export  @ export/20260515T083717 (1b7b378e36da4)
    + kcov preallocated-area core patch (289cf819b)
    + BRF kcov patches: kernel_patches/mptcp_kcov/ (0001-0009)
    + finding-001 fix (a7b17048)
  base branch tip: mptcp_brf_fuzz_base @ a7b17048540a8 (frozen)
  bzImage build:   2026_05_29_061403_mptcp_kmemleak_v2
Userspace (running binaries):
  BRF   harness   @ 55775577b  (== c1aea0a1b after a pre-push history rewrite;
                                 tree-identical for the executor)
  stock syzkaller @ 197909be8  (latest upstream; built with the measurement shim)
Resource budget per run: 2 VMs x 4 vcpu x 3072 MB, procs=4 (identical both arms).
```

## Artifacts

- `BoF_3run_figure.png` -- the 2-panel BoF figure (primary: reject-path
  BRF-exclusive; secondary: MPTCP-scoped coverage, with whole-kernel as an
  off-axis callout).  Regenerate with `baseline/baseline_bof_figure.py`.
- Raw per-run telemetry (MIB snapshots, bench logs, crash dirs, per-run
  summaries/manifests) lives OUTSIDE this repo, in the syz_manager run dir under
  `baseline_runs_archive/iter{1,2,3}_.../` (large + crash logs -> not tracked).
- Analysis tooling: `baseline/{mib_tally,baseline_summary,baseline_plot,
  baseline_bof_figure}.py`.
