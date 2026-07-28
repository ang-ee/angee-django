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
- By submitting a pull request you agree that your contribution is licensed under
  the project's LGPL-3.0 license (see `LICENSE`) and you certify you have the
  right to submit it under the [Developer Certificate of
  Origin](https://developercertificate.org/).

## Security

Do not open a public issue for security problems — see `SECURITY.md`.
