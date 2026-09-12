# Contributing to Angee

Thanks for your interest in Angee. This repository contains the framework core,
standard addons, React packages, examples, and templates shipped together (see
`README.md`).

## Before you start

- Read **`AGENTS.md`** — it is the contributor entry point and states the
  architecture constitution every change must satisfy.
- The development process and coding principles live in `docs/guidelines.md`;
  backend and frontend specifics in `docs/backend/guidelines.md` and
  `docs/frontend/guidelines.md`. The opinionated dependency stack is
  `docs/stack.md`, and terms are defined in `docs/glossary.md`.

## Running the stack

`angee dev` is the supported way to bring the complete local stack up. Resolve
the controlling stack root containing `angee.yaml` and run
`angee --root "$angee_root" dev`; the framework source slot is not that root.
See [Get Started](docs/howto/getstarted.md) and [Checks](docs/checks.md) for
command context and prerequisites.

## Pull requests

- Put each change at the level that owns the concern (framework / base addon /
  consumer addon), per `AGENTS.md`.
- Regenerate any generated output from source; never hand-edit generated
  `runtime/` trees.
- Run the relevant checks in [Checks](docs/checks.md), and state in the PR what
  you ran and what could not run.
- Sign the [Contributor License Agreement](CLA.md) — an automated check asks you
  to on your first pull request. You keep the copyright in your contribution; the
  agreement grants Angee the right to relicense it, which is what lets Angee ship
  both an LGPL release and commercially licensed builds. Contributing on behalf of
  an employer? See [CLA-CORPORATE.md](CLA-CORPORATE.md). One signature covers
  every repository in the `ang-ee` organization.

## Security

Do not open a public issue for security problems — see `SECURITY.md`.
