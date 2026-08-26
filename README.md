# angee-django

**Developer framework and base addons for building Django + React applications
on the [Angee platform](https://angee.ai).**

[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://github.com/ang-ee/angee-django/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-docs.angee.ai-1f6feb.svg)](https://docs.angee.ai)
![Python](https://img.shields.io/badge/python-3.14%2B-3776AB.svg)
![Django](https://img.shields.io/badge/django-6.0%2B-092E20.svg)
![React](https://img.shields.io/badge/react-19-61DAFB.svg)
![Status](https://img.shields.io/badge/status-early%20alpha%20preview-orange.svg)

![Angee agent-native app collage](https://raw.githubusercontent.com/ang-ee/angee-django/main/docs/screenshots/angee-readme-collage.png)

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

> **New here?** Start with **[Get Started](https://docs.angee.ai/guide/getstarted)**.

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
- The **`angee` CLI** — `brew install ang-ee/tap/angee`, or use the release
  installer below
- **Docker** (container Services), **process-compose** (local Services), and
  **git** (git Sources)

## Quick start

Before `angee init`, check for an existing current or ancestor
`angee.yaml`. If one exists, it already owns this checkout: use that
`ANGEE_ROOT` — never initialize a stack under a source checkout.

Angee runs as a **stack**: `angee init` renders a project host whose manifest
declares the framework repos as sources, and `angee dev` materializes them and
boots. This repository is one of those sources — you normally work on it inside
a framework-dev stack's `src` workspace, not as a standalone checkout.

```sh
brew install ang-ee/tap/angee      # or: curl -fsSL https://raw.githubusercontent.com/ang-ee/angee-operator/main/scripts/install.sh | sh
angee init myproject               # dev is the default template; add -y for non-interactive use
cd myproject
angee dev                          # materialize sources and boot everything
```

On macOS, create the stack in a normal working directory, not under `/tmp`:
that symlinked path currently fails the operator's persistence check. `angee
dev` is the only supported way to bring the whole local stack up; `angee up`
starts container services only. Never start Django, Vite, Daphne, or workers by
hand. For the full onboarding path
(one-shot management commands and isolated workspaces), see
**[Get Started → Set it up](https://docs.angee.ai/guide/getstarted#set-it-up)**.

## Repository layout

Angee's platform spans sibling repositories; this one holds the framework core —
the one real Python package, `django-angee`:

- **`angee/`** — the model and data contracts, ASGI and Celery seams, and the
  `manage.py angee build` composer.
- **`docs/`**, **`tests/`** — the intent docs and the framework test suite.

The React packages live in `ang-ee/angee-react`, the base addons (including the
GraphQL runtime) in `ang-ee/angee-base`, the messaging bridges in
`ang-ee/angee-messaging-bridges`, and the showcase consumer addons + reference
e2e suite in `ang-ee/angee-examples`. A stack's `src` workspace materializes
them all side by side as worktree slots.

The full annotated layout lives in
**[`AGENTS.md`](https://github.com/ang-ee/angee-django/blob/main/AGENTS.md)**.

## Documentation

This repo follows a simple rule: **the code is the spec; the docs carry the
intent.**

- **[Get Started](https://docs.angee.ai/guide/getstarted)** — what Angee is, what you can
  build, and your first run. Start here.
- **[Features](https://docs.angee.ai/guide/features)** — the complete capability list and how each
  part works.
- **[Glossary](https://docs.angee.ai/guide/glossary)** — shared vocabulary (addon, composer, host,
  source model, seams…).
- **[Opinionated stack](https://docs.angee.ai/guide/stack)** — which library owns which concern, and
  what is locked versus proposed.
- **[Development guidelines](https://docs.angee.ai/guide/guidelines)** ·
  **[Backend](https://docs.angee.ai/django/guidelines)** ·
  **[Frontend](https://docs.angee.ai/react/guidelines)** — process and language rules.
- **[Composer](https://docs.angee.ai/django/composer)** — how addon contracts become a runnable
  project.
- **[`AGENTS.md`](https://github.com/ang-ee/angee-django/blob/main/AGENTS.md)** — the constitution, repository layout, and how the
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
  [Security Policy](https://github.com/ang-ee/angee-django/blob/main/SECURITY.md)
  (`security@angee.ai`). Do not open a public
  issue for vulnerabilities.

## Contributing

Contributions are welcome — this is a public alpha and feedback is especially
valuable. Start with
**[`CONTRIBUTING.md`](https://github.com/ang-ee/angee-django/blob/main/CONTRIBUTING.md)**,
which points at the constitution in
**[`AGENTS.md`](https://github.com/ang-ee/angee-django/blob/main/AGENTS.md)** and
the process in
**[development guidelines](https://docs.angee.ai/guide/guidelines)**.

- Bring the stack up with `angee dev` from the repository root — never start the
  individual processes by hand.
- Run the backend checks (ruff, mypy, pytest) and the frontend checks from the
  [backend](https://docs.angee.ai/django/guidelines) and
  [frontend](https://docs.angee.ai/react/guidelines)
  guidelines before opening a pull request.
- By participating you agree to uphold our
  **[Code of Conduct](https://github.com/ang-ee/angee-django/blob/main/CODE_OF_CONDUCT.md)**.

Notable changes are recorded in
**[`CHANGELOG.md`](https://github.com/ang-ee/angee-django/blob/main/CHANGELOG.md)**.

## Roadmap & status

**Early alpha preview.** Production-readiness is targeted for **Q3 2026** —
a target, not a promise — and arrives capability-by-capability. See
[Get Started](https://docs.angee.ai/guide/getstarted#how-much-of-this-is-built-today)
for the
current built-versus-ahead breakdown.

## License

Copyright © 2026 Angee, Inc. Licensed under the **GNU Lesser General Public
License v3.0 or later** (LGPL-3.0-or-later). The LGPL is drafted as additional
permissions on top of the GPL, so both texts ship:
**[LICENSE](https://github.com/ang-ee/angee-django/blob/main/LICENSE)** (the
Lesser additional permissions) and
**[LICENSE.GPL](https://github.com/ang-ee/angee-django/blob/main/LICENSE.GPL)**
(the GNU General Public License v3.0 they extend).

Addons and applications built on Angee are yours to license as you choose,
including proprietary — the LGPL's terms apply to Angee itself.
