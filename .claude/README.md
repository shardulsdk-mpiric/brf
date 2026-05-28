# `.claude/` -- Claude Code working layout for BRF

This directory holds the Claude-side scaffolding that makes sessions in
this BRF (BPF Runtime Fuzzer) checkout productive without re-explaining
context every time. It is meant to work alongside a repo-root `CLAUDE.md`
(the always-loaded entry point). At time of writing, only this README and
`SESSION_SETUP_PATTERN.md` exist -- the rest of the layout (principles,
tasks, archive, settings.local.json) is created on demand as work
crystallizes.

The pattern itself is canonical -- developed in another working tree
and reused unchanged here. See
`SESSION_SETUP_PATTERN.md` for the full methodology and the rationale
for each piece. This README only documents how it currently lives in
BRF; the *why* lives there.

## What BRF is (so the layout makes sense)

BRF is a Syzkaller-derived, coverage-guided fuzzer that targets the
runtime side of the eBPF subsystem (verifier-passed programs running
under JIT, plus the helpers/maps/attach paths around them). The
codebase is Go (Syzkaller's prog/sys descriptors + a BRF overlay) plus
the C executor and the syscall description files under `sys/linux/`.

The repo's mainline is `dev`; the current working branch is
`protocol_flow_fuzzing_harness` (the kernel protocol-flow harness
work; off `bootstrap_experimental_v0_01`).
`origin` points at the personal fork
`git@github.com:shardulsdk-mpiric/brf.git`, so this is a working clone,
not a public mirror.

This shape -- a long-running fork with several in-flight changes (new
prog types, new map types, fuzzer tuning, deadlock/repro analysis,
kernel-side patches) -- is exactly the kind of repo
`SESSION_SETUP_PATTERN.md` was designed for. See the "When to apply
this pattern" section there.

## Intended layout (full form)

```
.claude/
  README.md                       this file
  SESSION_SETUP_PATTERN.md        canonical methodology (copy of microkernel's)
  settings.local.json             Claude Code local settings (gitignored)
  principles/                     short design-principle files (read on trigger; shared)
    <aspect>.md                   judgment-shaping: how to think about X
                                  e.g., kernel_patch_authoring.md
  designs/                        technical architecture / recipe docs (shared, tracked)
    <subject>.md                  fact-shaping: how a thing actually works
                                  e.g., brf_architecture.md (the BRF pseudo-syscall
                                  pattern and its lift to protocol fuzzing)
  users/                          per-user working memory (see users/README.md)
    README.md                     explains the per-user convention (tracked)
    .contributors                 one line per active collaborator (tracked)
    <name>/                       a single collaborator's working memory
      tasks/                      (gitignored) per-task working dirs
        index.md                  registry: name, status, keywords, brief path
        <task_slug>/
          CLAUDE.md               task brief: scope, memory load list, principles
          [optional] commits.md, patch_plan.md, repro_notes.md, ...
      notes/                      (gitignored) free-form per-session notes
  archive/                        retired memories with index
    README.md                     index: what was moved and why
```

The `users/<name>/tasks/` layout replaces the older single-user
`tasks/` directory at the `.claude/` root.  Existing per-developer
`tasks/` content from before the change can either stay where it
is (it remains gitignored) or be moved under
`.claude/users/<your-name>/tasks/`.

**`principles/` vs `designs/` distinction:** `principles/` files are
judgment-shaping ("when working on X, think this way"); `designs/`
files are fact-shaping ("here is how X actually works, and the
recipe for extending it").  Both are tracked, both are short, both
load on trigger from the CLAUDE.md table.  When a doc straddles the
two -- a recipe that also encodes opinions -- pick the directory
whose centre of gravity dominates and cross-link from the other.

Only the README + SESSION_SETUP_PATTERN files exist today. Everything
else is added when the corresponding work appears -- creating empty
scaffolding up front violates the "density wins over completeness"
guidance the pattern itself argues for.

## How a session is meant to start (the contract, once the scaffold is filled in)

1. Repo-root `CLAUDE.md` auto-loads.  It tells Claude to read the
   active user's `.claude/users/<name>/tasks/index.md` and lists
   the auto-load reference triggers.
2. Claude reads that per-user `tasks/index.md` -- a small file --
   to learn active tasks and their keyword map.
3. When the user's request matches a task's keywords, Claude **asks
   before loading** the task brief.
4. After loading the brief, Claude follows the brief's memory load
   list and the CLAUDE.md trigger table to read principle / design
   files automatically, announcing each load so the user can
   redirect.

Task briefs are gated by user confirmation. Principle files load
automatically when work matches a row in the CLAUDE.md trigger table.
This asymmetry is deliberate -- see `SESSION_SETUP_PATTERN.md`.

Until a repo-root `CLAUDE.md` is added here, Claude will not
auto-discover this layout. Bootstrapping that file is step 1 of the
"Bootstrapping the pattern in a new repo" section of
`SESSION_SETUP_PATTERN.md`.

## What's tracked in git vs not (recommended for BRF)

BRF's `origin` is a personal fork, so tracking some of this scaffold
is reasonable.  The recommended split mirrors microkernel and now
adds the per-user convention:

| Path                                          | Tracked? | Why                                                                 |
|-----------------------------------------------|----------|---------------------------------------------------------------------|
| `CLAUDE.md` (repo root)                       | Yes      | Repo entry point for Claude                                         |
| `.claude/README.md`                           | Yes      | Explains the convention                                             |
| `.claude/SESSION_SETUP_PATTERN.md`            | Yes      | Methodology pointer (canonical source lives in microkernel)         |
| `.claude/principles/*.md`                     | Yes      | Project-wide design philosophy                                      |
| `.claude/designs/*.md`                        | Yes      | Shared technical / architecture docs                                |
| `.claude/users/README.md`                     | Yes      | Explains the per-user convention                                    |
| `.claude/users/.contributors`                 | Yes      | One line per active collaborator                                    |
| `.claude/users/<name>/tasks/`                 | No       | Per-user task working memory                                        |
| `.claude/users/<name>/notes/`                 | No       | Per-user free-form notes                                            |
| `.claude/tasks/` (legacy)                     | No       | Per-developer working memory from before the per-user split         |
| `.claude/settings.local.json`                 | No       | Per-machine Claude Code settings                                    |

The `.gitignore` excludes the local-only paths; the per-user
convention's gitignore lines (`/.claude/users/*/tasks/`,
`/.claude/users/*/notes/`) sit next to the legacy
`/.claude/tasks/` entry.  A tracked profile note inside a user's
dir can be force-added (`git add -f .claude/users/<name>/profile.md`).

## BRF-specific candidates for `principles/` (when they earn their slot)

Don't write these speculatively -- write each one only when an actual
session showed Claude needed it. Likely candidates given the work
visible in `git log` and the current diff:

- **Syzkaller descriptor discipline.** How `sys/linux/bpf.txt` and
  `bpf.txt.const` interact, how syz-sysgen consumes them, where the
  ABI lives, why every flag added to a `flags[...]` group must also
  appear in `.const`. (Justified by the arena-map work in the current
  diff.)
- **Fuzzer grammar vs runtime separation.** What's in `prog/` (Go-side
  generation/mutation) vs `executor/` (C-side runtime) vs `sys/linux/`
  (descriptors). Where a given bug class is best fixed.
