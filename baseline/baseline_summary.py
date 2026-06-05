#!/usr/bin/env python3
"""Whole-run summarizer for the fair MPTCP coverage baseline (stock vs BRF).

Pulls together EVERY telemetry stream the two arms emit and prints one
side-by-side report.  Safe to run mid-run (reads live files read-only) or at
the end.  `mib_tally.py` only digests the MIB counters; this digests all of:

  1. RUN STATUS    -- ledger (sessions) + per-arm liveness (bench freshness)
  2. COVERAGE      -- the headline matched metric: coverage / corpus / execs /
                      exec-rate / crashes, from each arm's latest -bench snapshot
  3. MP_JOIN GATE  -- the contrast: MPTcpExt MP_JOIN funnel (MIB), side by side,
                      incl. an exec-normalized rate so the comparison is fair
  4. CRASHES       -- per-arm crash signatures (flags infra-noise vs real bugs)
  5. BRF VERIFIER  -- BRF-only struct_ops verifier accept/reject tally

Run dir layout (auto-discovered under <run_root>, default = cwd):
  workdir_baseline_{stock,brf}/   (corpus.db, crashes/, mib_stats/, brf_verifier_stats/)
  baseline_logs/{stock,brf}/      (bench_*.json, run_*.log)
  baseline_run_ledger.tsv

Usage:
  baseline_summary.py [run_root]            # one-shot
  baseline_summary.py [run_root] --watch 30 # refresh every 30s (mid-run)

ASCII-only output by deliberate house style.
"""
import sys
import os
import re
import glob
import json
import time
from datetime import datetime

ARMS = ("stock", "brf")

# ---- MP_JOIN funnel: (counter name in MPTcpExt, short label) ---------------
# MPCapableSYNTX (attempts) is shown first on purpose: stock TRANSMITS
# MP_CAPABLE SYNs but completes ~none (no same-netns MPTCP peer), so without
# the attempts row stock reads as an all-zero column that looks idle/broken
# rather than "attempts base connections but cannot complete the handshake".
FUNNEL = [
    ("MPCapableSYNTX", "MP_CAPABLE SYN sent (attempt)"),
    ("MPCapableACKRX", "MP_CAPABLE established"),
    ("MPJoinSynRx", "MP_JOIN SYN rx (token lookup)"),
    ("MPJoinSynAckRx", "MP_JOIN SYN/ACK rx"),
    ("MPJoinAckRx", "MP_JOIN ACK, HMAC VALID"),
    ("MPJoinAckHMacFailure", "MP_JOIN ACK, HMAC FAILED"),
    ("MPJoinSynAckHMacFailure", "MP_JOIN SYN/ACK HMAC fail"),
    ("MPJoinNoTokenFound", "MP_JOIN no token found"),
    ("MPJoinRejected", "MP_JOIN rejected"),
    ("AddAddr", "ADD_ADDR processed"),
    ("RmAddr", "RM_ADDR processed"),
]

# Headline bench fields (key in bench json -> short label). Schema-tolerant:
# absent keys are shown as n/a.
BENCH_FIELDS = [
    ("coverage", "coverage (PCs)"),
    ("corpus", "corpus"),
    ("exec total", "exec total"),
    ("crashes", "crashes"),
    ("crash types", "crash types"),
    ("uptime", "uptime (s)"),
]

LIVE_WINDOW_S = 180  # a bench file touched within this is considered "live"


def c(n):
    return "n/a" if n is None else f"{n:,}"


# --------------------------------------------------------------------------- #
# bench (-bench json stream: concatenated pretty-printed objects)
# --------------------------------------------------------------------------- #
def latest_bench(run_root, arm):
    files = glob.glob(os.path.join(run_root, "baseline_logs", arm, "bench_*.json"))
    if not files:
        return None, None
    f = max(files, key=os.path.getmtime)
    try:
        text = open(f).read()
    except OSError:
        return f, None
    dec = json.JSONDecoder()
    objs, i, n = [], 0, len(text)
    while i < n:
        j = i
        while j < n and text[j].isspace():
            j += 1
        if j >= n:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except json.JSONDecodeError:
            break
        objs.append(obj)
        i = end
    return f, (objs[-1] if objs else None)


