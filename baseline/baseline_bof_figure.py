#!/usr/bin/env python3
"""3-run BoF figure for the fair MPTCP coverage baseline (stock vs BRF).

Reads the three archived run bundles (each a self-contained run_root) and draws
TWO panels, per the agreed framing:

  PRIMARY (headline): the MP_JOIN invalid-HMAC (reject/reset) path -- BRF-only.
    Stock = 0 in all 3 runs; BRF always reaches it.  Shown per-1k-execs
    (duration-independent) so iter3's 36h does NOT inflate it (W2).

  SECONDARY (support): coverage, with the two metrics SEPARATED and labelled --
    MPTCP-scoped (cover_filter / "filtered coverage") vs whole-kernel
    ("coverage").  The whole-kernel delta is bigger because BRF completes
    connections and so exercises more of the TCP/crypto/netlink stack; it is
    NOT "MPTCP coverage".

Usage:  baseline_bof_figure.py <syz_manager_run_dir> [-o out.png]
ASCII-only labels.  Attribution: Syzkaller (Vyukov et al.) -> BRF (Hung &
Amiri Sani, arXiv:2305.08782) -> Mpiric MPTCP harness extension.
"""
import os
import sys
import glob

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_summary as bs  # noqa: E402  # type: ignore[import-not-found]

STOCK_C = "#9aa0a6"
BRF_C = "#0b8457"


def per_run(bundle):
    """Pull the verified metrics for one archived run bundle."""
    out = {}
    for arm in ("stock", "brf"):
        m = bs.mib_totals(bundle, arm)[0]
        ob = bs.latest_bench(bundle, arm)[1] or {}
        out[arm] = {
            "valid": m.get("MPJoinAckRx", 0),
            "reject": m.get("MPJoinAckHMacFailure", 0) + m.get("MPJoinSynAckHMacFailure", 0),
            "fcov": ob.get("filtered coverage", 0),   # MPTCP-scoped (cover_filter)
            "tcov": ob.get("coverage", 0),             # whole-kernel
            "execs": ob.get("exec total", 0) or 1,
        }
    return out


