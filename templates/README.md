# Templates

Copier templates the Angee operator renders. Each template's `copier.yml` carries
an `_angee` block — `schema`, `kind`, `name`, `description` — and that description
is the template's contract. This file is the map to the kinds; it is not a second
copy of each one.

## Kinds

| dir | kind | renders |
|-----|------|---------|
| `projects/` | `project` | a downstream host repo — `manage.py`, `settings.yaml`, the consumer-addon namespace, the web package; owns the project root |
| `stacks/` | `stack` | a runnable `angee.yaml` the operator brings up on docker-compose / process-compose |
| `workspaces/` | `workspace` | a set of sources + agentic config, materialized as files |
| `services/` | `service` | one long-running service added to a stack |

For what a template scaffolds and its inputs, read its `copier.yml` `_angee.description` —
that is the owner.

## Stacks: dev overlay vs local instance

One `angee.yaml` describes a stack in either of the two layouts covered by
[Concepts](https://docs.angee.ai/guide/concepts) and the [glossary](../docs/glossary.md);
the difference is where the stack root sits and where its sources come from.

- **`stacks/dev`** — a gitignored `.angee/` overlay (`ANGEE_ROOT=.angee`) on a
  framework checkout, with `local` sources pointing at that checkout. Added with
  `angee init --dev`. For developing the framework (or a consumer) against live
  source; it runs the checkout's example.
- **`stacks/local`** — a self-contained instance where the root folder *is* the
  stack **and** a git-controlled project (`ANGEE_ROOT=.`). You own the root; the
  framework is a dependency baked into the runtime image, not a cloned source.
  This is how you run your own Angee app locally on a real (Postgres + pgvector)
  database.

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
> chains `projects/web`, runs the framework from the `ghcr.io/ang-ee/django-angee`
> runtime image on `pgvector/pgvector:pg17`, bootstraps a generated `admin` user,
> and serves the frontend through Caddy by default (`frontend_mode=caddy_static`),
> with legacy Vite dev serving still available via `frontend_mode=vite`. The
> one-command render uses the operator's template-chain resolver
> (`ang-ee/angee-operator#39`); once that lands in a released operator, `angee stack
> init` renders host + overlay in one step. The web image rebuild depends on the
> published `hatch-angee` release that ships addon `web/schema/` directories.
> Until those releases are available, use the two-copier-step render below.

## Run a local stack

Once the operator's chain resolver ships, one command renders host + stack:

```sh
angee stack init https://github.com/ang-ee/angee-django/tree/main/templates/stacks/local ~/.angee
angee up --root ~/.angee
export ANGEE_OPERATOR_URL=http://127.0.0.1:9000   # the CLI then drives the operator
```

Until then, render the two templates in sequence (the stack overlays the host),
then bring it up. First start emits the local runtime, migrations, and GraphQL
schemas before starting Django and the selected frontend ingress:

```sh
copier copy .../templates/projects/web ~/.angee
copier copy --overwrite .../templates/stacks/local ~/.angee
angee up --root ~/.angee
```
