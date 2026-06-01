# angee-django

**The Django + React framework and base addons for the [Angee platform](https://angee.ai).**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.14%2B-3776AB.svg)
![Django](https://img.shields.io/badge/django-6.0%2B-092E20.svg)
![React](https://img.shields.io/badge/react-19-61DAFB.svg)
![Status](https://img.shields.io/badge/status-early%20access-orange.svg)

## What is this?

`angee-django` is the **framework**, the **base addons** that ship with it, and
the first and default **Host** for Angee — a platform for building
**agent-native applications** that humans and agents operate together.

Angee comes in two halves:

- **The operator** — a Go control plane (repo
  [`fyltr/angee`](https://github.com/fyltr/angee)) that pulls source
  repositories and composes them into **Workspaces** (Sources and/or agentic
  configuration) and **Stacks** (dev or prod), running them on docker-compose or
  process-compose.
- **This repository** — the thin composition framework that assembles a working
  Django + GraphQL + React application from **addons**.

In one line: *the operator runs things; `angee-django` decides what those things
are.*

> **New here?** Start with **[Get Started](docs/howto/getstarted.md)**.

## Architecture at a glance

```text
   ┌──────────────────────────────────────────────────────────────┐
   │  angee operator   (Go control plane — CLI · REST · GraphQL)  │
   │  pulls git Sources → composes Workspaces (a set of Sources   │
   │  and/or agentic config) and Stacks (dev or prod), runs them  │
   └─────────────────────────────┬────────────────────────────────┘
                                 │ runs as a Service
   ┌──────────────────────────────────────────────────────────────┐
   │  angee-django   ·   THIS REPO   ·   the default Host         │
   │                                                              │
   │    addons  (source models · GraphQL · REBAC · React views)   │
   │        │                                                     │
   │        │  manage.py angee build                              │
   │        ▼                                                     │
   │    runtime/   →   Django + GraphQL + React application       │
   └──────────────────────────────────────────────────────────────┘
```

## Requirements

- **Python ≥ 3.14** and [uv](https://docs.astral.sh/uv/)
- **Node ≥ 22.13** and [pnpm](https://pnpm.io/)
- The **`angee` CLI** — `curl -fsSL https://angee.ai/install.sh | sh`
- **Docker** (container Services), **process-compose** (local Services), and
  **git** (git Sources)

## Quick start

```sh
git clone https://github.com/fyltr/angee-django.git
cd angee-django
curl -fsSL https://angee.ai/install.sh | sh   # the angee CLI, if not already installed
angee init --dev                              # set up the angee dev stack from the template
angee dev                                     # run the examples/notes-angee stack from the repo root
```

`angee dev` is the only supported way to bring the local stack up — run it from
the repository root, and never start Django, Vite, Daphne, or workers by hand.
For the full onboarding path (one-shot management commands and isolated
workspaces), see **[Get Started](docs/howto/getstarted.md#i-have-access--now-what)**.

## What's inside

- **`django-angee`** (`src/angee/`) — the backend framework core and base addons
  (composition, GraphQL, REBAC, resources).
- **`@angee/sdk`** (`packages/sdk/`) — the headless frontend bindings.
- **`@angee/base`** (`packages/base/`) — the single rendered (styled) binding.
- **`examples/notes-angee/`** — the example project the root stack runs.
- **`templates/`** — the Stack and Workspace Copier templates Angee renders.

The full repository layout lives in **[`AGENTS.md`](AGENTS.md)**.

## Documentation

This repo follows a simple rule: **the code is the spec; the docs carry the
intent.**

- **[Get Started](docs/howto/getstarted.md)** — what Angee is, what you can
  build, and your first run. Start here.
- **[Glossary](docs/glossary.md)** — shared vocabulary (addon, composer, host,
  source model, seams…).
- **[Opinionated stack](docs/stack.md)** — which library owns which concern, and
  what is locked versus proposed.
- **[Development guidelines](docs/guidelines.md)** ·
  **[Backend](docs/backend/guidelines.md)** ·
  **[Frontend](docs/frontend/guidelines.md)** — process and language rules.
- **[Composer](docs/composer.md)** — how addon contracts become a runnable
  project.
- **[`AGENTS.md`](AGENTS.md)** — the constitution, repository layout, and how the
  framework composes.
- **[docs.angee.ai](https://docs.angee.ai)** — the operator: concepts, the
  `angee.yaml` manifest, templates, commands, and the REST + GraphQL API.

Generated API references — extracted from the code's own docstrings — will land
under `docs/generated/` as the framework matures.

## Development & contributing

All work follows the process in **[`docs/guidelines.md`](docs/guidelines.md)**
and the constitution in **[`AGENTS.md`](AGENTS.md)**. Bring the stack up with
`angee dev` from the repository root — never start the individual processes by
hand. Run the backend checks (ruff, mypy, pytest) and the frontend checks listed
in the [backend](docs/backend/guidelines.md) and
[frontend](docs/frontend/guidelines.md) guidelines before opening a pull
request.

## Status

**Early access for builders.** Production-readiness is targeted for **Q3 2026** —
a target, not a promise — and arrives capability-by-capability. See
[Get Started](docs/howto/getstarted.md#how-much-of-this-is-built-today) for the
current built-versus-ahead breakdown.

## License

Copyright © 2026 Angee, Inc. Licensed under the **GNU Affero General Public
License v3.0 or later** (AGPL-3.0-or-later). See **[LICENSE](LICENSE)**.
