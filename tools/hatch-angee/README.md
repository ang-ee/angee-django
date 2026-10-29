# hatch-angee

A small PEP 517 backend for one in-place Angee addon per distribution. Hatchling
owns metadata and archives; this backend derives layout defaults from the single
`angee.addons` entry point. All PEP 621 fields remain authored static metadata.

```toml
build-system = { requires = ["hatch-angee>=1,<2"], build-backend = "hatch_angee" }

[project]
name = "acme-notes"
version = "1.0.0"
entry-points."angee.addons"."acme.notes" = "acme.notes"
```

Put this file inside `addons/acme/notes/`, alongside its Python package content.
The backend remaps files into `acme/notes/`, includes package data, and points an
editable install at the namespace root. Explicit `tool.hatch.build` settings win.
Keep repository ignore rules at its root; nested ignore files can shadow them.

Build with `uv build --wheel`, or build a wheel from the self-contained sdist.
Run `uv run --package hatch-angee --extra dev pytest --noconftest -c tools/hatch-angee/pyproject.toml tools/hatch-angee/tests` for backend and distribution contracts.
