# Dev stack: converge on the disconnected (installed-package) shape

## Why

The `templates/stacks/dev` stack does double duty: it is both the **framework's
own self-host dev environment** and the advertised "generic consumer dev stack"
(`copier.yml`: "for any consumer project under angee-django or downstream"). The
framework-dev concerns leak into the consumer surface through `framework_path`
and `source://framework`. That seam is where the recent gonja parse regression
landed (see "Immediate fix" below).

The target end-state is a project that consumes Angee as **published packages**
(`django-angee` from PyPI, `@angee/*` from the npm registry) with no framework
source tree on disk. In that world `framework_path` and most of the stack's
machinery do not simplify — they disappear.

## Principle that drives the design

The choice "framework from local source vs. from the registry" is owned by the
**dependency manifests**, not the stack template. The stack template owns process
orchestration only. So the template should already be the lean consumer shape;
the local-vs-registry switch is a thin, removable redirect in the manifests.

- Python: `[project.dependencies]` stays registry-shaped (`django-angee`);
  `[tool.uv.sources]` redirects to a local editable path *for now*. Delete that
  block to go to PyPI.
- JS: `dependencies` stay registry-shaped semver; `pnpm.overrides` redirect
  `@angee/*` to `link:` local folders *for now*. Delete the block to go to npm.

Switching local→registry then touches two manifest blocks and **zero** template
lines.

## What the disconnected stack drops (vs. today)

- `sources.framework` + the `framework_path` copier input — no checkout to point at.
- `storybook` service — `@angee/storybook` is the framework's component workshop,
  not a consumer concern.
- `operator-schema` + `operator-codegen` jobs — the published `@angee/operator`
  ships its console prebuilt; consumers don't regenerate framework console types.
- the `_proj`/`_fw`/`_under`/`_up` reload-dir block — you don't edit installed
  wheels, so the `django` service just runs plain `--reload` on the project.

What stays, unchanged, all from `source://app`: `build`, `makemigrations`,
`migrate`, `permissions` (rebac sync), `resources`, `schema`, the `django`
service, and the `operator` daemon. The Python side already runs `uv run …` from
`source://app` and never touched `framework_path` — it is disconnected-ready
today. `deps`/`frontend` repoint from `source://framework` to the project's own
`web/`.

## Immediate fix (done in this change)

Make the existing template render again, in the target direction — do **not**
invest in correcting the over-engineered path math that the end-state deletes.

- The `django` service now emits plain `--reload` (watches the project source,
  i.e. `source://app`). The `_proj`/`_fw`/`_under`/`_up` block and the
  conditional `--reload-dir` args are removed.
- This both fixes the gonja parse error (the omitted-start slice `_proj[:n]`,
  which gonja's parser rejects) and removes a block that was also semantically
  wrong for the default monorepo case (`framework_path = "."` made `_under`
  false and `_up` off by one). The block had never rendered since it was
  introduced in `81728a45`, so no working behavior is lost.

### Known gap accepted for now

In the monorepo layout, editing **framework** source (`angee/`, `addons/angee/`
at the repo root) no longer hot-reloads the Django service — uvicorn `--reload`
watches only the app workdir. Consumer code under the project *is* watched. The
proper home for any framework-watch `--reload-dir` is the **operator** (it
resolves both the app and framework source paths and can compute the relative
hop with real path ops); it does not belong as path arithmetic in a constrained
gonja template. Deferred to the phased work below.

## Phased work (future)

1. **Split the stack into two templates.**
   - `stacks/dev` (framework self-host): keeps `source://framework`, storybook,
     operator-schema/codegen, and operator-owned framework reload-dirs.
   - `stacks/consumer` (lean): the disconnected shape above.
2. **Make a standalone consumer the reference.** Scaffold a real consumer project
   (the sibling layout `copier.yml` already anticipates) that depends on
   `django-angee` + `@angee/*` via the redirect layer, leaving the in-repo
   `examples/notes-angee` as the framework self-host. Recommended over converting
   the bundled example, which would pull it out of the root uv env / pnpm
   workspace and weaken the monorepo's self-host convenience.
   - Python: project `pyproject.toml`, `dependencies = ["django-angee"]`,
     `[tool.uv.sources] django-angee = { path = "../..", editable = true }`.
     (Today both source roots ship in the one `django-angee` wheel; add
     `django-angee-addons` when the split lands.)
   - JS: project `web/package.json` with registry-shaped `@angee/*` deps and a
     `pnpm.overrides` block of `link:` paths to `packages/*`.
3. **Operator owns reload-dirs.** If framework hot-reload is wanted in the
   self-host stack, have the operator compute the reload-dir list from the
   resolved app/framework paths and pass it into the template as ready-made
   values — no path math in the template.
