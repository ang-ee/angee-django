# hatch-angee

This published PEP 517 backend supplies layout defaults over native Hatchling for
one in-place addon distribution. It never reads `tool.angee`, computes requirements,
or writes project metadata. One `angee.addons` entry-point key owns layout; native
Hatchling configuration overrides defaults. Hatchling is the only dependency.

Do not add runtime APIs or restore manifest parsing, discovery, metadata hooks
or compilers.

Use `uv run --package hatch-angee --extra dev pytest --noconftest -c tools/hatch-angee/pyproject.toml tools/hatch-angee/tests`, `uv run --package hatch-angee --extra dev ruff check .`, and `uv build --package hatch-angee`.
Changes to the backend must cover wheel, editable, sdist and metadata entrypoints.
Keep builds deterministic and preserve authored PEP 621 metadata.
