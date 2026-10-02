# AGENTS.md

This is an **Angee framework development workspace**, materialized by the
`workspaces/src` template. The consolidated framework is one **git worktree
slot**, with optional external-addon slots beside it. The workspace is the
working surface; the stack's `sources/` directory is the operator's clone
cache — never work there.

Angee development uses Jujutsu (jj): each source store is a colocated jj
repository, and every further workspace is a jj workspace of it. The operator
does not manage jj yet, so **do not create workspaces with `angee ws create`**;
its `angee ws` verbs address only the workspaces it created, such as this one.
`angee/.agents/skills/angee-workspace/SKILL.md` owns the jj workflow.

## The slots

- **`angee/`** — the consolidated framework repository. Its root `AGENTS.md`
  carries the constitution for framework work. The main areas are:

  - `angee/` — the Python core and composer;
  - `addons/` — standard `angee.*` folder addons and co-located web fragments;
  - `packages/` — schema-independent `@angee/*` React packages and workshops;
  - `examples/` — showcase consumer addons and the reference e2e suite;
  - `templates/` — project, stack, workspace, service, and addon templates.

- **`angee-messaging-bridges/`** — the opt-in personal-messaging and takeout
  bridge addons (matrix, whatsapp, telegram, discord, signal, imessage,
  facebook, meta). Present only under the full profile.
- **`angee-arp/`** — arpee, the ARP product: the clean-room Odoo port as
  `arp.*` consumer addons. Present only when the stack opts in
  (`include_arp` — the repo is private).
- **`.work/`** — the private work-state repo (plans, notes, memory,
  handovers), present when the stack wires a work-state source or attaches a
  Jujutsu workspace of its shared store. Shared across checkouts: **commit and
  push continuously**, or the work is invisible everywhere else. When
  `.work/.jj` exists, use `jj` only there and follow `.work/AGENTS.md`.

`hatch-angee` and `strawberry-django-hasura` remain independently published
repositories, but normal framework development consumes their PyPI releases;
this workspace does not materialize co-development slots for them. The Go
operator likewise remains on its own release train and is consumed as the
installed `angee` CLI.

## Rules of the workspace

- Each slot is pinned to this workspace's branch — **never `git checkout`
  or `switch` inside a slot**; add a jj workspace for another branch. Update
  operator-created slots with the workspace source verbs (`angee ws …` /
  `workspaceSourcePull`), integrate back with publish/merge.
- A slot that has `.jj` is a jj workspace: write through `jj` only there. It
  has no running host and no JS install, so run web checks and browser
  verification in this stack's `src` workspace after merging its bookmark.
- **Never `pnpm install` inside a slot** — the stack workspace at the stack
  root owns the JS install; a private install forks dependency identities
  for every linked framework package.
- Work inside a slot is governed by that repo's own `AGENTS.md`; run that
  repo's own checks before handing off.
- Work in `.work/` is shared across checkouts: **commit and push continuously**,
  through `jj` when `.work/.jj` exists (`.work/AGENTS.md`).
