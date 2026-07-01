# Template taxonomy, project-as-template, and the wheel-portable web scaffold

Design note + implemented slice. Captures the taxonomy/inheritance model we
settled on for scaffolding downstream Angee projects, the constraints discovered
while implementing it, and the first concrete change that landed (de-sibling the
web scaffold so it survives a pure `pip install django-angee`).

## The unit model — a project *is* a template

Rendering the **project template** births a self-contained project root that
carries two things:

1. **the host** — `manage.py`, `settings.yaml` (INSTALLED_APPS), `addons/<ns>/`,
   the `web/` package;
2. **the project's own `templates/`** — its dev and prod **stack** templates,
   which *inherit* from Angee's base stack templates rather than copying them.

`angee` run from inside the project renders the dev or prod stack **from the
project's own templates**:

```
myproject/                       ← a rendered PROJECT = the root
├── manage.py  settings.yaml  addons/<ns>/  web/     ← the HOST
├── README.md  .gitignore  .copier-answers.yml
└── templates/                    ← the project's OWN stack templates
    └── stacks/{dev,prod}/        ← thin: extends an Angee base + project deltas

angee dev              → renders templates/stacks/dev  → angee.yaml (dev flavor)
```

The layering is **base (Angee) → project**, not "workspace wraps project." A
workspace is the optional dev-isolation shell (worktree + port pools) that can
render a project; the **project owns the host and its stack templates**.

## Inheritance — `extends` over go-getter refs, no invented grammar

Copier has no native `extends`, so Angee adds one, and the base a child extends is
a **ref** resolved by prior-art grammar — lift **go-getter** (the Go/Terraform
library the operator already backs `--template` with), never a bespoke scheme:

```
github.com/ang-ee/angee-django//templates/stacks/dev?ref=v0.7.3
git::ssh://git@github.com/acme/fork//templates/stacks/dev?ref=main
./templates/stacks/dev                       # local path — same grammar
```

- `//subdir` = the template dir (a plain file path in the source);
- `?ref=` = tag/branch/commit (the version pin);
- source scheme = whatever the operator's resolver already speaks
  (`gh:…`/`github.com/…`), **not** a new `gh://`.

A project's stack template is then thin — a ref plus only the deltas; the ~10 KB
`angee.yaml.jinja` body is never copied:

```yaml
# myproject/templates/stacks/dev/copier.yml — the whole file
_angee:
  kind: stack
  name: dev
  extends: github.com/ang-ee/angee-django//templates/stacks/dev?ref=v0.7.3
  overrides:
    project_path: .
```

**No `angee:` alias.** We considered `angee:stacks/dev` as registry sugar that
expands to the framework default at the installed version, but dropped it: a
direct `gh` ref (or a dedicated `ang-ee/templates` repo) keeps the grammar 100%
stock. `copier` stays the renderer and records `_src_path` + `_commit`, giving
`copier update` for framework upgrades.

### Distribution: Option A (in-repo), B as escape hatch

- **A (chosen): templates stay in `angee-django`.** Version-locked *by
  construction* (same tag = same commit as the wheel), free wheel-bundling for
  offline, one release train. `depth=1` clones keep it cheap.
- **B (escape hatch): dedicated `ang-ee/templates` monorepo.** Tiny clones,
  independent cadence, natural home for third-party templates — but versions
  decouple from the wheel and offline needs separate vendoring. Split to it only
  if template cadence must break free of framework releases; the refs change,
  nothing else does.
- **C (rejected): repo-per-template.** Copier-canonical but repo sprawl; fights
  the monorepo.

## Wheel vs source — the installed package is the one anchor

A downstream may **not** clone the framework; it may `pip install django-angee`
+ install the JS from the wheel. So "where is the framework" is the *installed
package*, discovered once, never a sibling path:

| Fact the project needs | Old (sibling assumption) | Now (installed-package anchor) |
|---|---|---|
| Python settings | `angee.compose.settings` | same — already location-free |
| JS runtime libs | `@angee/*` from `../../angee-django` | `@angee/*` from the wheel's `angee/web/*` |
| codegen bin | `angee-web-codegen` | `@angee/app` export (already) |
| **web build-config** | `../../angee-django/vite.shared` | **`@angee/app/vite` · `@angee/app/vitest`** ⟵ this pass |
| base templates | `templates → ../templates` symlink | go-getter ref (network) or wheel-bundled (offline) |

The dangling `templates → ../templates` symlink (broken in arpee, which rendered
with `ANGEE_ROOT=.`) is retired by ref resolution; it is not reintroduced.

