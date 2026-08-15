# Upgrade: the repo split

Angee's monorepo split into sibling repositories. If your stack tracked
`angee-django` before this release, pulling it removes trees your stack
references — follow this before restarting anything.

## What moved where

| Was (in angee-django) | Now |
|---|---|
| `angee/web/*` (`@angee/app`, `ui`, `refine`, `metadata`) + `packages/{storybook,e2e}` | the **angee-react** repo (flat: `app/ ui/ refine/ metadata/ storybook/ e2e/`) |
| `addons/angee/*` (base addons) | the **angee-base** repo (`addons/angee/*`, layout unchanged) |
| matrix, whatsapp, telegram, discord, signal, imessage, facebook, meta bridges | the **angee-messaging-bridges** repo — **opt-in by cloning** |
| `examples/notes-angee/addons` (`example.notes`) | the **angee-examples** repo |

What did **not** change: addon names, `INSTALLED_APPS` entries, the
`django-angee` distribution's dependencies and the `matrix` extra (they stay
until the dependency projection ships), your data, your migrations.

## 1. Add the new sources

Declare the repos your stack composes from in `angee.yaml` `sources:`
(git kind, `cache_path: sources/<name>`): `angee-react`, `angee-base`, and —
only if you compose any bridge addon — `angee-messaging-bridges`; add
`angee-examples` if you enable `example.notes`. Fetch them
(`angee source fetch <name>` or the next `angee dev` materializes them).

## 2. Point the project at the addon trees

`settings.yaml` — one explicit line per addon repo, `{BASE_DIR}`-relative:

```yaml
ANGEE_ADDON_DIRS:
  - "{BASE_DIR}/addons"                                  # your own addons
  - "{BASE_DIR}/sources/angee-base/addons"
  - "{BASE_DIR}/sources/angee-messaging-bridges/addons"  # only if you use bridges
  - "{BASE_DIR}/sources/angee-examples/addons"           # only for example.notes
```

An `INSTALLED_APPS` entry with no providing tree fails at boot with an
import error naming the addon — that is your signal a dir line is missing.

## 3. Point the JS workspace at angee-react

In your stack's `pnpm-workspace.yaml`, replace the old globs
(`sources/angee-django/angee/web/*`, `sources/angee-django/packages/*`,
`sources/angee-django/addons/angee/*/web`) with:

```yaml
  - "sources/angee-react/app"
  - "sources/angee-react/ui"
  - "sources/angee-react/refine"
  - "sources/angee-react/metadata"
  - "sources/angee-react/storybook"
  - "sources/angee-base/addons/angee/*/web"
  - "sources/angee-messaging-bridges/addons/angee/*/web"   # only with bridges
```

If a `storybook` service runs from the framework checkout
(`workdir: source://framework` + its own `pnpm install`), change it to run
in **your stack's workspace** (`workdir: source://app`, drop the private
`pnpm install`). **Never run `pnpm install` inside a linked checkout**: a
private install forks dependency identities for every linked package — the
symptom is a context-provider error at runtime ("No QueryClient set",
"nuqs requires an adapter").

## 4. Refresh and restart

```sh
uv sync --project sources/angee-django --extra postgres   # refresh the editable
pnpm install                                              # relink the workspace
angee dev                                                 # or restart services
```

The boot re-composes, `rebac sync` reads permissions from the new trees, and
codegen regenerates — no migrations, no data changes.

## Recommended instead of 2–3: materialize the `src` workspace

Steps 2–3 keep the old consume-the-cache layout working. The supported
destination is the `workspaces/src` template: every source repo as a sibling
worktree slot under one workspace, with your consumption pointing at
`workspaces/src/<name>` instead of `sources/<name>` — sources become a pure
clone cache. New stacks get this by default; existing stacks can adopt it as
step 5 with the same edits re-anchored (the sibling-relative shape is
identical).

## Notes

- **Bridges are opt-in now.** No bridge repo cloned = no bridge code
  present; remove bridge entries from `INSTALLED_APPS` if you don't use
  them. Matrix additionally still needs `django-angee[matrix]`.
- Consumer repos with hand-written globs into the old monorepo layout
  (e.g. `../angee-django/angee/web/*`) need the same re-pointing to their
  `../angee-react/*` siblings.
- Dependencies are unchanged in this release: the `django-angee` editable
  still carries the full dependency table, so no `pyproject.toml` changes
  are required. The per-addon dependency projection arrives separately.
