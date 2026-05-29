# Working frame

The project's standing frame: what this work is and what it aims at.
Read alongside `methodology.md`, which documents how the harness is
built; this document is the strategic frame that methodology serves.

## Working hypothesis

BRF's design principles — the three-plane layout (syzlang /
executor-C / generator), the pseudo-syscall + out-parameter
state-carrier discipline BRF established on Syzkaller's resource
model, reference acquire/release, and kcov-remote runtime coverage —
should generalize beyond eBPF to other **state-aware,
semantically-constrained** kernel surfaces, where a stateless fuzzer
is rejected at the first protocol gate and never reaches the
interesting code.

MPTCP's MP_JOIN handshake — a token + HMAC chain threaded across a
multi-step flow — is the first concrete test of that hypothesis. It
is the surface where the work has the most depth today, and early
upstream engagement on the direction has been encouraging. The
longer-term aspiration is for the harness to mature into a fuzzing
capability the upstream MPTCP community treats as a standard tool.

## Status: holding

- **The pattern transferred.** The MP_JOIN harness is built, runs,
  and threads live cryptographic state across the flow.
- **It reaches state-gated code a stateless fuzzer cannot construct
  — by construction.** MP_JOIN token lookup, server/client HMAC
  validation and DSS checks reject a random-byte attempt with
  probability ~0 to < 2^-64; the executor constructs the state that
  passes them (see `methodology.md` §2).
- **It surfaced two upstream-mergeable bugs on two distinct MPTCP
  surfaces:** a userspace-PM alloc-during-teardown race (finding
  001) and a kernel-PM-reachable divide-by-zero in `tcp_tso_segs`
  (finding 002).
- **Honest limit.** No controlled comparison against a stateless
  Syzkaller baseline has been run, and N = 2. "Reaches what a
  stateless fuzzer cannot" follows from reachability; "finds more
  bugs than a baseline" does not yet (see `methodology.md` §6).

## Two contributions, two justifications

1. **Pattern extension (the larger slice).** BRF's design principles
   applied to MPTCP's protocol-state surface. This uses BRF's
   architectural discipline and its kcov-remote mechanism, but **not**
   BRF's distinctive eBPF program generator. Justified because the
   patterns generalize to state-aware, non-eBPF surfaces.

2. **Engine extension (the narrower slice — Phase 3).** BRF's
   distinctive eBPF machinery — the program generator,
   verifier-semantics tables, the libbpf/BTF load path, and the
   `BpfRuntimeFuzzer` orchestrator — extended into the
   `mptcp_sched_ops` struct_ops surface. Justified because, and only
   because, MPTCP exposes an eBPF surface here. This is the slice
   where "we extended BRF" is fully literal.

## Future directions (hypothesis, not result)

- **Other state-aware, non-eBPF surfaces** where the design
  principles should transfer: in-kernel QUIC handshake (key-derivation
  shape is structurally similar — plausibly portable) and tlshd /
  NET_HANDSHAKE (a userspace-driven handshake — may need a different
  carrier abstraction). Planned, unbuilt; the fit is a hypothesis.
- **Other struct_ops surfaces** where the engine extension
  generalizes: `bpf_tcp_ca` (TCP congestion-control struct_ops),
  another struct_ops target reportedly not covered by syzkaller's
  current BPF support — a concrete near-term transfer target for the
  Phase 3 machinery.
- BRF maintenance for upstream eBPF feature parity is adjacent, not
  the main thrust.