- **Focused vs broad fuzzing.** When narrowing program-type selection
  is the right move (e.g. the `brf_legacy.go` change restricting to
  LSM/SYSCALL/NETFILTER) and how to revert it cleanly when broad mode
  resumes.
- **Kernel-patch authoring on this fork.** Email, sign-off, AI-tag
  policy for any commit that targets the kernel or bpf-next (distinct
  from BRF-side commits). Relates to global
  `feedback_no_coauthor.md` and `feedback_format_patch_below_cut.md`.

## Adding a new task (when the time comes)

1. Create `.claude/users/<your-name>/tasks/<task_slug>/CLAUDE.md`
   -- the brief.  Sections: goal, scope/anti-scope, memory load
   list (split: critical / helpful / skip), key files in repo,
   working principles, open questions.  Use any existing brief
   from microkernel or the Linux clone as a template.
2. Add an entry to `.claude/users/<your-name>/tasks/index.md`
   with status, keywords, brief path, branch, last touched,
   one-line goal.  Mark `status: active` if it's the current
   focus.
3. Optionally add skeleton sibling files (e.g. `commits.md`,
   `patch_plan.md`, `repro_notes.md`) for working notes.
4. When done: set `status: done` and consider moving the brief
   contents into `.claude/archive/` if there is a write-up worth
   preserving.

## Adding a new design principle

1. Create `.claude/principles/<aspect>.md`. Top of file: a "Read this
   when:" line so Claude knows the trigger condition. Keep it focused
   and short -- principles are read into context, not auto-loaded, so
   density wins over completeness.
2. Add a row to the "Auto-loaded references" table in the repo-root
   `CLAUDE.md` mapping work conditions to the new file.
3. Cross-link from related memory files in the project memory
   directory at
   `~/.claude/projects/-mnt-work-4gb-Tools-003-kernel-testing-prana-kernel-testing-host-drive-tests-bpf-runtime-fuzzer-git-repo-brf/memory/`.

## Why this layout

- **Reduces repeated explanation.** Task scope, principles, and
  memory load lists are encoded once and recalled automatically.
- **Keeps auto-loaded context small.** Only `CLAUDE.md` and
  `MEMORY.md` always load. Briefs and principles are read on-trigger.
- **Separates stable (principles) from volatile (tasks).** Principles
  travel via git; tasks stay local.
- **Asymmetric loading rules**: ask before loading task briefs (large,
  context-heavy); auto-load principle files (small, judgment-shaping).

## Canonical methodology

The pattern here is a per-repo application of a methodology developed
in another working tree.  The full write-up lives next to this README
as `.claude/SESSION_SETUP_PATTERN.md`, which is self-contained for
contributors who don't have the original source tree.
