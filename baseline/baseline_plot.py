#!/usr/bin/env python3
"""Plot the fair MPTCP coverage baseline (stock vs BRF) as a PNG dashboard.

A graphical companion to baseline_summary.py: same data, same current-run
scoping, but rendered as charts so the comparison is easy to take in at a
glance.  Reuses baseline_summary's parsers (bench series, MIB aggregation,
run scoping) -- this file only does layout.

Panels:
  1. Coverage over time      -- both arms, lines (does the gap hold/widen?)
  2. BRF/stock cov ratio     -- over time (is the lead stable, not a fluke?)
  3. MP_JOIN handshake funnel-- grouped bars; how deep each fuzzer reaches
  4. At the HMAC gate        -- pass/fail for BRF; stock never arrives

Usage:
  baseline_plot.py [run_root] [-o out.png] [--all]
    run_root : the syz_manager dir (default: cwd)
    -o       : output PNG path (default: <run_root>/baseline_plots/dashboard.png)
    --all    : cumulative across runs (default: current run only)

Needs matplotlib + numpy (already present in this env).  ASCII-only source.
"""
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")  # headless: write a file, never open a window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# import the parsers from the sibling summarizer (resolved at runtime via the
# path insert above; static checkers can't follow that -- hence type: ignore)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_summary as bs  # noqa: E402  # type: ignore[import-not-found]

STOCK_C = "#9aa0a6"   # muted gray  -- stock
BRF_C = "#0b8457"     # strong green -- BRF
FAIL_C = "#c2410c"    # orange       -- HMAC failures

STAGES = [
    ("MPCapableSYNTX", "MP_CAPABLE\nSYN sent"),
    ("MPCapableSYNACKRX", "MP_CAPABLE\nestablished"),
    ("MPJoinSynRx", "MP_JOIN SYN\n(subflow)"),
    ("MPJoinAckRx", "MP_JOIN HMAC\nvalid join"),
]


def _series_minutes(series):
    """(uptime_s, val) -> (minutes[], val[]) sorted by time."""
    s = sorted(series)
    return [u / 60.0 for u, _ in s], [v for _, v in s]


def gather(run_root, scope_all=False):
    """Pull every series/total once, so panels just draw."""
    since = 0.0 if scope_all else bs.current_run_start(run_root)
    ost = bs.latest_bench(run_root, "stock")[1] or {}
    obr = bs.latest_bench(run_root, "brf")[1] or {}
    return {
        "ost": ost, "obr": obr,
        "mst": bs.mib_totals(run_root, "stock", since)[0],
        "mbr": bs.mib_totals(run_root, "brf", since)[0],
        "ss": bs.bench_series(run_root, "stock", "filtered coverage"),
        "bsr": bs.bench_series(run_root, "brf", "filtered coverage"),
        "upt_min": max(ost.get("uptime", 0), obr.get("uptime", 0)) / 60.0,
    }


# ----- individual panels (each takes an Axes + the gathered data) ----------
def panel_coverage(a, d):
    if d["ss"]:
        x, y = _series_minutes(d["ss"])
        a.plot(x, y, color=STOCK_C, lw=2, label="stock")
        a.fill_between(x, y, color=STOCK_C, alpha=0.12)
    if d["bsr"]:
        x, y = _series_minutes(d["bsr"])
        a.plot(x, y, color=BRF_C, lw=2.2, label="BRF")
        a.fill_between(x, y, color=BRF_C, alpha=0.12)
    cs, cb = d["ost"].get("filtered coverage", 0), d["obr"].get("filtered coverage", 0)
    tcs, tcb = d["ost"].get("coverage", 0), d["obr"].get("coverage", 0)
    dlt = (100.0 * (cb - cs) / cs) if cs else 0.0
    tdlt = (100.0 * (tcb - tcs) / tcs) if tcs else 0.0
    a.set_title("MPTCP-scoped coverage (cover_filter)  BRF %+.0f%% (directional)\n"
                "[whole-kernel %+.0f%% -- NOT MPTCP cov: BRF completes connections]"
                % (dlt, tdlt), fontsize=10)
    a.set_xlabel("run time (min)")
    a.set_ylabel("MPTCP-scoped PCs (cover_filter)")
    a.legend(loc="lower right")
    a.grid(alpha=0.25)
    if cb:
        a.annotate("%d" % cb, xy=(d["upt_min"], cb), color=BRF_C,
                   fontsize=9, fontweight="bold", va="center")
    if cs:
        a.annotate("%d" % cs, xy=(d["upt_min"], cs), color="#5f6368",
                   fontsize=9, va="center")


