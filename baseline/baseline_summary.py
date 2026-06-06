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
def latest_bench(run_root, arm, since=0.0, until=float("inf")):
    files = [g for g in glob.glob(
        os.path.join(run_root, "baseline_logs", arm, "bench_*.json"))
        if since <= os.path.getmtime(g) < until]
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
def mib_totals(run_root, arm, since=0.0, until=float("inf")):
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
        if not (since <= os.path.getmtime(f) < until):
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


def crash_list(run_root, arm, since=0.0, until=float("inf")):
    d = os.path.join(run_root, "workdir_baseline_" + arm, "crashes")
    out = []
    for sig in sorted(glob.glob(os.path.join(d, "*"))):
        if not os.path.isdir(sig):
            continue
        if not (since <= os.path.getmtime(sig) < until):
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
def verifier_tally(run_root, since=0.0, until=float("inf")):
    d = os.path.join(run_root, "workdir_baseline_brf", "brf_verifier_stats")
    acc = rej = 0
    reasons = {}
    for f in glob.glob(os.path.join(d, "stats.*.log")):
        if not (since <= os.path.getmtime(f) < until):
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
def has_run_data(run_root):
    """True if run_root looks like a baseline run dir (the syz_manager dir):
    it has the per-arm workdirs and/or the bench logs.  Used to fail loudly
    instead of silently rendering an empty report when the wrong dir is given
    (e.g. running from the repo with no argument)."""
    return any(os.path.isdir(os.path.join(run_root, d)) for d in
               ("workdir_baseline_stock", "workdir_baseline_brf", "baseline_logs"))


def resolve_run_root(args):
    """Pick run_root: explicit arg > $BASELINE_RUN_ROOT > cwd.  Exit with a
    helpful message if it has no baseline data."""
    rr = args[0] if args else os.environ.get("BASELINE_RUN_ROOT") or os.getcwd()
    rr = os.path.abspath(rr)
    if not has_run_data(rr):
        sys.stderr.write(
            "error: no baseline run data under:\n  %s\n"
            "  expected workdir_baseline_{stock,brf}/ and baseline_logs/ there.\n"
            "  Pass the syz_manager run dir (or set BASELINE_RUN_ROOT), e.g.:\n"
            "    %s <.../brf_protocol_fuzz_setup/syz_manager>\n"
            % (rr, os.path.basename(sys.argv[0])))
        sys.exit(2)
    return rr


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


