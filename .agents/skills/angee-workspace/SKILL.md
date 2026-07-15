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
- Raw Git is only for committing reviewed changes, inspecting worktrees, and
  checking upstream state that Angee does not expose.

Resolve the controlling stack root before any workspace or GitOps command. Run
those commands with the resolved root explicitly, even while inside a source
checkout or target workspace. Never `git checkout` or `git switch` inside an
Angee workspace; a workspace is pinned to its `workspace/<name>` branch.

## Resolve The Controlling Stack Root

An existing current or ancestor stack owns workspace lifecycle. Search upward
from the repository root for `angee.yaml` first; only when no ancestor stack
exists may this repository's `.angee/angee.yaml` dev overlay be the owner:

```sh
repo_root=$(git rev-parse --show-toplevel) || exit 1
angee_root=
candidate=$repo_root
while :; do
  if test -f "$candidate/angee.yaml"; then
    angee_root=$(cd "$candidate" && pwd -P)
    break
  fi
  parent=$(dirname "$candidate")
  test "$parent" != "$candidate" || break
  candidate=$parent
done
if test -z "$angee_root" && test -f "$repo_root/.angee/angee.yaml"; then
  angee_root=$(cd "$repo_root/.angee" && pwd -P)
fi
test -n "$angee_root" || exit 1
```

If neither location exists, stop and ask the user how the stack should be
initialized; do not run `angee init` unless the user explicitly requested a new
stack. Never initialize `.angee/` inside a checkout that already sits below a
stack root.

Use `angee --root "$angee_root" ...` for every workspace and GitOps command
below. The explicit root is intentional: the CLI otherwise defaults to the
current directory, which can silently select a checkout-local dev overlay.

## Resolve The Current Workspace

1. If the current directory is under `$angee_root/workspaces/<name>`, use
   `<name>`.
2. Otherwise require the workspace name from the command arguments or user
   prompt.
3. Confirm with `angee --root "$angee_root" ws git <name> --json`.
4. If there is more than one git source slot, operate per slot. Match slots by
   name when pulling from another workspace.

For each workspace source, the parent ref is the source `ref` reported by
`angee --root "$angee_root" ws git <name> --json` or the matching link in
`angee --root "$angee_root" gitops topology --json`. That ref may be a normal
branch (`main`), a feature branch, or another workspace branch
(`workspace/<parent>`).

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

## Create Workspace

Resolve and validate the shared work-state repository before choosing the
workspace parent:

```sh
test -L "$repo_root/.work" || exit 1
work_state_path=$(cd "$repo_root/.work" && pwd -P) || exit 1
work_state_top=$(git -C "$work_state_path" rev-parse --show-toplevel) || exit 1
test "$work_state_top" = "$work_state_path" || exit 1
test "$(basename "$work_state_path")" != "$(basename "$repo_root")" || exit 1
```

These checks require the repository-root `.work` symlink, resolve it to an
absolute canonical directory, verify that directory is the top level of a valid
Git repository, and keep the private work-state repository distinct from the
public source repository. If any check fails, stop; do not fall back to
`docs/superpowers` or invent another path.

Use the dev workspace template unless the user names another template, and pass
the validated canonical path through the template's required input:

```sh
angee --root "$angee_root" ws create <name> --template dev --input base_ref=<parent-ref> \
  --input work_state_path="$work_state_path"
```

Choose `<parent-ref>` in this order:

1. An explicit argument from the user.
2. The current workspace branch, when creating a child workspace from inside a
   workspace.
3. The current branch at the repository root.
4. Ask the user; do not silently fall back to `main` when the parent is unclear.

After creation, report:

- Workspace path.
- Branch name.
- Parent ref.
- Resolved work-state path.
- `angee dev` command from the workspace root.
- `angee --root "$angee_root" ws status <name>` for follow-up inspection.

## Pull: Bring Changes Into Current Workspace

`/pull` means "get changes into the current workspace branch." It does not
publish, and it does not commit unrelated working-tree changes.

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
6. If the parent branch is behind upstream and clean, fast-forward it with
     `git -C <parent-path> pull --ff-only` when network approval is available.
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
`branch`, usually `workspace/<source>`.

If the merge conflicts, inspect the conflict files, resolve them according to
the repo's owners and `AGENTS.md`, then commit the merge. If the user wants to
abandon the merge, use:

```sh
angee --root "$angee_root" ws source merge-abort <current-workspace> <slot>
```

## Push: Commit And Publish Current Workspace

`/push` means "commit the current workspace changes and publish the workspace
branch." It does not merge into the parent branch.

1. Resolve the current workspace and source slots.
2. Inspect `angee --root "$angee_root" ws git <name> --json` and
   `git -C <workspace-path> status --short --branch`.
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
angee --root "$angee_root" ws source publish <workspace> <slot> --remote origin --branch <branch>
```

   `<branch>` comes from the source slot's reported `branch`.
6. Re-run `angee --root "$angee_root" ws git <name> --json` and report whether
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
