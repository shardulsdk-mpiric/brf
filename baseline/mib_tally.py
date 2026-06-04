#!/usr/bin/env python3
"""Tally MPTCP MIB telemetry from a baseline arm's mib_stats 9p dir.

The executors append, every >=10s, this VM's MPTcpExt counters from
/proc/net/netstat to mib.<boot-id>.log, as records:

    <ts_ms> proc=<procid> MPTcpExt: <fields...>

For each snapshot the executor writes TWO such lines -- the header (counter
NAMES) and the values (numbers).  Counters are per-proc-netns and cumulative
within a VM-boot, so the arm total = sum over each distinct (file, proc) of
that proc's LATEST values line.

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
    latest = {}                   # (file, proc) -> list[int] latest values
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
                    latest[(fn, proc)] = [int(x) for x in rest]
                elif header is None:
                    header = rest
    if header is None:
        sys.exit("no MPTcpExt header line found")
    totals = [0] * len(header)
    for vals in latest.values():
        for i in range(min(len(vals), len(totals))):
            totals[i] += vals[i]
    idx = {name: i for i, name in enumerate(header)}
    print("# arm dir: %s" % d)
    print("# distinct (boot, proc) netns sampled: %d across %d VM-boot file(s)"
          % (len(latest), len(files)))
    print("# --- key crypto-gate counters ---")
    for name in KEY:
        if name in idx:
            print("%-28s %d" % (name, totals[idx[name]]))
    print("# --- all MPTcpExt counters (cumulative, summed across netns) ---")
    for name in header:
        print("%-28s %d" % (name, totals[idx[name]]))


if __name__ == "__main__":
    main()