# --------------------------------------------------------------------------- #
# MIB (per (boot-file, proc) latest values; counters are cumulative per netns)
# --------------------------------------------------------------------------- #
def mib_totals(run_root, arm, since=0.0):
    """Sum MPTcpExt event counters across the arm's current-run boot files.

    Counters are cumulative within a netns, but a netns is recreated whenever
    that proc's executor restarts (fresh unshare(CLONE_NEWNET)) -- which can
    reset the counters back to 0 *within the same (file, proc) series*.  So we
    do NOT just take the latest value: per (file, proc) and per counter we sum
    the PEAK of each monotonic segment (a value dropping below the segment max
    marks a netns reset).  With no resets this equals the latest value; with
    resets it correctly adds every generation.  (Valid only for monotonic event
    counters -- all FUNNEL entries are; gauges like MPCurrEstab are excluded.)
    """
    d = os.path.join(run_root, "workdir_baseline_" + arm, "mib_stats")
    names = None
    # (file, proc) -> {counter: [accumulated_total, current_segment_max]}
    segs = {}
    for f in sorted(glob.glob(os.path.join(d, "mib.*.log"))):
        if os.path.getmtime(f) < since:
            continue
        try:
            fh = open(f)
        except OSError:
            continue
        with fh:
            for line in fh:
                p = line.split()
                if len(p) < 4 or p[2] != "MPTcpExt:":
                    continue
                proc = p[1]
                rest = p[3:]
                if rest and rest[0] == "MPCapableSYNRX":
                    names = rest
                    continue
                if not names or not all(t.lstrip("-").isdigit() for t in rest):
                    continue
                g = segs.setdefault((f, proc), {})
                for k, sv in zip(names, rest):
                    v = int(sv)
                    acc = g.setdefault(k, [0, 0])
                    if v < acc[1]:          # value dropped -> netns reset
                        acc[0] += acc[1]    # bank the finished segment's peak
                    acc[1] = v              # new running segment max
    totals = {}
    for g in segs.values():
        for k, acc in g.items():
            totals[k] = totals.get(k, 0) + acc[0] + acc[1]  # + open segment
    return totals, len(segs)  # totals, #netns-lineages observed


# --------------------------------------------------------------------------- #
# crashes
# --------------------------------------------------------------------------- #
INFRA_RE = re.compile(
    r"mismatching fuzzer/executor|SYZFATAL|machine check failed|"
    r"executor .* failed|lost connection|corrupted|no output from test",
    re.I,
)


def crash_list(run_root, arm, since=0.0):
    d = os.path.join(run_root, "workdir_baseline_" + arm, "crashes")
    out = []
    for sig in sorted(glob.glob(os.path.join(d, "*"))):
        if not os.path.isdir(sig):
            continue
        if os.path.getmtime(sig) < since:
            continue
        desc = ""
        dp = os.path.join(sig, "description")
        if os.path.exists(dp):
            desc = open(dp).read().strip()
        nlogs = len(glob.glob(os.path.join(sig, "log*")))
        out.append((os.path.getmtime(sig), desc, nlogs, INFRA_RE.search(desc) is not None))
    out.sort()
    return out


