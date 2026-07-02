# angee-django

**Developer framework and base addons for building Django + React applications
on the [Angee platform](https://angee.ai).**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-docs.angee.ai-1f6feb.svg)](https://docs.angee.ai)
![Python](https://img.shields.io/badge/python-3.14%2B-3776AB.svg)
![Django](https://img.shields.io/badge/django-6.0%2B-092E20.svg)
![React](https://img.shields.io/badge/react-19-61DAFB.svg)
![Status](https://img.shields.io/badge/status-early%20alpha%20preview-orange.svg)

![Angee agent-native app collage](docs/screenshots/angee-readme-collage.png)

> **For developers, not end users.** This repository is the framework source for
> teams building Angee applications and addons. If you want a product to use,
> start with a derivative distribution built on Angee:
> [ARP](https://github.com/ang-ee/arp-angee) (open-source agentic ERP / aERP),
> [fyltr.ai](https://fyltr.ai/) (personal AI), [SmartOPS Aero](https://smartops.aero/)
> (aviation operations), or another product-specific Angee distribution.

> **Public alpha / active refactor.** Angee is being opened while major addon,
> API, and UI surfaces are still moving. Use it for exploration and feedback,
> not production. Roadmap and compatibility guarantees are still in progress.

## What is this?

`angee-django` is the **framework**, the **base addons** that ship with it, and
the first and default **Host** for Angee — a platform for building
**agent-native applications** that humans and agents operate together.

Angee comes in two halves:

- **The operator** — a Go control plane (repo
  [`ang-ee/angee-operator`](https://github.com/ang-ee/angee-operator)) that pulls source
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
git clone https://github.com/ang-ee/angee-django.git
cd angee-django
curl -fsSL https://angee.ai/install.sh | sh   # the angee CLI, if not already installed
angee init --dev                              # set up the angee dev stack from the template
angee dev                                     # run the examples/notes-angee stack from the repo root
```

`angee dev` is the only supported way to bring the local stack up — run it from
the repository root, and never start Django, Vite, Daphne, or workers by hand.
For the full onboarding path (one-shot management commands and isolated
workspaces), see **[Get Started → Set it up](docs/howto/getstarted.md#set-it-up)**.

## Repository layout

Angee ships its Python and its JavaScript from a **single distribution**: the
shared React libraries live under the framework core, so they travel inside the
`django-angee` Python wheel — one release channel for both languages.

- **`angee/`** — the backend framework core and composer, plus the shared React
  libraries:
  - Python (`angee/…`) — composition, GraphQL, REBAC, resources, and the
    `manage.py angee build` composer.
  - `angee/web/*` — the shared React libraries: `@angee/ui`, `@angee/app`,
    `@angee/refine`, `@angee/metadata`.
- **`addons/angee/*`** — the base addons, each a vertical slice: Python
  (source models · GraphQL · REBAC) plus its React surface under
  `addons/angee/<name>/web` (`@angee/iam`, `@angee/agents`, `@angee/platform`,
  `@angee/storage`, …). Core and addons share the one `angee.*` Python namespace.
- **`packages/*`** — dev-only tooling (`@angee/storybook`, `@angee/e2e`); not
  shipped in the wheel.
- **`examples/notes-angee/`** — the example project the root stack runs.
- **`templates/`** — the Stack and Workspace Copier templates Angee renders.

The full annotated layout lives in **[`AGENTS.md`](AGENTS.md)**.

## Documentation

This repo follows a simple rule: **the code is the spec; the docs carry the
intent.**

- **[Get Started](docs/howto/getstarted.md)** — what Angee is, what you can
  build, and your first run. Start here.
- **[Features](docs/features.md)** — the complete capability list and how each
  part works.
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
- **[docs.angee.ai](https://docs.angee.ai)** — the full site: the operator's
  concepts, the `angee.yaml` manifest, templates, commands, and the REST +
  GraphQL API.

**Generated API references** — extracted from the code's own docstrings and
TSDoc on every docs build — are published on the site: the
**[backend / Python reference](https://docs.angee.ai/django/reference)** and the
**[frontend / React reference](https://docs.angee.ai/react/reference)**.

## Community & support

- **Docs & concepts** — [docs.angee.ai](https://docs.angee.ai).
- **Bugs & feature requests** — open an
  [issue](https://github.com/ang-ee/angee-django/issues).
- **Security reports** — please report **privately**; see the
  [Security Policy](SECURITY.md) (`security@angee.ai`). Do not open a public
  issue for vulnerabilities.

## Contributing

Contributions are welcome — this is a public alpha and feedback is especially
valuable. Start with **[`CONTRIBUTING.md`](CONTRIBUTING.md)**, which points at the
constitution in **[`AGENTS.md`](AGENTS.md)** and the process in
**[`docs/guidelines.md`](docs/guidelines.md)**.

- Bring the stack up with `angee dev` from the repository root — never start the
  individual processes by hand.
- Run the backend checks (ruff, mypy, pytest) and the frontend checks from the
  [backend](docs/backend/guidelines.md) and [frontend](docs/frontend/guidelines.md)
  guidelines before opening a pull request.
- By participating you agree to uphold our
  **[Code of Conduct](CODE_OF_CONDUCT.md)**.

Notable changes are recorded in **[`CHANGELOG.md`](CHANGELOG.md)**.

## Roadmap & status

**Early alpha preview.** Production-readiness is targeted for **Q3 2026** —
a target, not a promise — and arrives capability-by-capability. See
[Get Started](docs/howto/getstarted.md#how-much-of-this-is-built-today) for the
current built-versus-ahead breakdown.

## License

Copyright © 2026 Angee, Inc. Licensed under the **GNU Affero General Public
License v3.0 or later** (AGPL-3.0-or-later). See **[LICENSE](LICENSE)**.
