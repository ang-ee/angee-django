---
description: Lift a capability from another repo (a prototype or another framework) into this one by reconstructing it to local conventions — never by copying files.
argument-hint: <source path, or a description of what to lift>
---

# /lift — reconstruct, don't copy

You are lifting **$ARGUMENTS** into this repository.

Lifting is **deconstruct → understand → reconstruct**, never copy. The source is a
reference for *intent and behaviour*, not a source of files. What you produce must
read as if it were written here natively, by someone who only knows this repo's
conventions and has never seen the source.

## Absolute rules

- **Never `cp`, `mv`, or paste source files verbatim.** Read the source, then write
  fresh against local conventions. If a file you produce is byte-identical to a
  source file, you did it wrong — start that file over.
- **Green-field — no provenance anywhere.** Never mention the source repo, a
  prototype, an earlier version, "lifted/ported from", plan numbers, a rebuild, or a
  migration — not in code, comments, docs, resource data, filenames, or commit
  messages. The origin of the code must not appear in any artifact.
- **Stay DRY and land at the right level.** Reuse existing local primitives instead
  of reimporting; put each change at the level that owns the concern — framework /
  base addon vs consumer addon (see `AGENTS.md` → Repository Role).
- **Defer to the stack.** If `docs/stack.md` says a library owns a concern, wire it —
  do not bring the source's hand-rolled version. Do not add a dependency without an
  owner row; flag it instead of adding it silently.

## Process

1. **Understand the source.** Read what you are lifting. Identify the real capability,
   its inputs/outputs, and its genuine dependencies. Ignore how it happens to be
   wired in its repo.
2. **Understand here.** Read `AGENTS.md`, `docs/guidelines.md`, `docs/stack.md`,
   `docs/glossary.md`, and the relevant `docs/backend/guidelines.md` /
   `docs/frontend/guidelines.md`. Scan existing local code for the patterns, naming,
   and primitives you should match and reuse.
3. **Decide placement and level.** Framework/base addon or consumer addon? Where in
   the local layout does it belong? What does the local stack already provide that you
   should reuse rather than port?
4. **Reconstruct from first principles.** Rewrite the capability in local idioms,
   naming, and structure, on stack-sanctioned libraries. Drop anything the framework
   already owns. Aim to make it smaller and clearer than the source — watch for the
   red flags in `docs/guidelines.md` (bigger instead of smarter, spaghetti, code you
   don't understand, reinventing a tested wheel).
5. **Verify it belongs.** Re-read your output as if reviewing a native contribution:
   local conventions, DRY, no dangling references to things that don't exist here, no
   provenance. Run the relevant per-area checks if they are wired.

## Report

- What capability you reconstructed, and at what level.
- What you reused from the local stack/code instead of porting.
- What you deliberately dropped or simplified, and why.
- Anything flagged: dependencies needing owner rows, unwired tools, or decisions that
  need a human.