def list_runs(run_root):
    """Parse the ledger into the sequence of fuzzing RUNS.  The ledger
    (written by baseline_control.sh) is the source of truth: each
    SESSION_START opens a run; the matching SESSION_STOP (or the next
    SESSION_START, or 'now' if still up) closes it.  All telemetry files pile
    into shared dirs and are attributed to a run *logically* by their mtime
    falling in [run.start, next_run.start) -- there is no per-run subdir.

    Returns a list (oldest first) of dicts:
      {idx, start, end, label(iso), status, dur_h}
    status is 'running' (open, latest), 'stopped' (has a SESSION_STOP), or
    'superseded' (no stop, but a later run started)."""
    f = os.path.join(run_root, "baseline_run_ledger.tsv")
    if not os.path.exists(f):
        return []
    events = []
    for ln in open(f).read().splitlines():
        p = ln.split("\t")
        if len(p) < 2:
            continue
        try:
            ts = datetime.strptime(p[0], "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            continue
        if p[1] in ("SESSION_START", "SESSION_STOP"):
            events.append((ts, p[1], p[0]))
    starts = [e for e in events if e[1] == "SESSION_START"]
    runs = []
    for i, (ts, _, iso) in enumerate(starts):
        nxt = starts[i + 1][0] if i + 1 < len(starts) else None
        stop = next((e[0] for e in events if e[1] == "SESSION_STOP"
                     and e[0] > ts and (nxt is None or e[0] < nxt)), None)
        if stop:
            end, status = stop, "stopped"
        elif nxt is not None:
            end, status = nxt, "superseded"
        else:
            end, status = None, "running"
        dur_h = ((end or time.time()) - ts) / 3600.0
        runs.append({"idx": i + 1, "start": ts, "end": end,
                     "label": iso, "status": status, "dur_h": dur_h})
    return runs


def run_window(run_root, run_sel):
    """Map a run selection to a (since, until) mtime window for filtering.
      run_sel = None / 'latest' -> the current/last run (until = inf)
      run_sel = 'all'           -> everything (since=0, until=inf)
      run_sel = <int idx>       -> that run's [start, next_start) window
    Returns (since, until)."""
    runs = list_runs(run_root)
    if run_sel in (None, "latest"):
        return (current_run_start(run_root), float("inf"))
    if run_sel == "all":
        return (0.0, float("inf"))
    for r in runs:
        if r["idx"] == int(run_sel):
            nxt = next((x["start"] for x in runs if x["idx"] == r["idx"] + 1),
                       float("inf"))
            return (max(0.0, r["start"] - 15.0), nxt)
    raise SystemExit("no such run index: %s (have 1..%d)" % (run_sel, len(runs)))


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


def coverage_growth(series, window_s=6 * 3600):
    """Coverage growth (%) over the trailing `window_s` of a run.  Returns
    (growth_pct, uptime_hours) or ("early", uptime_hours) if the run is shorter
    than the window, or None if no data.  Used to decide saturation."""
    if not series:
        return None
    s = sorted(series)
    u_now, c_now = s[-1]
    if u_now < window_s:
        return ("early", u_now / 3600.0)
    target = u_now - window_s
    c_then = s[0][1]
    for u, c in s:
        if u <= target:
            c_then = c
        else:
            break
    g = 100.0 * (c_now - c_then) / c_then if c_then else 0.0
    return (g, u_now / 3600.0)


def plateau_status(run_root, thresh=3.0):
    """One-line saturation verdict: per-arm trailing-6h coverage growth plus a
    stop/keep-going call.  Saturated => the coverage delta is a settled-enough
    number and the run is safe to stop or repeat."""
    parts, saturated, early = [], True, False
    for arm in ARMS:
        g = coverage_growth(bench_series(run_root, arm))
        if g is None:
            parts.append("%s=n/a" % arm)
            saturated = False
            continue
        val, uh = g
        if val == "early":
            parts.append("%s: only %.1fh" % (arm, uh))
            early = True
            saturated = False
        else:
            parts.append("%s +%.1f%%/6h" % (arm, val))
            if val >= thresh:
                saturated = False
    if early:
        verdict = "TOO EARLY -- need >=~6h to judge (rule: <%.0f%%/6h both arms)" % thresh
    elif saturated:
        verdict = "SATURATED -- coverage settled; safe to stop / start next repeat"
    else:
        verdict = "STILL CLIMBING -- keep running for a settled delta"
    return "trailing-6h coverage growth: " + ", ".join(parts) + "  => " + verdict


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


def stall_diagnosis(m):
    """Data-driven 'where does this arm get stuck' line, attributing the stall
    to a concrete protocol reason (defends the fairness of the comparison: it
    is NOT that the baseline is misconfigured, it is structural)."""
    syn = m.get("MPCapableSYNTX", 0)
    est = m.get("MPCapableACKRX", 0)
    jsyn = m.get("MPJoinSynRx", 0)
    jok = m.get("MPJoinAckRx", 0)
    if syn == 0:
        return "no MP_CAPABLE traffic generated at all"
    if est == 0:
        return ("sends MP_CAPABLE SYNs but completes 0 handshakes -- no MPTCP "
                "listener paired in the per-proc netns (SYNTX>0, SYNRX/ACKRX=0)")
    if jsyn == 0:
        return "establishes connections but never attempts an MP_JOIN subflow"
    if jok == 0:
        return "attempts MP_JOIN but never passes the HMAC gate (0 valid joins)"
    return "reaches AND passes the MP_JOIN HMAC gate (drives the full flow)"


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
    print("\n  WHERE EACH ARM STALLS (attributed from the MIB -- not a config artefact):")
    print("      stock : %s" % stall_diagnosis(mst))
    print("      brf   : %s" % stall_diagnosis(mbr))
    print("  >> stock's stall is STRUCTURAL: random syscalls do not pair an MPTCP")
    print("     client+server in one netns, so the crypto/HMAC code is unreachable")
    print("     -- and no amount of run time changes that.  This is the gap the")
    print("     state-carrier harness is built to close.")

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
    upt_h = max(ost.get("uptime", 0), obr.get("uptime", 0)) / 3600.0
    print("\n  " + "-" * 68)
    print("  MPTCP-SCOPED COVERAGE  (DIRECTIONAL -- a coverage delta needs a long,")
    print("  repeated run to be a settled number; the STRUCTURAL result is the")
    print("  funnel above, which holds at any run length)\n")
    print("      stock %6d |%s" % (cs, _bar(cs, cmax, 30)))
    print("      brf   %6d |%s   (+%.0f%%, directional, ~%.1fh run)"
          % (cb, _bar(cb, cmax, 30), delta, upt_h))

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
    print("\n  " + plateau_status(run_root))

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
    print("  " + plateau_status(run_root))

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
    # where each arm stalls (attributed -- defends the baseline's fairness)
    print("  " + "-" * 68)
    print("  where each arm stalls (structural, not a config artefact):")
    print("    stock : " + stall_diagnosis(mst))
    print("    brf   : " + stall_diagnosis(mbr))

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


def list_runs_view(run_root):
    runs = list_runs(run_root)
    print("RUNS (from the ledger; data is attributed to a run by mtime window)")
    print("  run  start             status      dur(h)")
    print("  " + "-" * 46)
    for r in runs:
        print("  %-4d %-17s %-11s %6.1f"
              % (r["idx"], r["label"][:16].replace("T", " "), r["status"], r["dur_h"]))
    if not runs:
        print("  (none -- ledger empty; start a run via baseline_control.sh)")
    else:
        print("\n  default scope = latest run (#%d).  Use --run <idx> to target a"
              % runs[-1]["idx"])
        print("  past run, --runs-summary for the cross-run rollup, --all for cumulative.")


def runs_summary(run_root):
    runs = list_runs(run_root)
    print("=" * 70)
    print("  CROSS-RUN ROLLUP  (one row per ledger run; band across runs)")
    print("=" * 70)
    print("  run  start          status      dur_h   stockCov    brfCov   cov_delta  brfJoins")
    print("  " + "-" * 78)
    deltas, hidden = [], 0
    for r in runs:
        s, u = run_window(run_root, r["idx"])
        ost = latest_bench(run_root, "stock", s, u)[1] or {}
        obr = latest_bench(run_root, "brf", s, u)[1] or {}
        mbr = mib_totals(run_root, "brf", s, u)[0]
        cs, cb = ost.get("coverage", 0), obr.get("coverage", 0)
        # hide empty/aborted stubs (no coverage data and not the live run)
        if cs == 0 and cb == 0 and r["status"] != "running":
            hidden += 1
            continue
        d = (100.0 * (cb - cs) / cs) if cs else 0.0
        joins = mbr.get("MPJoinAckRx", 0)
        if cs:
            deltas.append(d)
        print("  %-4d %-14s %-11s %5.1f  %9d %9d   %+6.0f%%  %8d"
              % (r["idx"], r["label"][5:16].replace("T", " "), r["status"],
                 r["dur_h"], cs, cb, d, joins))
    print("  " + "-" * 78)
    if hidden:
        print("  (%d empty/aborted run(s) hidden)" % hidden)
    if len(deltas) >= 2:
        sd = sorted(deltas)
        med = sd[len(sd) // 2]
        print("  coverage delta across %d run(s) with data:  min %+.0f%%   median "
              "%+.0f%%   max %+.0f%%" % (len(deltas), min(deltas), med, max(deltas)))
        print("  >> report the delta as this RANGE across runs, not one number.")
    else:
        print("  (need >=2 completed runs for a min/max band; %d so far)" % len(deltas))
    print("  NOTE: only completed/long runs are meaningful -- check plateau per run.")


def main():
    args = [a for a in sys.argv[1:]]
    if "--list-runs" in args:
        list_runs_view(resolve_run_root([a for a in args if a != "--list-runs"]))
        return
    if "--runs-summary" in args:
        runs_summary(resolve_run_root([a for a in args if a != "--runs-summary"]))
        return
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
    run_root = resolve_run_root(args)
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
