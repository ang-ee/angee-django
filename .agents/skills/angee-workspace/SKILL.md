---
name: angee-workspace
description: Use when creating Angee workspaces or operating workspace branches with /pull, /push, sync-base, parent workspace sync, workspace-to-workspace merge, commit, publish, or GitOps topology checks.
---

# Angee Workspace

This skill owns Angee workspace branch workflow. Use Angee's CLI and GitOps
state as the source of truth; do not reconstruct workspace state from directory
names unless the CLI cannot answer.

## Owners

- Workspace lifecycle: `angee --root "$angee_root" ws ...`.
- Workspace Git state: `angee --root "$angee_root" ws git <name> --json` and
  `angee --root "$angee_root" gitops topology --json`.
- Source-slot operations: `angee --root "$angee_root" ws source ...`.
- Raw Git is for committing reviewed changes, inspecting worktrees, and the
  narrow source-cache fast-forward described below when Angee cannot expose it.
- Jujutsu workspaces: `jj`, as described in
  [Jujutsu Source Stores](#jujutsu-source-stores). The operator does not manage
  them yet.

Resolve the controlling stack root before any workspace or GitOps command. Run
those commands with the resolved root explicitly, even while inside a source
checkout or target workspace. Never `git checkout` or `git switch` inside an
Angee workspace; a workspace is pinned to its `<prefix>/<name>` branch, where
the prefix is the stack's name (from the stack's `workspace_defaults`) unless
the workspace declares a shared `feature/<topic>` branch explicitly.

## Jujutsu Source Stores

Angee development uses Jujutsu (jj). A source store is a colocated jj
repository: the path GitOps topology reports for the source contains `.jj`.
New workspaces are jj workspaces of that store, created and driven with `jj`.

The operator does not manage jj yet. Do not create workspaces with
`angee ws create`: it materializes git worktrees, which new work no longer
uses. The `angee ws ...` verbs address only workspaces the operator created,
so a jj workspace is unknown to `ws list`, `ws git`, `ws status`, `sync-base`,
and `ws source`; inspect and operate it with `jj`. Existing operator
workspaces, including the stack's `src`, keep the operator verbs below.

A slot is a jj workspace when `<slot>/.jj` exists. In such a slot:

- Write with `jj` only: commit, rebase, bookmark, push. Read-only `git` (`log`,
  `show`, `diff`, `status`) is fine. Never `git commit`, `git checkout`,
  `git switch`, `git reset`, or `git stash` there.
- The working copy is a change of its own on top of its parent. No branch
  exists until a bookmark is set; name it `<prefix>/<name>` by the namespace
  rule above.
- Never rewrite commits reachable from a pushed bookmark or from the parent
  ref, and never pass `--ignore-immutable`.
- If jj reports a stale working copy, run `jj workspace update-stale`.

## Resolve The Controlling Stack Root

An existing current or ancestor stack owns workspace lifecycle. A workspace
root is not a git repository (its repo slots are), so walk up from the current
directory — never from `git rev-parse --show-toplevel`:

```sh
angee_root=
candidate=$(pwd -P)
while :; do
  if test -f "$candidate/angee.yaml"; then
    angee_root=$candidate
    break
  fi
  parent=$(dirname "$candidate")
  test "$parent" != "$candidate" || break
  candidate=$parent
done
test -n "$angee_root" || exit 1
```

If no ancestor stack exists, stop and ask the user how the stack should be
initialized; do not run `angee init` unless the user explicitly requested a
new stack. Never initialize a stack under a source checkout.

Use `angee --root "$angee_root" ...` for every workspace and GitOps command
below. The explicit root is intentional: the CLI otherwise defaults to the
current directory, which is rarely the stack root.

## Resolve The Current Workspace

1. If the current directory is under `$angee_root/workspaces/<name>`, use
   `<name>`.
2. Otherwise require the workspace name from the command arguments or user
   prompt.
3. Confirm with `angee --root "$angee_root" ws git <name> --json`. When the
   operator does not know the workspace and its slot has `.jj`, it is a jj
   workspace: confirm with `jj status` inside the slot and follow the jj
   sections of this skill.
4. If there is more than one git source slot, operate per slot. Match slots by
   name when pulling from another workspace.

For each workspace worktree source, the parent ref is the source `ref` reported by
`angee --root "$angee_root" ws git <name> --json` or the matching link in
`angee --root "$angee_root" gitops topology --json`. That ref may be a normal
branch (`main`), a shared feature branch (`feature/<topic>`), or another
workspace branch (`<prefix>/<parent>`).

The optional work-state checkout is independent task history. Do not merge it
between workspace branches as if it were a framework worktree; synchronize its
own reported upstream when that operation is authorized.

A "parent workspace" is the workspace whose reported source `branch` equals the
current workspace's parent ref. When such a workspace exists locally, treat that
workspace as the pull source and apply the source-workspace checks below. When
no workspace owns the parent ref, treat the parent as a branch/ref.

Before pulling any branch/ref, check whether that branch is checked out in a
local worktree with `git worktree list --porcelain`. If it is, inspect that
worktree before merging:

