# AGENTS.md

This is an **Angee framework development workspace**, materialized by the
`workspaces/src` template: every directory here is a **git worktree slot** of
a framework source repo, side by side. The workspace is the working surface;
the stack's `sources/` directory is the operator's clone cache — never work
there.

## The slots

- **`angee-django/`** — the framework core: `angee.{base,compose,graphql,tasks}`,
  the composer, and the framework docs. The one real Python package. Its
  `AGENTS.md` carries the constitution that governs work in every slot.
- **`angee-react/`** — the framework React packages (`@angee/app`, `ui`,
  `refine`, `metadata`) with the storybook and e2e workshops.
  Schema-independent by invariant.
- **`angee-base/`** — the base addons: folders with `addon.toml`, each a
  Django app with its co-located `web/` fragment. Content, not a package.
- **`angee-messaging-bridges/`** — the opt-in personal-messaging and takeout
  bridge addons (matrix, whatsapp, telegram, discord, signal, imessage,
  facebook, meta). Same folder-addon model.
- **`angee-examples/`** — showcase consumer addons (`example.notes`) and the
  reference e2e suite; the reference for third-party addon authors.
- **`angee-arp/`** — arpee, the ARP product: the clean-room Odoo port as
  `arp.*` consumer addons. Present only when the stack opts in
  (`include_arp` — the repo is private).
- **`angee-templates/`** — the Copier templates that render stacks, projects,
  workspaces, and services (what `angee init` consumes).
- **`angee-operator/`** — the `angee` CLI / operator daemon (Go).
- **`.work/`** — the private work-state repo (plans, notes, memory,
  handovers), present only when the stack wires a work-state source. Shared
  across clones: **commit and push continuously**, or the work is invisible
  everywhere else.

## Rules of the workspace

- Each slot is pinned to this workspace's branch — **never `git checkout`
  or `switch` inside a slot**; create another workspace for another branch.
  Update slots with the workspace source verbs (`angee ws …` /
  `workspaceSourcePull`), integrate back with publish/merge.
- Slots reference each other **sibling-relative** (`../angee-react/…`,
  `{BASE_DIR}/../angee-base/addons`); that layout is the contract this
  workspace materializes.
- **Never `pnpm install` inside a slot** — the stack workspace at the stack
  root owns the JS install; a private install forks dependency identities
  for every linked framework package.
- Work inside a slot is governed by that repo's own `AGENTS.md`; run that
  repo's own checks before handing off.
