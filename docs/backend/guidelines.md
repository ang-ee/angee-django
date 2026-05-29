# Backend Guidelines

Backend code is Python, Django, and the composer. It owns data, permissions,
transport-neutral business behavior, and generated contracts.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
backend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for backend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. Python dependency setup belongs in `pyproject.toml` and
`uv.lock`.

## Rules

- Domain behavior lives on models, managers, and querysets.
- Source models are abstract. Concrete apps are emitted by the composer.
- `Meta` is the declarative backend contract. Unknown keys should fail early.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural. Reads scope through the model manager; writes check the
  instance. No bypass without an audited `sudo` or system context.
- GraphQL is auto-generated from models. Handwritten `graphql/` code is
  overrides-only for real virtual operations or non-model types.
- REST must not mirror GraphQL. If a browser or agent can use GraphQL, it should.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## Checks

Run the narrowest relevant check while editing, then the broad check before
handoff:

```sh
uv run ruff check .
uv run mypy src/
uv run pytest
angee build --check
```

If a command is not wired yet, say so plainly.