- Dirty worktree: stop unless the user explicitly overrides.
- Clean but unpushed branch: stop unless the user explicitly overrides.
- Diverged branch: stop.
- Clean and pushed branch: it is safe to use as the pull source.

An explicit override means the user has directly said to merge local unpublished
or uncommitted source state anyway. If the user overrides a dirty source
worktree, report that uncommitted files still will not be included unless they
are committed first.

## Resolve Work-State

Use the current workspace's reported `work-state` slot, when present. The src
template materializes it at `$angee_root/workspaces/<name>/.work`, beside the
framework checkout. It is a Git clone or a local-source symlink, according to
the stack source kind, or a Jujutsu workspace of a shared work-state store that
the stack attaches itself; it is not necessarily `<repository>/.work`. When
`.work/.jj` exists, every write goes through `jj` (never `git commit`/`git push`
there) and `.work/AGENTS.md` owns the commit, publish, and attach procedure.

Write private specs, plans, notes, and handovers beneath that resolved location.
If the optional slot is absent, keep task state in the conversation; do not
create a replacement directory or fall back to `docs/superpowers`. Durable
rules and pitfalls belong in the owning checked-in guideline, as defined by
`AGENTS.md` → Where Knowledge Lives.

## Inspect Workspace

For status or inspection requests, resolve the workspace and read its native
`ws git <name> --json`, `ws status <name>`, and relevant GitOps topology.
Report per-slot paths, refs, branches, cleanliness, and upstream state. Inspection
does not create a workspace or publish changes.

For a jj workspace, read `jj -R <store-path> workspace list` and, inside the
slot, `jj status` and `jj log -r '@ | @- | <parent-ref>@origin'`. Report the
slot path, its parent, its bookmark if one is set, and whether that bookmark is
pushed.

## Create Workspace

A new workspace is a jj workspace of the framework source's store. Resolve the
store path from GitOps topology. If that store has no `.jj`, stop and ask the
user; do not fall back to `angee ws create`.

Choose the framework's `<parent-ref>` in this order:

1. An explicit argument from the user.
2. The reported framework slot branch, when creating a child from a workspace.
3. The current framework repository branch, when outside a workspace.
4. Ask the user; do not silently fall back to `main` when the parent is unclear.

Fetch first so the parent ref is current, then add the workspace under the
stack's `workspaces/` directory:

```sh
jj -R <store-path> git fetch
jj -R <store-path> workspace add --colocate \
  --name <stack>--<name> -r <parent-ref> "$angee_root/workspaces/<name>/angee"
```

- `--colocate` gives the slot its own `.git`, so git tooling and the
  repository's checks work inside it. It needs jj 0.46 or a build that has the
  flag.
- `<stack>` is the stack's name, the same namespace as its branch prefix.
- Add an external slot the same way from its own store, and only when the work
  touches that repository.