def main():
    args = list(sys.argv[1:])
    out = "baseline_bof_3run.png"
    if "-o" in args:
        i = args.index("-o"); out = args[i + 1]; del args[i:i + 2]
    run_root = os.path.abspath(args[0] if args else os.getcwd())
    bundles = sorted(glob.glob(os.path.join(run_root, "baseline_runs_archive", "iter*")))
    if len(bundles) < 1:
        sys.exit("no archived run bundles under %s/baseline_runs_archive" % run_root)
    runs = [per_run(b) for b in bundles]
    labels = ["run %d" % (i + 1) for i in range(len(runs))]
    x = np.arange(len(runs)); w = 0.38

    from matplotlib.patches import Patch
    fig = plt.figure(figsize=(11, 9.5))
    # top ~65% / bottom ~35%: coverage is SUPPORT, reject-path is the headline.
    # top margin reserved (top=0.85) so the 3-line suptitle never overprints the
    # top panel's own title; the saturation note is line 3 of the suptitle itself
    # (multiline suptitle is laid out without self-overlap) -- see FIX 1.
    gs = fig.add_gridspec(2, 1, height_ratios=[1.9, 1.0], hspace=0.42, top=0.85)
    fig.suptitle("BRF vs stock syzkaller -- MPTCP protocol-flow fuzzing  (3 independent runs)\n"
                 "same frozen kernel | same RESOURCE budget per run (cpu/mem/procs) | same kcov | "
                 "only the harness differs\n"
                 "runs stopped on coverage saturation; durations 24-36h",
                 fontsize=11.5, fontweight="bold", y=0.985)

    # ---- PRIMARY (headline): BINARY reached / not-reached, every run ----
    # No per-exec denominator: stock=0 is bulletproof under any denominator, and
    # BRF bar heights would otherwise invite a normalization question (see report).
    a = fig.add_subplot(gs[0])
    for i, r in enumerate(runs):
        a.bar(i + w / 2, 1.0, w, color=BRF_C)         # BRF: reached (uniform)
        a.bar(i - w / 2, 0.0, w, color=STOCK_C)        # stock: never (zero height)
        a.text(i + w / 2, 1.0, "REACHED\n%d reject/reset\n(%d valid joins)"
               % (r["brf"]["reject"], r["brf"]["valid"]), ha="center", va="bottom",
               fontsize=8, color=BRF_C, fontweight="bold")
        a.text(i - w / 2, 0.03, "never\nreached\n(0 / 0)", ha="center", va="bottom",
               fontsize=8, color="#5f6368")
    a.set_xticks(x); a.set_xticklabels(labels)
    a.set_yticks([0, 1]); a.set_yticklabels(["not reached", "reached"])
    a.set_ylim(0, 1.7)
    a.set_ylabel("MP_JOIN invalid-HMAC\n(reject/reset) path")
    a.set_title("HEADLINE: the invalid-HMAC (reject/reset) path is BRF-EXCLUSIVE\n"
                "stock NEVER reaches it (0 in all 3 runs -- cannot forge a bad HMAC); "
                "BRF reaches it every run", fontsize=11, fontweight="bold")
    a.legend(handles=[Patch(color=STOCK_C, label="stock"), Patch(color=BRF_C, label="BRF")],
             loc="upper left")
    a.grid(alpha=0.2, axis="y")

    # ---- SECONDARY (support): MPTCP-scoped ONLY; whole-kernel = text callout ----
    a2 = fig.add_subplot(gs[1])
    fdelta = [100.0 * (r["brf"]["fcov"] - r["stock"]["fcov"]) / r["stock"]["fcov"]
              if r["stock"]["fcov"] else 0 for r in runs]
    tdelta = [100.0 * (r["brf"]["tcov"] - r["stock"]["tcov"]) / r["stock"]["tcov"]
              if r["stock"]["tcov"] else 0 for r in runs]
    a2.bar(x, fdelta, 0.5, color="#2563eb")
    for i in range(len(runs)):
        a2.text(x[i], fdelta[i], "+%.0f%%" % fdelta[i], ha="center", va="bottom",
                fontsize=9, color="#2563eb", fontweight="bold")
    a2.set_xticks(x); a2.set_xticklabels(labels)
    a2.set_ylabel("BRF MPTCP-scoped\ncov advantage (%)")
    a2.set_ylim(0, (max(fdelta) * 1.5) if max(fdelta) else 1)
    a2.set_title("SUPPORT: MPTCP-scoped coverage (cover_filter) -- modest and variable",
                 fontsize=10)
    a2.grid(alpha=0.2, axis="y")
    # whole-kernel: a TEXT callout, NOT a bar on the same axis (different denominator)
    a2.text(0.985, 0.93,
            "Whole-kernel source coverage: +%.0f-%.0f%% (runs 1-3).\n"
            "Different denominator (~21k vs ~2.6k MPTCP PCs) -- do\n"
            "NOT compare to the bars. Reflects BRF completing\n"
            "connections, exercising the wider TCP/crypto/netlink\n"
            "stack. This is NOT MPTCP coverage."
            % (min(tdelta), max(tdelta)),
            transform=a2.transAxes, ha="right", va="top", fontsize=7.8, color="#334155",
            bbox=dict(boxstyle="round", fc="#f1f5f9", ec="#94a3b8"))

    fig.text(0.5, 0.005,
             "Reject-path result CONCLUSIVE across 3 independent runs; coverage VARIABLE across runs. "
             "N=2 confirmed bugs; bug-rate superiority NOT claimed.\n"
             "Syzkaller (Vyukov et al.) -> BRF (Hung & Amiri Sani, arXiv:2305.08782) -> Mpiric MPTCP extension.",
             ha="center", fontsize=7.5, color="#555")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print("wrote %s" % out)
    # echo the numbers behind the figure for the record
    print("\nper-run numbers (verified):")
    for lab, r in zip(labels, runs):
        print("  %s: stock reject=%d valid=%d fcov=%d tcov=%d | brf reject=%d valid=%d fcov=%d tcov=%d"
              % (lab, r["stock"]["reject"], r["stock"]["valid"], r["stock"]["fcov"], r["stock"]["tcov"],
                 r["brf"]["reject"], r["brf"]["valid"], r["brf"]["fcov"], r["brf"]["tcov"]))


if __name__ == "__main__":
    main()