## What landed in this pass — de-sibling the web scaffold (change #1)

Goal: a web package rendered from `templates/projects/web` reaches the framework's
build-config **by package name**, so it resolves against an editable checkout
*and* an installed wheel, with no `../../angee-django/*` sibling path.

- **`@angee/app` now owns the neutral build-config** as subpath exports:
  `@angee/app/vite` (`config/vite.ts` — was root `vite.shared.ts`) and
  `@angee/app/vitest` (`config/vitest.ts` — the neutral half of root
  `vitest.shared.ts`). Optional `peerDependencies` (`vite`, `vitest`,
  `@vitejs/plugin-react`, `@tailwindcss/vite`) declare the build-tooling contract;
  in-repo they resolve from the hoisted root, downstream from the web project.
- **Repo-root files became thin fixture wrappers.** `vitest.shared.ts`
  re-exports the neutral builders (by *relative* path — see constraint below) and
  adds only the in-repo notes-example `gqlAlias` fixture. `vite.shared.ts` was
  deleted (its one consumer, the notes example, now imports `@angee/app/vite`).
- **The web template is de-sibled.** `vite.config`/`vitest.config` import
  `@angee/app/vite`/`@angee/app/vitest`; the three `framework_*` sibling-path
  inputs are gone from `copier.yml` (only `project_title`, `package_name`,
  `web_path`, `react_version` remain).
- **hatch-angee ships a `config` seam** (`AddonSourcesBuildHook._ship`, v0.1.3) so
  `angee/web/app/config/` rides in the wheel — otherwise the subpath exports
  would resolve only from an editable checkout. Verified the hook force-includes
  `angee/web/app/config` against the real tree.

### Constraints discovered (write these down — they shaped the design)

1. **oxc does not resolve package-name tsconfig `extends`.** `tsc` resolves
   `"extends": "@angee/app/tsconfig.base.json"`, but Vite's oxc tsconfig loader
   (used by `vite` and `vitest`) does not — it errors "Tsconfig not found". So:
   - the **repo-root** `tsconfig.base.json` reaches the shared contract by a
     *relative* path only (in-repo);
   - the **downstream template** `tsconfig.json` **inlines** the compiler options
     (self-contained, like `create-vite`). The `@angee/app/tsconfig.base.json`
     package export was tried and reverted — it had no working consumer.
2. **Build tooling must not live in the runtime package's typecheck.** Adding
   `vite` to `@angee/app` *devDeps* pulled a second `@types/node@26` into app's
   type graph and broke `pnpm --filter @angee/app typecheck`. Fix: keep the config
   under `config/` (outside the `src` include glob so it is never an entry point in
   app's program) and rely on hoisting for in-repo resolution; the tooling is only
   an optional *peer*, never an app dep.
3. **Pre-existing, not ours:** `@angee/app` `ResourceList.test.tsx` has 12
   grouped-view failures on clean HEAD; `@refinedev/core`/`papaparse` lib
   typecheck errors are library-internal. Confirmed via stash test.

### Verified

`@angee/app` typecheck clean; tests pass — ui 293, refine 41, resources 16,
iam 16, notes-host 9 (loads `@angee/app/vite`); `templates/projects/web` renders
with `@angee/app/vite|vitest` and an inlined tsconfig, zero sibling paths;
hatch-angee suite 15 passed; config seam force-included for `@angee/app`.

## Next steps (not in this pass)

1. **The full project template** — expand `templates/projects/web` (or a new
   `projects/<name>`) to emit the whole host: `manage.py`, `settings.yaml` (the 9
   base addons, all present, + a commented consumer-addon slot), `addons/<ns>/`,
   `README.md`, `.gitignore`, plus the project's own `templates/stacks/{dev,prod}`
   as thin `extends` manifests. Validate the render reproduces hand-made
   `../arpee-angee` (minus its bespoke `specs/`).
2. **The `extends` keyword + go-getter ref resolution** in the operator (Go repo)
   — render a base template from a ref, overlay the child's inputs/files. Until
   then, validate with `copier` directly.
3. **A project-render verb** — `angee init stack` only renders `kind: stack`; no
   `angee` verb renders `kind: project` yet. Either teach the operator to render a
   project, or make the standalone bootstrap a `kind: stack` template that chains
   the project. (arpee is "a workspace with a stack rendered into it".)
4. **End-to-end wheel proof** — build a `django-angee` wheel, install it in a
   throwaway project, and confirm `@angee/app/vite` resolves from site-packages
   (the config seam is verified at the hook level; the full build is the belt).
