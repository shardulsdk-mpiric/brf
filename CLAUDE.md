# CLAUDE.md

Working notes for Claude Code in this BRF (BPF Runtime Fuzzer)
checkout.  This tree is a **reusable substrate** for kernel
protocol-flow fuzzing: an extension of Hsin-Wei Hung & Ardalan
Amiri Sani's published eBPF runtime fuzzer (UC Irvine,
arXiv:2305.08782, NSF + Google ASPIRE funded), which is itself a
fork of Google's Syzkaller.  Mpiric maintains it to lift BRF's
state-carrier pseudo-syscall pattern to network transport-security
protocol flows (MPTCP today; QUIC and tlshd as planned siblings),
to drive upstream bug findings, and to support any external
presentation built on that work.  The harness, the AI-augmented
description authoring methodology, and the bug case studies all
live here as substrate; they are not artefacts of one specific
submission venue.

## Authorship chain (important -- preserve this in all external-facing work)

```
Syzkaller (Google, Dmitry Vyukov et al.)
   ↓ fork, Jan 2024
BRF (Hsin-Wei Hung + Ardalan Amiri Sani, UC Irvine; published
      arXiv:2305.08782, May 2023; ACM venue version at
      papers/3643778.pdf)
   ↓ fork, Aug 2025
shardulsdk-mpiric/brf  (this checkout)
```

License: Apache 2.0.  No CLA.  Attribution requirements: preserve
`LICENSE`, `AUTHORS`, `CONTRIBUTORS`; cite Syzkaller and BRF (with
funding ack: NSF #1763172, NSF #1846230, Google ASPIRE 2020) in any
external presentation.

**Mpiric's contribution shape:** *extension*, not authorship.  We
never present BRF as our platform.  We present our protocol-flow
harness work as extending BRF.

## What's in flight here

See `.claude/users/<your-name>/tasks/index.md` (per-user task
registry; see the per-user convention section below).  As of this
writing the active task in `shardul/` is:

- **mptcp_protocol_fuzzing** -- extending BRF's harness-generation
  approach to kernel transport-security protocol flows (MPTCP,
  QUIC, tlshd) with AI-augmented syscall description authoring.
  Substrate work that drives upstream bug findings and is
  available to any external presentation venue built on it.

The task brief at `.claude/users/shardul/tasks/mptcp_protocol_fuzzing/CLAUDE.md`
is the primary read.

## Workspace root convention

All absolute paths in this tree's docs and scripts are expressed
relative to `$KERNEL_DEV_ENV_ROOT` -- the path to the
`mpiric_kernel_dev_env/` workspace on whatever machine you're on.
This BRF tree itself lives at
`$KERNEL_DEV_ENV_ROOT/open/src/fuzzing/brf`; the kernel clone is at
`$KERNEL_DEV_ENV_ROOT/open/src/kernel/linux`; the shared scratch tree
at `$KERNEL_DEV_ENV_ROOT/shared/`; and so on.

Resolving it:

- **In scripts**: source `$KERNEL_DEV_ENV_ROOT/infra/scripts/config.sh`,
  which auto-detects the root from the script's own location (it walks
  up looking for `infra/` + `open/` markers), with `KERNEL_DEV_ENV_ROOT`
  env-var override.  BRF-tree scripts can also derive it directly:
  `KERNEL_DEV_ENV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../../.. && pwd)"`.
- **In docs**: copy-paste blocks use `$KERNEL_DEV_ENV_ROOT/...`.  Either
  `export KERNEL_DEV_ENV_ROOT=...` once in your shell, or substitute your
  workspace root by hand.
- **Inside the dev_env VM**: paths use the 9p mount tags directly --
  `/mnt/src` (= `$KERNEL_DEV_ENV_ROOT/open/src`), `/mnt/build`
  (= `$KERNEL_DEV_ENV_ROOT/open/build`), `/mnt/host`
  (= `$KERNEL_DEV_ENV_ROOT/shared`).  These are stable regardless of
  where the host workspace lives.

If you see a literal `/mnt/work_4gb/Dev/mpiric_kernel_dev_env/...` or
`/mnt/dev/users/shardulb/workspace/mpiric_kernel_dev_env/...` in any
file in this tree, treat it as a stale leak from a particular
contributor's machine -- file a fix.

## Cross-repo context

Mpiric's main Linux kernel work happens in a separate clone at:

```
$KERNEL_DEV_ENV_ROOT/open/src/kernel/linux
```

A companion proposal task lives there with cross-
references back here:

- Proposal task brief: `.claude/tasks/mptcp_protocol_fuzzing_proposal/CLAUDE.md`
  (in the Linux clone)
- Upstream-presentation artefacts (draft, research questions, roadmap):
  `shared/mpiric/<task-dir>/` (in the same dev tree)

Read those when proposal-side decisions need updating.

## Auto-loaded references and session-setup methodology

