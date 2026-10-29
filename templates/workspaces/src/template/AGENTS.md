# AGENTS.md

This is an **Angee framework development workspace**, materialized by the
`workspaces/src` template. The consolidated framework is one source slot, with
optional external-addon slots beside it. The workspace is the working surface;
the source caches its slots are cut from (the stack's `sources/`, or its
machine-wide `sources_home`) belong to the operator — never work there.

Angee development uses Jujutsu (jj): each source store is a colocated jj
repository, and new work goes in jj workspaces of it. A code slot that has
`.jj` is a jj workspace the operator cut and drives ([jj slots](#jj-slots));
one without is a git worktree. `angee ws create <name> --template src` cuts a
new workspace's code slots in the `slot_mode` the stack's `workspace_defaults`
gives in `angee.yaml`, git worktrees when it gives none:

- When it gives `jj`, create workspaces with
  `angee --root "$ANGEE_ROOT" ws create <name> --template src`.
- Otherwise **do not create workspaces with `angee ws create`**; add a jj
  workspace by hand. The `angee ws` verbs address only the workspaces the
  operator created, such as this one.

`angee/.agents/skills/angee-workspace/SKILL.md` owns the workspace workflow;
in a slot the operator cut as a jj workspace, [jj slots](#jj-slots) wins over
its jj recipes.

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
  push continuously**, or the work is invisible everywhere else. It names no
  mode, so it is a jj workspace when the stack sets `vcs: jj`, and
  `ws sync-base` and `ws push` leave it alone either way. When `.work/.jj`
  exists, use `jj` only there and follow `.work/AGENTS.md`, which owns how it
  is committed and published.

`hatch-angee` and `strawberry-django-hasura` remain independently published
repositories, but normal framework development consumes their PyPI releases;
this workspace does not materialize co-development slots for them. The Go
operator likewise remains on its own release train and is consumed as the
installed `angee` CLI.

## Rules of the workspace

- Each slot is pinned to this workspace's branch — **never `git checkout`
  or `switch` inside a slot**; use another workspace for another branch.
  Update a worktree slot with the workspace verbs (`angee ws sync-base`,
  `ws source pull`) and integrate back with publish/merge; a jj slot follows
  [jj slots](#jj-slots).
- Only the stack's `src` workspace has a running host and the JS install. In
  any other workspace, run focused checks in the slot, and run web checks and
  browser verification in `src` after merging the slot's branch there.
- **Never `pnpm install` inside a slot** — the stack workspace at the stack
  root owns the JS install; a private install forks dependency identities
  for every linked framework package.
- Work inside a slot is governed by that repo's own `AGENTS.md`; run that
  repo's own checks before handing off.
- Work in `.work/` is shared across checkouts: **commit and push continuously**,
  through `jj` when `.work/.jj` exists (`.work/AGENTS.md`).
- **Restart the running stack through the operator** from the stack root, never
  by killing processes. The angee operator is the stack's monitoring and
  management service; it is not part of the frontend or backend and keeps
  running while it restarts them:
  `angee --root "$ANGEE_ROOT" job run deps --chained-restart` restarts the whole
  application, and `angee --root "$ANGEE_ROOT" restart <service>...` restarts
  single services. Apply dependency changes with the whole-application restart;
  never `uv sync` the stack's venv while the stack runs. Details and the
  operator API:
  [Restart the running stack](angee/docs/howto/getstarted.md#restart-the-running-stack).

## jj slots

A slot that has `.jj` is a jj workspace of its source's store, colocated so git
tooling and the repository's checks work inside it. The operator cuts the code
slots that way under `slot_mode: jj`, and `.work` under the stack's `vcs: jj`,
naming each `<stack>--<workspace>` in its store. In every jj slot:

- **One agent per workspace.** Never write in another workspace's slots, or in
  the store the slot was cut from. jj tolerates two processes working in one
  workspace, but their changes can diverge, and the operator's verbs then fail
  on the slot rather than resolve it.
- **Write through `jj` only.** Read-only `git` (`log`, `show`, `diff`,
  `status`) is fine; never `git commit`, `checkout`, `switch`, `reset`,
  `stash` or `push` there. The working copy `@` is a commit of its own on top
  of the slot's work: record it with `jj commit -m "<message>"` (there is no
  staging area; pass paths to commit only those). The slot has no branch until
  its first publish.
- **Never rewrite commits reachable from a pushed bookmark or from the parent
  ref** by hand (`jj rebase`, `jj squash --into`, `jj describe`, `jj abandon`
  on them), and **never pass `--ignore-immutable`**. The sanctioned rewrites
  are the rebase `ws sync-base` runs and resolving a conflict it recorded,
  below.
- If jj reports a stale working copy, run `jj workspace update-stale`.
- A slot the operator cut is its to add and remove: never `jj workspace add`,
  `forget` or `rename` it, or move its directory. `angee ws destroy` removes
  it, and refuses while the slot holds work no remote has, or a nested `.git`
  or `.jj`, which jj does not record.

In a jj code slot, the operator verbs do the rest:

- **Sync through `angee --root "$ANGEE_ROOT" ws sync-base <workspace>`.** It
  fetches, then rebases each code slot's commits, the working copy with them,
  onto the moved base; it never merges a jj slot, and refuses `--merge`. A
  conflict does not stop it: the slot reports `conflicted` with its paths.
  Resolve the files and `jj squash` the fix into the conflicted change, even
  one a pushed bookmark held before the rebase; jj will not push a conflicted
  commit. A branch the rebase rewrote after it was published is
  force-updated, with jj's lease, by its next publish. To bring in a ref
  without rewriting anything (another workspace's branch, or the base once
  others build on yours), run
  `angee --root "$ANGEE_ROOT" ws source merge <workspace> <slot> <ref>`, with
  the ref in jj's spelling (`main@origin`). `ws source pull` refuses a jj
  slot.
- **Publish through `angee --root "$ANGEE_ROOT" ws source publish <workspace>
  <slot>` or `angee --root "$ANGEE_ROOT" ws push <workspace>`.** The operator
  sets the slot's branch (`<stack>/<workspace>`) on `@-`, checks the remote's
  rules, and pushes it with a lease to the slot's push remote, only when the
  slot has commits beyond its base. Never set the bookmark or `jj git push`
  by hand. Commit the working copy first: publish refuses changes still in
  `@`, and commits that have conflicts or no description. A branch holding a
  collaborator's work the slot lacks is refused (`branch_moved`); merge it in
  first.
- **Files jj will not snapshot block sync and publish.** A new file jj leaves
  out of the working-copy commit (one over its size limit, say) is listed as
  untracked, and sync-base, rebase, merge, publish and push refuse the slot
  until the file is ignored, removed, or tracked with
  `jj file track --include-ignored <path>`.
