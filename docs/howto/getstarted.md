# Get Started with Angee

This is the front door. Read it once, end to end, and you will know what Angee
is, what you can build with it, how much exists today, and exactly what to run
after your GitHub invitation lands. Everything here links out to the doc that
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
`runtime/` tree. Everything — including the framework core — is an addon, so the
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
- **CRM / PRM** — customer and partner relationship data models.
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
  Workspaces, secrets, ports, and gitops topology over CLI, REST, and GraphQL)
  and the framework core: composition (source models → `runtime/`), GraphQL via
  strawberry-django, relationship-based authorization (REBAC), aggregates,
  tiered resources, history/revisions, and the React frontend (layouts,
  list/board/form views).
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

## How can I get access?

Access is by invitation to the **`ang-ee` GitHub organization**. Once you are
invited you should receive a GitHub email — accept it, and you will have the two
repositories:

- **[`ang-ee/angee-operator`](https://github.com/ang-ee/angee-operator)** — the operator and CLI.
- **[`ang-ee/angee-django`](https://github.com/ang-ee/angee-django)** — the
  framework, base addons, and default Host (the Django / React Runtime).

If you expected an invitation and don't see one, ask your Angee contact to add
your GitHub account to the org.

## I have access — now what?

Start from the Django / React Runtime; it ships the Stack template and the
example project, so it brings the whole stack up for you.

1. **Clone the framework.**

   ```sh
   git clone https://github.com/ang-ee/angee-django.git
   cd angee-django
   ```

2. **Install the `angee` CLI.** From a release:

   ```sh
   curl -fsSL https://angee.ai/install.sh | sh
   ```

   You also need **Docker** (for container Services), **process-compose** (for
   local Services), and **git** (for git Sources). See the operator's
   [Getting started](https://docs.angee.ai/guide/getting-started) for details.

3. **Set up and bring up the stack from the repo root.**

   ```sh
   angee init --dev   # set up the dev stack from the template (first time only)
   angee dev          # run the examples/notes-angee stack
   ```

   `angee dev` is the only supported way to run the local stack — don't start
   Django, Vite, Daphne, or workers by hand. Run from the repository root: the
   root stack is wired to the `examples/notes-angee` project, so `angee dev`
   runs that example against the framework.

To run one-shot management commands against the example (emit runtime sources,
migrate, sync permissions, load data, check the GraphQL SDL), drive its
`manage.py` through `uv` from the root — the full sequence is in
[`AGENTS.md`](../../AGENTS.md) under "Run From The Root". To work on a change in
isolation, create a workspace and run the stack inside it:

```sh
angee ws create my-feature --template dev --input base_ref=main
cd .angee/workspaces/my-feature
angee dev
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
  `migrate` / `rebac sync` / `resources load` / `schema --check`. Because
  everything is an addon and each fact has one owner, an agent's job is to find
  the owning level and change it there — never to re-derive or monkey-patch.

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
