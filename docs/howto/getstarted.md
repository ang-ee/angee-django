# Get Started with Angee

This is the front door. Read it once, end to end, and you will know what Angee
is, what you can build with it, how much exists today, and exactly what to run
on a fresh machine. Everything here links out to the doc that
owns the detail — this page stays the map, not the territory.

## What is Angee?

Angee is a platform for building **agent-native applications**: products that
humans and agents operate together. It binds boring, proven libraries into one
deterministic product surface so that the hard, repetitive problems — auth,
permissions, data, deployment, gitops, UI layouts — are solved once and inherited
everywhere, instead of re-solved per project.

Angee comes in two halves. Knowing which half owns what is the whole mental
model:

### The operator

The **operator** is the self-managed stack manager — Angee's control plane. It
is a Go CLI (`angee`) and an HTTP daemon (`angee-operator`) that pulls your
source repositories and composes them into two things: **Workspaces** — a set
of Sources and/or agentic configuration, materialized as files — and
**Stacks** — the runnable unit, in a **dev** or **prod** flavor — which it
brings up on docker-compose or process-compose. One `angee.yaml` describes a
Stack in either flavor, so what you build against a Workspace's Sources promotes
to a production Stack by pointing it at those same Sources.

One operator runs against one Stack root and owns everything reachable from it:
Stacks, Services, Jobs, Sources, Workspaces, secrets, and ports — exposed over
CLI, REST, **and** GraphQL so a human, a script, or an agent drives the exact
same lifecycle. The operator is deliberately framework-agnostic: it knows
nothing about Django or React, it just runs whatever Services you declare.

