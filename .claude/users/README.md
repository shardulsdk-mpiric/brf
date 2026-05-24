# `.claude/users/` -- per-user working memory convention

This directory holds **per-collaborator** Claude Code working
memory for this BRF tree.  Each contributor gets their own
subdirectory `.claude/users/<your-name>/` and is free to organise
its contents (`tasks/`, `notes/`, etc.) however they want; the
subtrees are gitignored by default, so a collaborator's working
state never collides with another's.

Shared substrate stays at the conventional locations:

- `.claude/designs/` -- technical / architecture / recipe docs.
- `.claude/principles/` -- short judgment-shaping principle files.
- `.claude/README.md`, `.claude/SESSION_SETUP_PATTERN.md` -- the
  layout and methodology.
- `CLAUDE.md` (repo root) -- the always-loaded entry point.

The `findings/` tree, the BRF/syzkaller code, and the kernel
patch series are also commonly owned and are not per-user.

## Layout

```
.claude/users/
  README.md                this file (tracked)
  .contributors            one line per active collaborator (tracked)
  <name>/                  one collaborator's working memory
    tasks/                 gitignored: per-task working dirs
      index.md             registry: name, status, keywords, brief path
      <task_slug>/
        CLAUDE.md          task brief: scope, memory load list, principles
        commits.md, patch_plan.md, repro_notes.md, ...  optional notes
    notes/                 gitignored: free-form per-session notes
    profile.md             optional, force-add to track: collaborator
                           profile / contact / working preferences
```

Anything under `.claude/users/<name>/` is gitignored *except* files
that are explicitly force-added.  If you want to track a profile
note or a contact file under your own dir, do:

```
git add -f .claude/users/<your-name>/profile.md
```

## Onboarding (when you start working in this tree)

1. Create your own directory: `mkdir -p .claude/users/<your-name>/tasks`.
2. Start your task index at `.claude/users/<your-name>/tasks/index.md`.
   Use the format from `.claude/SESSION_SETUP_PATTERN.md` ("Adding a
   new task") or copy from another collaborator's index as a
   template.
3. Add a line for yourself to `.claude/users/.contributors`
   (a tracked file).  One line per person, free-form, suggested
   shape: `<your-name>  <email>  <one-line role / focus>`.
4. Tell Claude (or rely on `git config user.name`) which user
   directory to read at session start.

## Why per-user instead of one shared `tasks/`

Multiple collaborators ran into avoidable friction when they
shared a single `.claude/tasks/` tree: task slugs collided, task
indexes drifted between machines, and each new contributor had to
inherit the previous contributor's working state.  Per-user dirs
fix all three.  The tradeoff is that cross-collaborator awareness
of "what tasks are in flight" now requires reading more than one
index -- the `.contributors` file is the way to discover whose
indexes exist.

## Relationship to the legacy `.claude/tasks/` directory

The older single-user `.claude/tasks/` location is still
gitignored and still works for repos / phases where only one
contributor is active.  When a second contributor joins, the
expectation is to migrate working state under
`.claude/users/<name>/tasks/` rather than to keep growing the
shared dir.  Either layout is acceptable; both are gitignored.
