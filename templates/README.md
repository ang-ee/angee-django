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

## Stacks: framework-dev vs local instance

One `angee.yaml` describes a stack in either of the two layouts covered by
[Concepts](https://docs.angee.ai/guide/concepts) and the [glossary](../docs/glossary.md);
in both, the root folder *is* the stack **and** the project host (`ANGEE_ROOT=.`) —
the difference is the runtime and where framework code comes from. Both
render from ONE shared manifest body (`stacks/_shared/stack-body.yaml.jinja`): each
template's `angee.yaml.jinja` is a thin `{% set %}` header (mode + address variables)
that includes it, and `{% if runtime_mode == "process" | "docker" %}` branches cover
only where the two modes differ. Both chain `projects/web` to scaffold the host.
Their manifests expose preparation as explicit jobs: framework dependency install,
provision, operator schema refresh, and codegen; local instances replace framework
codegen with a completed static frontend build. Python jobs and services share one
runtime/environment include, while each container synchronizes its own virtualenv.

These manifests require operator support for readiness and chained jobs. A successful
addon settings edit can restart the declared entry job with `chained_restart`, which
replays its dependency graph before the running application restarts. Failed jobs stop
the chain instead of restarting against incomplete generated state.

- **`stacks/dev`** (`runtime_mode: process`) — the framework-dev stack, run on
  process-compose. It declares the consolidated framework plus optional external
  git `sources:` and the `src` workspace; `angee dev` materializes their clone
  caches, cuts the workspace, and runs Django/Celery/Vite/Storybook as local
  processes against those worktrees. The rendered pyproject resolves
  `django-angee` editable from the consolidated `angee` slot, so
  `uv run manage.py` works bare from the root. For developing the framework —
  or a consumer project against live framework source.
- **`stacks/local`** (`runtime_mode: docker`) — a self-contained instance run on
  docker-compose. You own the root. By default (`framework=source`) the django/celery
  services run the deps-only base image and link the framework editable from a local
  `sources/angee` checkout at container start; `framework=baked` runs a
  code-baked runtime image instead. Provision and operator-schema are completed jobs;
  the frontend build is another real job. Caddy starts after that build, then Django
  starts once its named trusted proxy is ready. This avoids a proxy DNS startup
  cycle. This runs your own Angee app locally on a real (Postgres + pgvector) database.

Before initializing either layout, check for an existing current or ancestor
`angee.yaml`. If one exists, it owns the checkout; never initialize a stack
under a source checkout. Both templates render the same root `AGENTS.md`
contract so code agents retain that ownership rule while working below
`sources/` or `workspaces/`.

### Connect framework-dev stacks to shared PostgreSQL and Redis servers

`stacks/dev` runs a pgvector container and a Redis container per stack by
default. With `postgres_mode: external` it connects to a PostgreSQL server it
does not run and renders no Postgres service, port lease, or `pgdata`
persistence. With `redis_mode: external` it does the same for Redis and renders
no Redis service or port lease. The inputs are declared in
[`stacks/dev/copier.yml`](stacks/dev/copier.yml).

To share one server across several stacks, give each stack its own login role
and a database owned by that role. Stacks on one server share its
`max_connections`; size it with the connection budget in
[the stack guide](../docs/stack.md#stack-serve-mode) summed over every stack.

For a new stack, render it with the external inputs, store the role's password
as the `db-password` secret, then bring it up:

```sh
angee stack init <template> <root> --input postgres_mode=external \
  --input postgres_host=127.0.0.1 --input postgres_port=5433 \
  --input postgres_db=app_db --input postgres_user=app_role
angee --root <root> secret set db-password --stdin
```

`db-password: { required: true }` fails only when the secret is absent.

To convert an existing bundled stack:

1. Stop it with `angee down`.
2. Create the role with the stack's current `db-password`, or overwrite that
   secret with the role's password. The stack still holds the generated value,
   so nothing else catches a mismatch.
3. Run `angee stack update --template` with the external inputs. The update
   merges into the existing manifest and keeps keys the template no longer
   emits, so remove `services.postgres`, `ports.postgres`, and `persist.pgdata`
   from `angee.yaml` by hand.
4. Remove the stack's old Postgres container. Delete `data/pgdata` once its
   data has moved.

To share one Redis server, give each stack its own `redis_db` (cache and the
Channels layer) and `redis_broker_db` (Celery broker). Celery's queue keys are
plain queue names, so two stacks on one broker database consume each other's
tasks. The rendered URLs carry no password. A converted stack has the same merge
caveat: after `angee stack update --template`, remove `services.redis` and
`ports.redis` from `angee.yaml` by hand. Stop the stack's workers before the
switch; its queued broker messages stay in the old server.

`postgres_host` and `redis_host` are the servers as the stack's runtime reaches
them:

- **Process runtime:** the host as the stack's processes see it.
- **Docker runtime:** an address containers can reach, such as
  `host.docker.internal` on Docker Desktop or OrbStack. On a Linux engine, the
  operator does not add the host-gateway mapping to container jobs, so use a
  routable address there.
- **IPv6:** write literals in brackets.

### Report errors to Sentry

Both stack templates take `sentry_dsn` (Django, Celery, and management commands)
and `sentry_web_dsn` (the React SPA). Both are empty by default, and an empty
input renders nothing. With a DSN:

- Every Python service and job gets `SENTRY_DSN` and `SENTRY_ENVIRONMENT`.
- The frontend dev server and the production build get `VITE_SENTRY_DSN` and
  `VITE_SENTRY_ENVIRONMENT`. A production SPA bakes them in at build time, so
  changing the web DSN needs a rebuild.
- The environment follows the serve mode (`development` or `production`).
- A host renders `main.tsx` from the project template, which passes the web DSN
  to `bootApp`. A host rendered before that change must re-render `main.tsx`,
  or add the `errorReporting` option to its `bootApp` call by hand. Until then
  `sentry_web_dsn` has no effect there.

To turn reporting on for an existing stack, run `angee stack update --template`
with the input.

To turn it off, run the same update with an empty value. The update merges, so
it keeps the variables it no longer renders: then remove `SENTRY_DSN`,
`SENTRY_ENVIRONMENT`, `VITE_SENTRY_DSN`, and `VITE_SENTRY_ENVIRONMENT` from
`angee.yaml` by hand.

### The `local` root is a git-controlled project

Commit what you author; ignore what a tool regenerates:

```
<root>/                    # project == stack (ANGEE_ROOT=.)
  ── committed ──
  angee.yaml               # stack: operator · postgres · django · frontend ingress
  manage.py                # ANGEE_PROJECT_DIR = here
  settings.yaml            # INSTALLED_APPS (base) · DATABASE_URL → postgres
  AGENTS.md · CLAUDE.md    # stack-root ownership rules for coding agents
  addons/<ns>/             # your addons
  web/                     # your frontend
  runtime/                 # composer output — the pinned, deployed artifact
  .copier-answers.yml · .gitignore
  ── gitignored ──
  data/ · .env · run/ · sources/ · docker-compose.yaml · process-compose.yaml
```

`runtime/` is **committed**: a local instance is a real deployment, so the built
concrete apps + SDL are the artifact you deploy and review. (The framework-dev
stack regenerates it disposably, so it is ignored there.) Everything a tool regenerates —
resolved secrets, the operator's compiled compose files, materialized sources, the
Postgres volume — stays out of git. The database is a stack service and the app
reads `DATABASE_URL`, so no SQLite file lives in the tree. Add the framework's
in-repo example later by including `sources/angee/examples/addons` in
discovery and enabling it in `INSTALLED_APPS`.

> **Status.** The `local` template now *is* this shape: a thin `kind: stack` that
> chains `projects/web` and includes the shared manifest body in `docker` mode. By
> default (`framework=source`) its django/celery services run the deps-only
> `ghcr.io/ang-ee/django-angee-base` image and link the framework editable from a
> `sources/angee` checkout at container start (clone it at the stack root first);
> `framework=baked` runs the code-baked `ghcr.io/ang-ee/django-angee` runtime image
> instead. It runs on `pgvector/pgvector:pg17`, drives first start through a
> provision job running
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
git clone https://github.com/ang-ee/angee-django ~/.angee/sources/angee
angee stack init https://github.com/ang-ee/angee-django/tree/main/templates/stacks/local ~/.angee
angee dev --root ~/.angee
export ANGEE_OPERATOR_URL=http://127.0.0.1:9000
# `angee secret reveal` reads the stack's secrets backend — never hand-parse .env
# (its values are quoted, so an awk/cut one-liner captures the quotes).
export ANGEE_OPERATOR_TOKEN="$(angee secret reveal operator-token --root ~/.angee)"
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
  's#active: stacks/local#active: https://github.com/ang-ee/angee-django/tree/main/templates/stacks/local#' \
  ~/.angee/angee.yaml
angee stack update --root ~/.angee --template
```

For the framework-dev (`stacks/dev`) data-layout change — everything lives
under `./data` now — stop the stack, move the cluster, and re-render (or edit
`persist.pgdata.subpath` and the postgres bind to `./data/pgdata` by hand):

```sh
angee down
mv pgdata data/pgdata
angee stack update --template
angee dev
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
