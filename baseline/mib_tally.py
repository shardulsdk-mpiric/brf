#!/usr/bin/env python3
"""Tally MPTCP MIB telemetry from a baseline arm's mib_stats 9p dir.

The executors append, every >=10s, this VM's MPTcpExt counters from
/proc/net/netstat to mib.<boot-id>.log, as records:

    <ts_ms> proc=<procid> MPTcpExt: <fields...>

For each snapshot the executor writes TWO such lines -- the header (counter
NAMES) and the values (numbers).  Counters are per-proc-netns and cumulative
within a netns, but an executor restart recreates the netns and resets the
counters to 0 within the same (file, proc) series.  So the arm total = sum,
over each distinct (file, proc), of the PEAK of every monotonic segment (a
value dropping below the running max marks a netns reset).  With no resets this
is just the latest value.

Usage: mib_tally.py <mib_stats_dir>   (e.g. .../workdir_baseline_stock/mib_stats)
"""
import sys
import os
import glob

# Counters most relevant to the crypto-gate story (printed first); the rest
# are summed and printed too.
KEY = [
    "MPJoinSynRx", "MPJoinAckRx",
    "MPJoinSynAckHMacFailure", "MPJoinAckHMacFailure",
    "MPJoinSynAckNoMPJoin", "MPJoinAckNoMPJoin", "MPJoinNoTokenFound",
]


def is_values(rest_tokens):
    return bool(rest_tokens) and all(t.lstrip("-").isdigit() for t in rest_tokens)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: mib_tally.py <mib_stats_dir>")
    d = sys.argv[1]
    header = None                 # list of counter names (stable per kernel)
    # (file, proc) -> [acc_list, segmax_list].  Counters are cumulative within a
    # netns, but an executor restart recreates the netns (fresh unshare NEWNET)
    # and resets the counters back to 0 *within the same (file, proc) series*.
    # So instead of the latest value we sum the PEAK of each monotonic segment
    # (a value dropping below the running max marks a reset).  No resets => this
    # equals the latest value; with resets it correctly adds every generation.
    # Valid for monotonic event counters; gauges (e.g. MPCurrEstab) may be
    # over-counted but none are in the KEY crypto-gate set.
    segs = {}
    files = sorted(glob.glob(os.path.join(d, "mib.*.log")))
    if not files:
        sys.exit("no mib.*.log files in %s (was the run started with the MIB build?)" % d)
    for fn in files:
        with open(fn, errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 4 or parts[1][:5] != "proc=" or parts[2] != "MPTcpExt:":
                    continue
                proc = parts[1][5:]
                rest = parts[3:]
                if is_values(rest):
                    vals = [int(x) for x in rest]
                    g = segs.get((fn, proc))
                    if g is None:
                        g = [[0] * len(vals), [0] * len(vals)]
                        segs[(fn, proc)] = g
                    acc, segmax = g
                    for i, v in enumerate(vals):
                        if i >= len(segmax):
                            break
                        if v < segmax[i]:        # reset -> bank finished segment
                            acc[i] += segmax[i]
                        segmax[i] = v
                elif header is None:
                    header = rest
    if header is None:
        sys.exit("no MPTcpExt header line found")
    totals = [0] * len(header)
    for acc, segmax in segs.values():
        for i in range(min(len(acc), len(totals))):
            totals[i] += acc[i] + segmax[i]    # banked segments + open segment
    idx = {name: i for i, name in enumerate(header)}
    print("# arm dir: %s" % d)
    print("# distinct (boot, proc) netns sampled: %d across %d VM-boot file(s)"
          % (len(segs), len(files)))
    print("# --- key crypto-gate counters ---")
    for name in KEY:
        if name in idx:
            print("%-28s %d" % (name, totals[idx[name]]))
    print("# --- all MPTcpExt counters (cumulative, summed across netns) ---")
    for name in header:
        print("%-28s %d" % (name, totals[idx[name]]))


if __name__ == "__main__":
    main()