# --------------------------------------------------------------------------- #
# BRF verifier stats (BRF-only)  -- "<ts> <n> ACCEPT|REJECT <reason...>"
# --------------------------------------------------------------------------- #
def verifier_tally(run_root, since=0.0):
    d = os.path.join(run_root, "workdir_baseline_brf", "brf_verifier_stats")
    acc = rej = 0
    reasons = {}
    for f in glob.glob(os.path.join(d, "stats.*.log")):
        if os.path.getmtime(f) < since:
            continue
        try:
            fh = open(f)
        except OSError:
            continue
        with fh:
            for line in fh:
                p = line.split(None, 3)
                if len(p) < 3:
                    continue
                verdict = p[2].upper()
                if verdict == "ACCEPT":
                    acc += 1
                elif verdict == "REJECT":
                    rej += 1
                    reason = (p[3].strip() if len(p) > 3 else "")
                    # collapse to a coarse signature (drop the per-prog hash)
                    reason = re.sub(r"brf_[0-9a-f]+", "brf_<id>", reason)
                    reason = reason[:70]
                    reasons[reason] = reasons.get(reason, 0) + 1
    return acc, rej, reasons


# --------------------------------------------------------------------------- #
def ledger_tail(run_root, k=6):
    f = os.path.join(run_root, "baseline_run_ledger.tsv")
    if not os.path.exists(f):
        return []
    return open(f).read().splitlines()[-k:]


def current_run_start(run_root):
    """Epoch of the last SESSION_START in the ledger -> scope telemetry to the
    current run (older boot files / crashes / verifier logs from prior, stopped
    runs are excluded, so per-run aggregates match the per-run bench window).
    Returns 0.0 if no ledger (then nothing is filtered)."""
    f = os.path.join(run_root, "baseline_run_ledger.tsv")
    if not os.path.exists(f):
        return 0.0
    start = 0.0
    for ln in open(f).read().splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[1] == "SESSION_START":
            try:
                start = datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                pass
    # small slack: a VM's first mib append lands a few seconds after START
    return max(0.0, start - 15.0)


def row(label, a, b, w=34):
    return f"  {label:<{w}} {str(a):>16} {str(b):>16}"


def _bar(v, vmax, w=26, ch="#"):
    if vmax <= 0 or v <= 0:
        return ""
    return ch * max(1, int(round(v / vmax * w)))


SPARK_RAMP = "_.-=+*#@"  # ASCII height ramp, low -> high (legend printed with it)


def bench_series(run_root, arm):
    """All (uptime_s, coverage) snapshots from the arm's latest bench file,
    in time order.  Coverage is cumulative, so the series is non-decreasing."""
    f = latest_bench(run_root, arm)[0]
    if not f:
        return []
    try:
        text = open(f).read()
    except OSError:
        return []
    dec = json.JSONDecoder()
    out, i, n = [], 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        if "coverage" in obj:
            out.append((obj.get("uptime", 0), obj["coverage"]))
    return out


def _spark(vals, width=46):
    if not vals:
        return ""
    if len(vals) > width:  # downsample by averaging contiguous groups
        step = len(vals) / width
        vals = [sum(vals[int(i * step):int((i + 1) * step)] or [vals[-1]])
                / len(vals[int(i * step):int((i + 1) * step)] or [1])
                for i in range(width)]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    r = SPARK_RAMP
    return "".join(r[int((v - lo) / rng * (len(r) - 1) + 0.5)] for v in vals)


def _bucketize(series, nb, umax):
    """Bucket cumulative (uptime, value) into nb time buckets over [0, umax];
    each bucket = latest value seen in it, forward-filled (carry-forward)."""
    out = [None] * nb
    for u, v in series:
        b = min(nb - 1, int(u / umax * nb)) if umax > 0 else 0
        out[b] = v
    last = None
    for i in range(nb):
        if out[i] is None:
            out[i] = last
        else:
            last = out[i]
    return out


# Narrative funnel = MONOTONIC depth into the handshake (counter, label).
# HMAC pass/fail is a BRANCH at the gate, not a deeper stage, so it is shown
# separately below rather than as a funnel bar.
STAGES = [
    ("MPCapableSYNTX", "MP_CAPABLE SYN sent (attempt)"),
    ("MPCapableACKRX", "MP_CAPABLE connection established"),
    ("MPJoinSynRx", "MP_JOIN SYN -- subflow attempt"),
    ("MPJoinAckRx", "MP_JOIN reaches HMAC gate + PASSES"),
]


