# Templates

Copier templates the Angee operator renders. Each template's `copier.yml` carries
an `_angee` block — `schema`, `kind`, `name`, `description` — and that description
is the template's contract. This file is the map to the kinds; it is not a second
copy of each one.

## Kinds

| dir | kind | renders |
|-----|------|---------|
| `projects/` | `project` | a downstream host repo — `manage.py`, `settings.yaml`, the consumer-addon namespace, the web package; owns the project root |
| `addons/` | `addon` | a source addon package, including the frontend manifest, i18n, tests, and package wiring |
| `stacks/` | `stack` | a runnable `angee.yaml` the operator brings up on docker-compose / process-compose |
| `workspaces/` | `workspace` | a set of sources + agentic config, materialized as files |
| `services/` | `service` | one long-running service added to a stack |

For what a template scaffolds and its inputs, read its `copier.yml` `_angee.description` —
that is the owner.

## Stacks: dev overlay vs local instance

One `angee.yaml` describes a stack in either of the two layouts covered by
[Concepts](https://docs.angee.ai/guide/concepts) and the [glossary](../docs/glossary.md);
the difference is where the stack root sits and where its sources come from. Both
render from ONE shared manifest body (`stacks/_shared/stack-body.yaml.jinja`): each
template's `angee.yaml.jinja` is a thin `{% set %}` header (mode + address variables)
that includes it, and `{% if runtime_mode == "process" | "docker" %}` branches cover
only where the two modes differ. Both collapse the first-run lifecycle into one
`manage.py angee provision` command.

- **`stacks/dev`** (`runtime_mode: process`) — a gitignored `.angee/` overlay
  (`ANGEE_ROOT=.angee`) on a framework checkout, with `local` sources pointing at that
  checkout, run on process-compose. Added with `angee init --dev`. For developing the
  framework (or a consumer) against live source; it runs the checkout's example. The
  lifecycle is a single `provision` job.
- **`stacks/local`** (`runtime_mode: docker`) — a self-contained instance where the
  root folder *is* the stack **and** a git-controlled project (`ANGEE_ROOT=.`), run on
  docker-compose. You own the root. By default (`framework=source`) the django/celery
  services run the deps-only base image and link the framework editable from a local
  `sources/angee-django` checkout at container start; `framework=baked` runs a
  code-baked runtime image instead. `provision` runs inside the django container. This
  is how you run your own Angee app locally on a real (Postgres + pgvector) database.

### The `local` root is a git-controlled project

Commit what you author; ignore what a tool regenerates:

```
<root>/                    # project == stack (ANGEE_ROOT=.)
  ── committed ──
  angee.yaml               # stack: operator · postgres · django · frontend ingress
  manage.py                # ANGEE_PROJECT_DIR = here
  settings.yaml            # INSTALLED_APPS (base) · DATABASE_URL → postgres
  addons/<ns>/             # your addons
  web/                     # your frontend
  runtime/                 # composer output — the pinned, deployed artifact
  .copier-answers.yml · .gitignore
  ── gitignored ──
  data/ · .env · run/ · sources/ · docker-compose.yaml · process-compose.yaml
```

`runtime/` is **committed**: a local instance is a real deployment, so the built
concrete apps + SDL are the artifact you deploy and review. (A dev overlay
regenerates it disposably, so it is ignored there.) Everything a tool regenerates —
resolved secrets, the operator's compiled compose files, materialized sources, the
Postgres volume — stays out of git. The database is a stack service and the app
reads `DATABASE_URL`, so no SQLite file lives in the tree. Add the framework's
example later by adding a source onto `angee-django/examples` and enabling it in
`INSTALLED_APPS`.

> **Status.** The `local` template now *is* this shape: a thin `kind: stack` that
> chains `projects/web` and includes the shared manifest body in `docker` mode. By
> default (`framework=source`) its django/celery services run the deps-only
> `ghcr.io/ang-ee/django-angee-base` image and link the framework editable from a
> `sources/angee-django` checkout at container start (clone it at the stack root first);
> `framework=baked` runs the code-baked `ghcr.io/ang-ee/django-angee` runtime image
> instead. It runs on `pgvector/pgvector:pg17`, drives first start through
> `manage.py angee provision --bootstrap-admin` (which bootstraps a generated `admin`
> user), and serves the built SPA through Caddy. The one-command render uses the
> operator's template-chain resolver (`ang-ee/angee-operator#39`); once that lands in a
> released operator, `angee stack init` renders host + overlay in one step. The web
> image rebuild depends on the published `hatch-angee` release that ships addon
> `web/schema/` directories. Until those releases are available, use the two-copier-step
> render below.

## Run a local stack

Once the operator's chain resolver ships, one command renders host + stack. The
default `framework=source` links the framework editable from a local checkout at
container start, and `stack init` validates that source exists — so clone it first:

```sh
mkdir -p ~/.angee/sources
git clone https://github.com/ang-ee/angee-django ~/.angee/sources/angee-django
angee stack init https://github.com/ang-ee/angee-django/tree/main/templates/stacks/local ~/.angee
angee dev --root ~/.angee
export ANGEE_OPERATOR_URL=http://127.0.0.1:9000
export ANGEE_OPERATOR_TOKEN="$(awk -F= '$1=="ANGEE_SECRET_OPERATOR_TOKEN"{print $2}' ~/.angee/.env)"
```

### Update an existing local stack

`angee stack update --template` re-renders `angee.yaml` from the stack template
recorded in `.copier-answers.stack.yml`, then regenerates the derived runtime
files. If the stack was initialized from an unpinned template ref such as
`.../tree/main/templates/stacks/local`, template fixes are picked up by:

```sh
angee stack update --root ~/.angee --template
```

If the stack was initialized from a pinned tag, first re-render from the newer
template tag with `angee stack init ... --force`, or update the recorded template
ref intentionally before running `stack update --template`.

Stacks rendered before `0.1.6` may record the local catalog name
`template.active: stacks/local`; outside an Angee source checkout,
`stack update --template` cannot resolve that name. Patch the active ref once,
then update from the template:

```sh
sed -i.bak \
  's#active: stacks/local#active: https://github.com/ang-ee/angee-django/tree/v0.1.7/templates/stacks/local#' \
  ~/.angee/angee.yaml
angee stack update --root ~/.angee --template
```

For the `0.1.5` local-stack layout change, stop the stack before moving the
database directory:

```sh
angee down --root ~/.angee
mkdir -p ~/.angee/data
mv ~/.angee/pgdata ~/.angee/data/pgdata
angee stack update --root ~/.angee --template
angee dev --root ~/.angee
```

Until then, render the two templates in sequence (the stack overlays the host),
then bring it up. First start emits the local runtime, migrations, and GraphQL
schemas before starting Django and the selected frontend ingress:

```sh
copier copy .../templates/projects/web ~/.angee
copier copy --overwrite .../templates/stacks/local ~/.angee
angee dev --root ~/.angee
```