The full per-repo layout convention is documented at
`.claude/README.md` (this is the methodology source-of-truth for
BRF; the canonical write-up lives in the microkernel repo).

Session contract:
1. This file (`CLAUDE.md`) auto-loads.
2. Claude reads the active user's `.claude/users/<name>/tasks/index.md`
   (small, always read).  The user's identity is inferred from the
   git user (`git config user.name`) or asked at session start.
3. When user requests match a task's keywords, Claude asks before
   loading the brief.
4. Principle files under `.claude/principles/` (none yet) and design
   files under `.claude/designs/` load automatically on the trigger
   conditions in the table below.

### Auto-loaded references (read without asking, when triggered)

| When the work involves...                                            | Read this without asking                                |
|----------------------------------------------------------------------|---------------------------------------------------------|
| Touching `prog/brf*.go`, `executor/common_brf*.h`, BRF pseudo-       | `.claude/designs/brf_architecture.md`                   |
| syscall lifecycle, or designing a new BRF-style harness extension    |                                                         |
| (MPTCP, QUIC, tlshd, or any new protocol substrate)                  |                                                         |
| Implementing, debugging, or extending the MPTCP MP_JOIN harness      | `.claude/designs/mptcp_join_harness_design.md`          |
| (sys/linux/socket_mptcp_crypto.txt, executor/common_brf_linux_mptcp.h, or kernel-side kcov patches for net/mptcp/) |                                                         |
| Picking or updating a kernel base for any harness, applying our      | `.claude/designs/kernel_base_management.md`             |
| kernel-side patches, or recording reproducibility footers for bug    |                                                         |
| reports                                                              |                                                         |
| Implementing or extending **Phase 3** -- the BPF struct_ops MPTCP    | `.claude/designs/mptcp_bpf_sched_phase3.md`             |
| scheduler -- or changing BRF's program generator (`prog/brf*.go`)    |                                                         |
| for struct_ops program generation                                    |                                                         |

## Design principles (binding on all work in this tree)

- **Citation discipline.** Every external artifact (talk, paper,
  blog, lore post) that references BRF must cite both Syzkaller
  and Hung+Sani's BRF paper.  Funding acknowledgment for any
  presentation built on BRF.  No exceptions; this protects Mpiric's
  reputation in the academic-leaning kernel-security community.
- **Extension, not appropriation.** We can fork, modify, and present
  extensions.  We do not refer to BRF as ours, claim authorship,
  rename the project in any external materials, or strip upstream
  attribution from forks we publish.
- **Speaker-defensibility constraint.** Anything that lands in
  external-facing material (slides, paper, blog, lore message)
  has to be defendable by the presenter under Q&A from kernel
  veterans.  The learning model from the Linux clone applies
  here too: Claude excavates, the human owner diagnoses and
  owns the presentation.  If a piece of work can't be defended
  cold, it doesn't go on a slide.
- **AI use is in scope to discuss openly, not to hide.**  The
  methodology being developed here -- AI-augmented syscall
  description authoring for protocol flows -- is the substance
  of the project's documented methodology, not a guilty secret.
  Be honest about what works, what fails, where the human
  verification step lives.

## What's tracked in git here

Per the existing `.claude/README.md` recommendation:

| Path | Tracked? |
|---|---|
| `CLAUDE.md` (this file) | Yes |
| `.claude/README.md`, `.claude/SESSION_SETUP_PATTERN.md` | Yes |
| `.claude/principles/*.md` (when added) | Yes |
| `.claude/designs/*.md` | Yes (shared substrate) |
| `.claude/users/README.md` | Yes (explains the per-user convention) |
| `.claude/users/.contributors` | Yes (one line per active collaborator) |
| `.claude/users/<name>/tasks/`, `.claude/users/<name>/notes/` | No (per-user working memory) |
| `.claude/tasks/` (legacy single-user dir) | No (kept gitignored for back-compat) |
| `.claude/settings.local.json` | No (per-machine) |

### Per-user convention

To let multiple collaborators use this tree without stepping on
each other's working memory, per-user state lives under
`.claude/users/<your-name>/` (`tasks/`, `notes/`, ...).  These
subtrees are gitignored.  Shared substrate -- `.claude/designs/`,
`.claude/principles/`, `.claude/README.md`, this `CLAUDE.md`, the
`findings/` tree, and the BRF/syzkaller code -- is commonly owned
and tracked normally.  See `.claude/users/README.md` for the
onboarding recipe; add a line for yourself to
`.claude/users/.contributors` so others know you're working in
this tree.

## Maintaining this file

`CLAUDE.md` is living.  Update it directly with `Edit`/`Write` when:

- A new task becomes active.
- A new design principle binds future sessions.
- The cross-repo context changes (paths move, related tasks shift).

Don't re-run `/init`; the hand-built sections above would be
clobbered.