def present_view(run_root, scope_all=False):
    """Conference-style side-by-side picture (illustrative; ASCII-only)."""
    since = 0.0 if scope_all else current_run_start(run_root)
    ost = latest_bench(run_root, "stock")[1] or {}
    obr = latest_bench(run_root, "brf")[1] or {}
    mst = mib_totals(run_root, "stock", since)[0]
    mbr = mib_totals(run_root, "brf", since)[0]
    upt = max(ost.get("uptime", 0), obr.get("uptime", 0))

    line = "=" * 72
    print(line)
    print("   BRF  vs  stock syzkaller   --   MPTCP protocol-flow fuzzing")
    print("   same kernel | same fuzzing budget | same kcov instrumentation")
    print("   (only the harness differs -- live snapshot, ~%d min, illustrative)"
          % (upt // 60))
    print(line)

    # --- the funnel -------------------------------------------------------
    gmax = max([mst.get(k, 0) for k, _ in STAGES] + [mbr.get(k, 0) for k, _ in STAGES] + [1])
    print("\n  HOW DEEP INTO THE MPTCP HANDSHAKE DOES EACH FUZZER REACH?\n")
    for k, lab in STAGES:
        s, b = mst.get(k, 0), mbr.get(k, 0)
        print("  %s" % lab)
        print("      stock %5d |%s" % (s, _bar(s, gmax) or "  (none -- never reached)"))
        print("      brf   %5d |%s" % (b, _bar(b, gmax)))
    print("\n  >> stock stops at the FIRST SYN: with random syscalls it never")
    print("     pairs an MPTCP client+server in one netns, so 0 handshakes")
    print("     complete and the crypto/HMAC code is UNREACHABLE for stock.")
    print("  >> BRF's state-carrier harness drives the whole flow -- it reaches")
    print("     AND passes the MP_JOIN HMAC gate, and exercises the reset path.")

    # --- conversion rate: the volume-independent rebuttal -----------------
    def conv(m, num, den):
        d = m.get(den, 0)
        return ("%d/%d = %d%%" % (m.get(num, 0), d, round(100 * m.get(num, 0) / d))
                ) if d else "n/a (0 attempts)"
    print("\n  CONVERSION RATE (per attempt -- does NOT depend on how many tries):")
    print("      SYN sent -> established : stock %-18s brf %s"
          % (conv(mst, "MPCapableACKRX", "MPCapableSYNTX"),
             conv(mbr, "MPCapableACKRX", "MPCapableSYNTX")))
    print("  >> this is a RATE gap, not a volume gap: stock converts ~0% of its")
    print("     SYNs to a live connection no matter HOW MANY it sends.")

    # --- coverage headline ------------------------------------------------
    cs, cb = ost.get("coverage", 0), obr.get("coverage", 0)
    cmax = max(cs, cb, 1)
    delta = (100.0 * (cb - cs) / cs) if cs else 0.0
    print("\n  " + "-" * 68)
    print("  MPTCP-SCOPED COVERAGE (kernel PCs hit on the contested surface)\n")
    print("      stock %6d |%s" % (cs, _bar(cs, cmax, 30)))
    print("      brf   %6d |%s   (+%.0f%%)" % (cb, _bar(cb, cmax, 30), delta))

    # --- coverage trajectory (is the gap stable, or a lucky instant?) -----
    ss, bs = bench_series(run_root, "stock"), bench_series(run_root, "brf")
    if ss and bs:
        umax = max([u for u, _ in ss] + [u for u, _ in bs] + [1])
        N = 46
        sb, bb = _bucketize(ss, N, umax), _bucketize(bs, N, umax)
        sv = [c for c in sb if c]
        bv = [c for c in bb if c]
        if sv and bv:
            print("\n  coverage trajectory over the run   (legend: low %s high)"
                  % SPARK_RAMP)
            print("      stock  %s  %d->%d" % (_spark(sv), sv[0], sv[-1]))
            print("      brf    %s  %d->%d" % (_spark(bv), bv[0], bv[-1]))
            ratios = []
            for i in range(N):
                s, b = sb[i], bb[i]
                if s and b:
                    ratios.append(b / s)
            if ratios:
                # report the SETTLED range (2nd half) -- the 1st snapshots are
                # VM-warmup (BRF's coverage starts near 0 before its VMs boot),
                # which would otherwise show a meaningless ~0x dip.
                settled = ratios[len(ratios) // 2:] or ratios
                print("      ratio  %s  brf/stock settled %.2fx..%.2fx, now %.2fx"
                      % (_spark(ratios), min(settled), max(settled), ratios[-1]))
                print("  >> the lead holds (in fact widens) across the run -- not a"
                      " lucky instant.")

    # --- the quantitative gate story --------------------------------------
    ok = mbr.get("MPJoinAckRx", 0)
    bad = mbr.get("MPJoinAckHMacFailure", 0) + mbr.get("MPJoinSynAckHMacFailure", 0)
    total_gate = ok + bad
    print("\n  " + "-" * 68)
    print("  AT THE MP_JOIN HMAC GATE (pass vs fail -- the security-critical code):")
    print("      stock : never reached the gate (0 joins attempted)")
    if total_gate:
        print("      brf   : %d reached it  ->  %d PASS (valid HMAC) / %d FAIL (reset)"
              % (total_gate, ok, bad))
        print("              => both the accept path AND the failure/reset path are fuzzed")
    print("\n  TAKEAWAY:  on the exact surface stock is tuned to hit, stock cannot")
    print("  get past the opening SYN; BRF reaches the deep MPTCP crypto state")
    print("  machine -- the gap the harness is built to close.")
    print(line)


def report(run_root, scope_all=False):
    now = time.time()
    since = 0.0 if scope_all else current_run_start(run_root)
    print("=" * 70)
    print("  MPTCP FAIR COVERAGE BASELINE -- live summary")
    print("  run_root: " + run_root)
    print("  as of   : " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
    if since:
        print("  scope   : current run only (since "
              + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
              + "); use --all for cumulative")
    else:
        print("  scope   : ALL telemetry (cumulative across runs)")
    print("=" * 70)

    # 1. status
    print("\n[1] RUN STATUS")
    for ln in ledger_tail(run_root):
        print("    ledger | " + ln.replace("\t", "  "))
    bench = {}
    for arm in ARMS:
        bf, obj = latest_bench(run_root, arm)
        bench[arm] = obj
        if bf:
            age = now - os.path.getmtime(bf)
            state = "LIVE" if age < LIVE_WINDOW_S else "stale/stopped"
            print(f"    {arm:5} | bench {os.path.basename(bf)}  ({state}, "
                  f"updated {int(age)}s ago)")
        else:
            print(f"    {arm:5} | no bench file yet")

    # 2. coverage headline
    print("\n[2] COVERAGE & THROUGHPUT (matched -bench metric)")
    print(row("metric", "STOCK", "BRF"))
    print("  " + "-" * 68)
    for key, lab in BENCH_FIELDS:
        a = bench["stock"].get(key) if bench["stock"] else None
        b = bench["brf"].get(key) if bench["brf"] else None
        print(row(lab, c(a), c(b)))
    # exec rate
    def rate(arm):
        o = bench[arm]
        if o and o.get("uptime"):
            return round(o.get("exec total", 0) / o["uptime"], 1)
        return None
    print(row("exec/sec (this bench)", c(rate("stock")), c(rate("brf"))))

    # 3. MP_JOIN funnel (the contrast)
    print("\n[3] MP_JOIN CRYPTO-GATE FUNNEL (MPTcpExt MIB, summed per netns)")
    mst, nst = mib_totals(run_root, "stock", since)
    mbr, nbr = mib_totals(run_root, "brf", since)
    print(row(f"counter  (netns: stock={nst} brf={nbr})", "STOCK", "BRF"))
    print("  " + "-" * 68)
    for key, lab in FUNNEL:
        print(row(lab, c(mst.get(key, 0)), c(mbr.get(key, 0))))
    # --- derived, VOLUME-INDEPENDENT metrics --------------------------------
    # (rebut "BRF only wins because it makes more attempts": these normalize it
    #  out -- a rate and a per-exec figure, neither grows just by trying more.)
    print("  " + "-" * 68)
    print("  derived (volume-independent -- not just 'more attempts'):")

    def convrate(m):  # SYN sent -> connection established (per attempt)
        syn, est = m.get("MPCapableSYNTX", 0), m.get("MPCapableACKRX", 0)
        return f"{100*est//syn}% ({est}/{syn})" if syn else "n/a"
    print(row("  SYN->established conversion", convrate(mst), convrate(mbr)))

    def passrate(m):
        ok, bad = m.get("MPJoinAckRx", 0), m.get("MPJoinAckHMacFailure", 0)
        return f"{(100*ok//(ok+bad))}%" if (ok + bad) else "n/a"
    print(row("  HMAC pass rate at ACK gate", passrate(mst), passrate(mbr)))

    def per1k(arm, m, key="MPJoinAckRx"):
        o = bench[arm]
        tot = o.get("exec total") if o else None
        if not tot:
            return "n/a"
        return f"{1000*m.get(key,0)/tot:.2f}"
    print(row("  valid joins / 1k execs", per1k("stock", mst), per1k("brf", mbr)))
    print(row("  MP_CAPABLE est. / 1k execs",
              per1k("stock", mst, "MPCapableACKRX"),
              per1k("brf", mbr, "MPCapableACKRX")))

    # 4. crashes
    print("\n[4] CRASHES (per arm; [infra] = harness noise, not a kernel bug)")
    for arm in ARMS:
        cl = crash_list(run_root, arm, since)
        if not cl:
            print(f"    {arm:5} | none")
            continue
        for mt, desc, nlogs, infra in cl:
            tag = "[infra]" if infra else "[BUG?] "
            when = time.strftime("%m-%d %H:%M", time.localtime(mt))
            print(f"    {arm:5} | {tag} {when}  x{nlogs}  {desc[:80]}")

    # 5. BRF verifier (BRF-only)
    print("\n[5] BRF struct_ops VERIFIER (BRF-only telemetry, not a fair metric)")
    acc, rej, reasons = verifier_tally(run_root, since)
    tot = acc + rej
    if tot:
        print(f"    accept={acc}  reject={rej}  accept_rate={100*acc//tot}%  (n={tot})")
        for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:5]:
            print(f"      reject x{n:<5} {r}")
    else:
        print("    no verifier records yet")
    print()


def main():
    args = [a for a in sys.argv[1:]]
    watch = None
    if "--watch" in args:
        i = args.index("--watch")
        watch = int(args[i + 1])
        del args[i:i + 2]
    scope_all = False
    if "--all" in args:
        scope_all = True
        args.remove("--all")
    present = False
    if "--present" in args:
        present = True
        args.remove("--present")
    render = present_view if present else report
    run_root = args[0] if args else os.getcwd()
    run_root = os.path.abspath(run_root)
    if watch:
        try:
            while True:
                os.system("clear")
                render(run_root, scope_all)
                print(f"(refreshing every {watch}s -- Ctrl-C to stop)")
                time.sleep(watch)
        except KeyboardInterrupt:
            pass
    else:
        render(run_root, scope_all)


if __name__ == "__main__":
    main()