- Attach `.work` beside the slot by the work-state repository's `AGENTS.md`.
- The workspace has no running host, no database, and no JS install: the
  stack's JS install links only the `src` workspace's slots. Run focused Python
  checks in the slot. Run web checks, codegen, and browser verification on
  `src`, after merging the workspace's bookmark there with
  [Pull From Another Workspace Or Ref](#pull-from-another-workspace-or-ref).

Remove one with `jj -R <store-path> workspace forget <stack>--<name>`, then
delete its directory.

After creation, report the slot path, the parent ref, whether `.work` was
attached, and that no bookmark exists until the first publish.

## Pull: Bring Changes Into Current Workspace

`/pull` means "get changes into the current workspace branch." It does not
publish, and it does not commit unrelated working-tree changes.

### Pull In A jj Workspace

Fetch, then move the workspace's changes onto the parent:

```sh
jj git fetch
jj rebase -b @ -d <parent-ref>@origin
```

Rebase is the default while the workspace's changes are unpublished. Once its
bookmark is pushed, merge instead, so published commits are not rewritten:

```sh
jj new <prefix>/<name> <parent-ref>@origin -m "Merge <parent-ref> into <prefix>/<name>"
```

Use the same merge form to bring in another workspace's bookmark or ref. jj
records conflicts in the change instead of stopping: resolve the conflicted
files, then `jj squash` the fix into the conflicted change. jj refuses to push a
conflicted commit.

The remaining pull sections apply to operator workspaces.

### Default Pull

With no argument, pull from the current workspace's parent ref:

1. Resolve current workspace and source slots.
2. Require the current workspace source to be clean before merging. If dirty,
   stop and ask whether to commit/push first.
3. Resolve the parent ref for each slot.
4. If the parent ref corresponds to another local workspace branch, use that
   parent workspace as the source and apply the workspace-to-workspace pull flow
   below.
5. Apply the pull-source worktree validation above to the parent ref.
6. Inspect the source-cache path reported by GitOps topology; do not reconstruct
   it from a source name. If the merge reads that cache branch and it is behind
   upstream and clean, refresh it through the source owner. When no native
   command exposes that refresh, the narrow exception is
   `git -C <reported-cache-path> pull --ff-only`. This refresh is within the pull
   request; it does not authorize editing framework files in the clone cache.
7. Merge the parent into the workspace with Angee:

```sh
angee --root "$angee_root" ws sync-base <current-workspace> --merge
```

Use `--rebase` only when the user explicitly asks for rebase. Merge is the
default because `/pull` is branch-style integration, not history rewriting.

### Pull From Another Workspace Or Ref

With an argument, merge that workspace or ref into the current workspace:

1. Resolve the argument:
   - If it is a workspace name, read
     `angee --root "$angee_root" ws git <source> --json`.
   - If it is a branch/ref, use it directly.
2. For a source workspace:
   - Stop if the source workspace is dirty unless the user explicitly
     overrides.
   - Stop if it has committed but unpublished work unless the user explicitly
     overrides.
   - Match source slots to current slots by `slot`.
3. For a branch/ref argument, apply the pull-source worktree validation above.
4. Require the current workspace source slot to be clean.
5. Merge with Angee:

```sh
angee --root "$angee_root" ws source merge <current-workspace> <slot> <source-branch-or-ref>
```

For a workspace argument, `<source-branch-or-ref>` is the source slot's reported
`branch`, usually `<prefix>/<source>`. Repeat the merge for matching framework
and external-addon worktree slots in BOTH workspaces; a slot only one side has
is skipped and reported. The work-state checkout follows its own upstream.

If the merge conflicts, inspect the conflict files, resolve them according to
the repo's owners and `AGENTS.md`, then commit the merge. If the user wants to
abandon the merge, use:

```sh
angee --root "$angee_root" ws source merge-abort <current-workspace> <slot>
```

## Push: Commit And Publish Current Workspace

`/push` means "commit the current workspace changes and publish the workspace
branch." It does not merge into the parent branch.

### Push From A jj Workspace

Review the change with `jj status` and `jj diff`, then seal and publish it:

```sh
jj commit -m "<message>"
jj bookmark set <prefix>/<name> -r @-
jj git push -b <prefix>/<name>
```

`jj commit` takes the whole working copy; there is no staging area. Pass paths
to `jj commit` to seal only those files, and keep generated runtime output,
scratch artifacts, and test reports out of the change as for an operator
workspace. The first push tracks the new bookmark. Report the change, the
bookmark, and that `<prefix>/<name>@origin` matches it.

The numbered steps below apply to operator workspaces.

1. Resolve the current workspace and source slots.
2. Inspect `angee --root "$angee_root" ws git <name> --json`, then
   `git -C <slot-path> status --short --branch` per slot — the workspace root
   itself is not a git repository.
3. If there are changes, review them before staging.
   - Stage source changes deliberately.
   - Do not stage generated runtime output, scratch artifacts, `.vite`, test
     reports, or agent-only bookkeeping unless the user explicitly asked for
     them.
   - Do not use `git add .` blindly from the repository root.
4. Commit when there are staged changes. Use the user's message when provided;
   otherwise write a concise message from the diff.
5. Publish each workspace source slot:
   - If the slot has an upstream, push it:

```sh
angee --root "$angee_root" ws source push <workspace> <slot>
```

   - If the slot has no upstream, publish it and set upstream:

```sh
angee --root "$angee_root" ws source publish <workspace> <slot> --remote <remote> --branch <branch>
```

   `<branch>` comes from the source slot's reported `branch`. `<remote>` is the
   slot's push remote as git resolves it (`git -C <slot-path> config
   remote.pushDefault`, else `origin`); never publish a stack-prefixed branch to a
   remote that only accepts `main`, `release/*`, `feature/*`, and `dev-*/*`.
   Publish only slots with commits beyond their base ref; an empty branch on an
   untouched repository is noise.
6. When present, the `work-state` slot is a slot like any other: inspect its
   reported branch and source kind. Commit and push its changes continuously
   (`ws source push <workspace> work-state` for a Git source; the `jj` publish
   recipe in `.work/AGENTS.md` when `.work/.jj` exists), within the user's
   authorization. A user instruction not to commit or push applies to this slot
   too. Do not assume it uses the workspace branch or a particular upstream.
7. Re-run `angee --root "$angee_root" ws git <name> --json` and report whether
   each slot is clean and pushed.

Publishing usually needs network access. If the command fails because of
sandboxed network access, request escalation for the same command with a scoped
justification.

## Safety Stops

Stop and ask before proceeding when:

- Current workspace cannot be determined.
- Parent ref cannot be determined from Angee state.
- Current workspace has uncommitted changes during `/pull`.
- Source workspace has uncommitted changes during workspace-to-workspace pull.
- Any local worktree for the pull source is dirty.
- Any local worktree for the pull source is clean but unpushed, unless the user
  explicitly overrides.
- Any local worktree for the pull source is diverged from upstream.
- A merge or rebase is already in progress and the user did not ask to continue
  or abort it.
- The requested operation would rewrite a published/shared branch.

## Reporting

For pull, report source, target, merge method, conflicts if any, and final
workspace git state.

For push, report commit SHA/message when a commit was created, published branch,
remote/upstream, and final `pushed` state.