> The operator lives in its own repository, `ang-ee/angee-operator`, and its full docs are
> published at **[docs.angee.ai](https://docs.angee.ai)** — start with
> [Concepts](https://docs.angee.ai/guide/concepts).

### Django / React Runtime

The **Django / React Runtime** — `ang-ee/angee-django` — is the **framework** and
the **base addons** that ship with it, and it is the first and default **Host**:
the application runtime that runs *inside* an operator-managed Service.

The framework is a thin composition layer. You write **source models**
(abstract Django models), GraphQL contributions, REBAC permissions, and React
views inside **addons**; the **composer** assembles those addon contracts into a
concrete, runnable Django + GraphQL + React application under a generated
`runtime/` tree. The framework core supplies the language and composition
machinery, while every product capability — including GraphQL — is an addon. The
first question for any change is always *which level owns it.*

Concretely, you build in **addons**, and each addon bundles two halves that ship
together: a **Django app** — models, permissions, and operations — and the
**React components** that render it. You never wire them together by hand. From
your declarations the composer emits a **production-ready, typed API** — a GraphQL
schema and the matching TypeScript contract — so the frontend talks to the
backend through a generated, type-safe client that is always in sync. Write a
field or an operation in Python, and the React side sees it, fully typed, with no
hand-written API layer in between. You write Django and React; the seam between
them is generated, not maintained.

And every app the runtime composes is reachable three ways at once: a **React UI**
for people, a **typed API** for systems, and an **MCP** tool surface for agents.
Your data, your knowledge, your files — any domain you model — becomes available
to **users, systems, and agents** alike, all through the same permissions. At its
heart, that is what `angee-django` does: it **connects your domain data to agents,
safely**, and serves it to whoever — or whatever — is allowed to ask.

> The vocabulary (addon, composer, host, project, source model, seams…) is
> defined once in the [Glossary](../glossary.md). The root rules and how the
> framework composes live in [`AGENTS.md`](../../AGENTS.md).

So: **the operator runs things; the Django / React Runtime decides what those things are.**

## What can you build with Angee?

Anything you would build with Django and React — but with a large share of the
problems pre-solved out of the box. For example:

- A **personal assistant**.
- A **company CRM** or internal "company OS".
- A **marketing website and blog**.
- A **customer-service** desk.
- Effectively any **SaaS-like product**.

You bring the product logic; Angee brings the composed foundation under it.

## What's included?

A quick tour of what Angee composes for every project on top of plain Django +
React — see **[Features](../features.md)** for the complete list and how each one
works:

- **Workspaces and services** — isolated environments composed from Sources
  and/or agentic configuration, plus the long-running workloads (Services)
  behind them, managed by the operator.
- **Agent runtimes** — persistent agents that run as Services and drive the
  product through the same control plane.
- **Storage management** — files and blobs with upload, MIME detection, and
  presigned flows.
- **Knowledge management** — structured and searchable product knowledge.
- **Workflows** — orchestration of multi-step, long-running work.
- **Integrations** — connectors to outside systems and OAuth providers.
- **Communications** — messaging and notification channels.
- **Personal relationship management (PRM)** — the current base for parties,
  relationships, communication, shared spaces, and stay-in-touch intent. CRM
  composes over that base; partner relationship management is a possible future
  addon, not a capability built today.
- **UX with different layouts** — public, app, and operator UI layouts built from
  one component system and themeable by tokens.

## How much of this is built today?

Every capability in the [feature list](../features.md) is already **prototyped
and working end to end** — proven inside production platforms the team has built.
Angee is the exercise of *lifting* those capabilities out of those codebases and
open-sourcing them here, reconstructed to the framework's conventions one addon
at a time. Expect a lot of movement over the coming weeks as new addons land.

Concretely, today:

- **Already landed here.** The operator (Stacks, Services, Jobs, Sources,
  Workspaces, secrets, ports, and gitops topology over CLI, REST, and GraphQL),
  the framework core (including composition from source models to `runtime/`),
  and the base addons: GraphQL via strawberry-django, relationship-based
  authorization (REBAC), aggregates, tiered resources, history/revisions, and
  the React frontend (layouts, list/board/form views).
- **Being lifted in now.** The higher-level addons — agents, integrations,
  knowledge, storage, and communications. They already run in the team's other
  platforms; the work in flight is reconstructing and open-sourcing them here,
  addon by addon.

This is the whole point of the framework — and why it is **technical investment,
not technical debt**. Every component and its permissions are tested end to end,
so the foundation each new addon builds on is already proven, and each addon that
lands makes the next one easier instead of adding to a pile of things to fix
later.

For exactly which libraries are wired versus still proposed, the
[opinionated stack](../stack.md) is the source of truth; for the full breakdown
of every capability see **[Features](../features.md)**.

## When will it be ready for production?

The target is **Q3 2026**. That is a target, not a promise — and
production-readiness arrives capability-by-capability rather than as one flip of
a switch. The operator and the framework core harden first; the higher-level
addons follow as they land. Until then, Angee is an **early alpha preview**:
excellent for prototyping and for shaping the framework, not yet a platform to
run a business-critical product on unattended.

## How do I get it?

Angee is open source under the **LGPL-3.0** license. The operator installs as a
binary; its default template knows the framework source repositories, so you do
not clone them by hand. `angee dev` materializes the sources declared by the
rendered stack.

## Set it up

The default `dev` Stack template renders the project host, then `angee dev`
materializes its framework sources and boots the complete stack.

1. **Install the `angee` CLI.**

   ```sh
   brew install ang-ee/tap/angee
   ```

   Or use the release installer:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/ang-ee/angee-operator/main/scripts/install.sh | sh
   ```

   You also need **Docker** (for container Services), **process-compose** (for
   local Services), and **git** (for git Sources). See the operator's
   [Getting started](https://docs.angee.ai/cli/getting-started) for details.

2. **Render and bring up a stack.**

   Before `angee init`, check for an existing current or ancestor
   `angee.yaml`. If one exists, it already owns this checkout: use that
   `ANGEE_ROOT` — never initialize a stack under a source checkout.
   On macOS, do not choose a path under `/tmp`: its symlinked path currently
   trips the operator's persistence check.

   ```sh
   angee init myproject   # dev is the default template; add -y for non-interactive use
   cd myproject
   angee dev              # materialize sources and boot everything
   ```

   There is no `--dev` flag: `dev` is the default value of `-t/--template`.
   `angee dev` is the supported bring-up command for the whole local stack;
   `angee up` starts container Services only. Don't start Django, Vite, Daphne,
   or workers by hand. The rendered manifest declares the consolidated framework
   source plus optional external sources; `angee dev` materializes them, cuts the
   `src` workspace, and runs the composed host at the stack root against those
   worktrees.

### Containerized mode and remote hosts

`runtime_mode` is a template input (a recorded answer, so one stack can switch
later): the default `process` runs Django, Celery, Vite, and Storybook as local
processes, while `docker` runs every application service in containers — the
host then needs only the `angee` binary, Docker, and git (no uv, node, or
pnpm). Docker mode also runs a headless Playwright browser server and a
Playwright MCP service behind the stack's edge, both reachable only with an
operator-minted route token.

```sh
angee init myproject -t dev --input runtime_mode=docker
```

On a remote host, add a public DNS name and the edge serves the whole UX over
automatic Let's Encrypt TLS (ports 443/80; the Vite UX at the domain root,
agent chat at `wss://<domain>/<service>/`, Playwright MCP at
`https://<domain>/playwright-mcp/mcp`):

```sh
angee init myproject -t dev --input runtime_mode=docker --input ingress_domain=dev.example.com
```

The stack root must live on the machine whose Docker daemon runs it (bind
mounts resolve on that filesystem) — ssh in and run `angee` there rather than
pointing a remote `DOCKER_HOST` at it. With the default `localhost` domain the
edge stays plain HTTP on `edge_port` and the UX is published directly.

### Upgrading a stack rendered before the monorepo

Stacks rendered while the framework lived in split repositories declare
`angee-django`, `angee-react`, `angee-base`, and `angee-templates` (and
optionally `angee-examples`) as separate sources. `angee stack update` cannot
retire those keys — migrate the manifest surgically instead:

```sh
# dry-run first; --apply backs up angee.yaml and rewrites sources + answers
uv run python scripts/migrate-stack-to-monorepo.py "$ANGEE_ROOT" --apply
angee --root "$ANGEE_ROOT" stack update --template --overwrite
# re-cut each workspaces/src workspace once its slots are clean and pushed:
angee --root "$ANGEE_ROOT" ws destroy src && angee --root "$ANGEE_ROOT" dev
```

The script replaces exactly the donor sources with the single `angee` source
and preserves every custom source; the workspace destroy guard refuses to drop
unpushed work, so land anything in flight first.

### Optional Ollama inference

The dev stack can run one shared, operator-managed Ollama container for local
inference. It is disabled by default because the image and model store are large.
Enable it when rendering the stack:

```sh
angee init myproject --input enable_ollama=true --input ollama_port=11434
cd myproject
angee dev
# In another shell after the stack is up:
docker compose -f docker-compose.yaml exec ollama ollama pull llama3.2
```

The model pull is deliberately manual; downloaded models persist in the stack's
`./ollama` store across restarts. The `ollama` inference backend defaults to
`http://localhost:11434/v1`, which reaches the published port from the local
Django and Celery processes. If `ollama_port` is changed or leased to a workspace,
set the Ollama inference provider row's `base_url` to
`http://localhost:<ollama_port>/v1`.

To run one-shot management commands against the example (emit runtime sources,
migrate, sync permissions, load data, check the GraphQL SDL), drive its
`manage.py` through `uv` from the root — the full sequence is in
[`AGENTS.md`](../../AGENTS.md) under "Run From The Root". To work on a change in
isolation, create a src-style workspace — the consolidated framework source and
optional external sources are pinned to `workspace/<name>`:

```sh
# Resolve angee_root with .agents/skills/angee-workspace/SKILL.md.
angee --root "$angee_root" ws create my-feature --template src --input base_ref=main
cd "$angee_root/workspaces/my-feature"
```

## What's needed for agents to self-build?

Angee is built so an agent can drive the entire loop, because every operation is
exposed on the same CLI + REST + GraphQL surface a human uses. Two layers
cooperate:

- **The operator gives agents a control plane.** An agent declares Sources,
  renders an isolated **Workspace** (`angee workspace create … --template
  dev-pr`), brings up that workspace's inner Stack, stays current with `main`
  via `workspaceSyncBase`, pushes its branches, and promotes to production by
  syncing the production Stack — all without touching anyone else's environment.
  This loop is described in full under
  [What "Self-Building" Looks Like](https://docs.angee.ai/guide/concepts#what-self-building-looks-like).
- **The framework gives agents a build step.** Inside the Host, an agent changes
  source models and addons, then runs `manage.py angee build` to compose them
  into the deterministic `runtime/` tree, followed by `makemigrations` /
  `migrate` / `rebac sync` / `resources load` / `schema --check`. Bringing the
  whole runtime up from a fresh checkout is the single `manage.py angee provision`
  command, which owns that build→migrate→sync→load→schema chain end to end (the
  dev and local stacks invoke it instead of restating the steps). Because
  each capability is an addon and each fact has one owner, an agent's job is to
  find the owning level and change it there — never to re-derive or monkey-patch.

In short, agents self-build because the operator makes the *lifecycle*
scriptable and the framework makes the *application* a deterministic build from
source — the same contracts, whether a human or an agent is at the controls. The
shared slash commands and sub-agents that support this work live in
[`.agents/`](../../.agents/README.md).

## Where to next

This page is part of `docs/howto/` — the human-readable guide. It points at the
docs that own each detail:

- **[Glossary](../glossary.md)** — the shared vocabulary (addon, composer, host,
  source model, seams…).
- **[Opinionated stack](../stack.md)** — which library owns which concern, and
  what is locked versus proposed.
- **[Development guidelines](../guidelines.md)** — the process and coding
  principles for all work here.
- **[Backend guidelines](../backend/guidelines.md)** and
  **[Frontend guidelines](../frontend/guidelines.md)** — the language-specific
  rules.
- **[Composer](../composer.md)** — how addon contracts become a runnable
  project.
- **[`AGENTS.md`](../../AGENTS.md)** — root rules and how the framework composes.
- **[docs.angee.ai](https://docs.angee.ai)** — the operator: concepts, the
  `angee.yaml` manifest, templates, commands, and the REST + GraphQL API.

As the docs grow, deeper how-to guides will join this folder and generated API
references (extracted from the code's own docstrings) will live alongside them —
the code stays the spec; these guides carry the intent.
