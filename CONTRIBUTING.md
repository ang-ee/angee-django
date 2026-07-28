# Contributing to Angee

Thanks for your interest in Angee. This repository is the framework core and the
base addons shipped with it (see `README.md`).

## Before you start

- Read **`AGENTS.md`** — it is the contributor entry point and states the
  architecture constitution every change must satisfy.
- The development process and coding principles live in `docs/guidelines.md`;
  backend and frontend specifics in `docs/backend/guidelines.md` and
  `docs/frontend/guidelines.md`. The opinionated dependency stack is
  `docs/stack.md`, and terms are defined in `docs/glossary.md`.

## Running the stack

`angee dev` is the only supported way to bring the local stack up — run it from
the repository root. See `docs/howto/getstarted.md`.

## Pull requests

- Put each change at the level that owns the concern (framework / base addon /
  consumer addon), per `AGENTS.md`.
- Regenerate any generated output from source; never hand-edit generated
  `runtime/` trees.
- Run the relevant backend / frontend / schema / e2e checks described in the
  guidelines, and state in the PR what you ran.
- Sign the [Contributor License Agreement](CLA.md) — an automated check asks you
  to on your first pull request. You keep the copyright in your contribution; the
  agreement grants Angee the right to relicense it, which is what lets Angee ship
  both an LGPL release and commercially licensed builds. Contributing on behalf of
  an employer? See [CLA-CORPORATE.md](CLA-CORPORATE.md). One signature covers
  every repository in the `ang-ee` organization.

## Security

Do not open a public issue for security problems — see `SECURITY.md`.