def panel_ratio(a, d):
    ss, bsr = d["ss"], d["bsr"]
    if ss and bsr:
        umax = max([u for u, _ in ss] + [u for u, _ in bsr] + [1])
        N = 80
        sb = bs._bucketize(ss, N, umax)
        bb = bs._bucketize(bsr, N, umax)
        tx, ry = [], []
        for i in range(N):
            if sb[i] and bb[i]:
                tx.append((i + 0.5) / N * umax / 60.0)
                ry.append(bb[i] / sb[i])
        if ry:
            a.plot(tx, ry, color=BRF_C, lw=2)
            a.axhline(1.0, color="#999", ls="--", lw=1)
            a.fill_between(tx, ry, 1.0, where=[v >= 1 for v in ry],
                           color=BRF_C, alpha=0.12)
            settled = ry[len(ry) // 2:] or ry
            a.set_title("BRF / stock coverage ratio   "
                        "(settled %.2fx-%.2fx, now %.2fx)"
                        % (min(settled), max(settled), ry[-1]), fontsize=11)
            a.annotate("warmup\n(VMs booting)", xy=(tx[0], ry[0]),
                       xytext=(tx[0] + d["upt_min"] * 0.05, 0.5), fontsize=8,
                       color="#999",
                       arrowprops=dict(arrowstyle="->", color="#bbb"))
    a.set_xlabel("run time (min)")
    a.set_ylabel("coverage ratio  (1.0 = parity)")
    a.set_ylim(bottom=0)
    a.grid(alpha=0.25)


def panel_funnel(a, d):
    mst, mbr = d["mst"], d["mbr"]
    labels = [lab for _, lab in STAGES]
    sv = [mst.get(k, 0) for k, _ in STAGES]
    bv = [mbr.get(k, 0) for k, _ in STAGES]
    yp = np.arange(len(labels))
    h = 0.38
    a.barh(yp + h / 2, sv, height=h, color=STOCK_C, label="stock")
    a.barh(yp - h / 2, bv, height=h, color=BRF_C, label="BRF")
    a.set_yticks(yp)
    a.set_yticklabels(labels, fontsize=9)
    a.invert_yaxis()  # SYN at top -> deepest at bottom (funnel reads downward)
    a.set_title("How deep into the MPTCP handshake each fuzzer reaches",
                fontsize=11)
    a.set_xlabel("count (this run)")
    a.legend(loc="lower right")
    a.grid(alpha=0.25, axis="x")
    xmax = max(bv + [1])
    for i, (s, b) in enumerate(zip(sv, bv)):
        a.text(b + xmax * 0.01, yp[i] - h / 2, "%d" % b, va="center",
               fontsize=8, color=BRF_C, fontweight="bold")
        a.text(xmax * 0.01, yp[i] + h / 2,
               "%d" % s if s else "0  (never reached)", va="center",
               fontsize=8, color="#5f6368")


def panel_gate(a, d):
    mst, mbr = d["mst"], d["mbr"]
    ok = mbr.get("MPJoinAckRx", 0)
    fail = mbr.get("MPJoinAckHMacFailure", 0) + mbr.get("MPJoinSynAckHMacFailure", 0)
    s_ok = mst.get("MPJoinAckRx", 0)
    s_fail = mst.get("MPJoinAckHMacFailure", 0) + mst.get("MPJoinSynAckHMacFailure", 0)
    syn_s, est_s = mst.get("MPCapableSYNTX", 0), mst.get("MPCapableSYNACKRX", 0)
    syn_b, est_b = mbr.get("MPCapableSYNTX", 0), mbr.get("MPCapableSYNACKRX", 0)

    def pct(num, den):
        if not den:
            return "n/a"
        p = 100.0 * num / den
        return ("%.2f%%" % p) if 0 < p < 1 else ("%.0f%%" % p)

    # plot BOTH arms' real pass/fail (stock is NOT hardcoded to 0 -- long runs
    # show stock reaches the gate incidentally via kernel-PM).
    a.bar(["stock", "BRF"], [s_ok, ok], color=BRF_C, label="HMAC pass (valid join)")
    a.bar(["stock", "BRF"], [s_fail, fail], bottom=[s_ok, ok], color=FAIL_C,
          label="HMAC fail (reset/mutated path)")
    a.set_title("At the MP_JOIN HMAC gate  (security-critical crypto)", fontsize=11)
    a.set_ylabel("MP_JOIN ACKs reaching the gate")
    a.legend(loc="upper left", fontsize=9)
    a.grid(alpha=0.25, axis="y")
    top = max(ok + fail, 1)
    if s_ok + s_fail == 0:
        a.text(0, top * 0.5, "has not\nreached\nthe gate", ha="center",
               va="center", fontsize=9, color="#777")
    else:
        a.text(0, s_ok + s_fail, "  %d pass / %d fail\n  (incidental, kernel-PM)"
               % (s_ok, s_fail), ha="center", va="bottom", fontsize=8, color="#333")
    if ok + fail:
        a.text(1, ok + fail, "  %d pass / %d fail" % (ok, fail),
               ha="center", va="bottom", fontsize=9, fontweight="bold")
    a.annotate(
        "per-attempt conversion (volume-indep): stock %s vs BRF %s   |   "
        "mutated HMAC-reject path: stock %d vs BRF %d"
        % (pct(est_s, syn_s), pct(est_b, syn_b), s_fail, fail),
        xy=(0.5, -0.18), xycoords="axes fraction", ha="center", fontsize=8.5,
        color="#333")


PANELS = [
    ("coverage", panel_coverage),
    ("ratio", panel_ratio),
    ("funnel", panel_funnel),
    ("gate", panel_gate),
]


def _suptitle(fig, d):
    fig.suptitle(
        "BRF vs stock syzkaller  --  MPTCP protocol-flow fuzzing\n"
        "same kernel | same budget | same kcov instrumentation -- only the "
        "harness differs   (live ~%d min, illustrative)" % d["upt_min"],
        fontsize=13, fontweight="bold")


def plot_dashboard(run_root, out_png, scope_all=False):
    d = gather(run_root, scope_all)
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    _suptitle(fig, d)
    axes = [ax[0][0], ax[0][1], ax[1][0], ax[1][1]]
    for (_, fn), a in zip(PANELS, axes):
        fn(a, d)
    fig.tight_layout(rect=[0, 0.02, 1, 0.95])
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return [out_png]


def plot_split(run_root, out_dir, scope_all=False, stamp=""):
    """One PNG per panel -- handy for dropping a single chart into a slide."""
    d = gather(run_root, scope_all)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, fn in PANELS:
        fig, a = plt.subplots(figsize=(7, 5))
        fn(a, d)
        fig.tight_layout(rect=[0, 0.04, 1, 1])
        p = os.path.join(out_dir, "panel_%s%s.png"
                         % (name, ("_" + stamp) if stamp else ""))
        fig.savefig(p, dpi=130)
        plt.close(fig)
        written.append(p)
    return written


def main():
    args = list(sys.argv[1:])
    scope_all = "--all" in args
    if scope_all:
        args.remove("--all")
    split = "--split" in args
    if split:
        args.remove("--split")
    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1])  # minutes
        del args[i:i + 2]
    out = None
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        del args[i:i + 2]
    run_root = bs.resolve_run_root(args)

    def once():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        if split:
            outdir = out or os.path.join(run_root, "baseline_plots")
            paths = plot_split(run_root, os.path.abspath(outdir), scope_all, stamp)
        else:
            png = out or os.path.join(run_root, "baseline_plots",
                                      "dashboard_%s.png" % stamp)
            paths = plot_dashboard(run_root, os.path.abspath(png), scope_all)
        for p in paths:
            print("wrote %s" % p)

    if watch:
        try:
            while True:
                once()
                print("(next refresh in %d min -- Ctrl-C to stop)" % watch)
                time.sleep(watch * 60)
        except KeyboardInterrupt:
            pass
    else:
        once()


if __name__ == "__main__":
    main()
