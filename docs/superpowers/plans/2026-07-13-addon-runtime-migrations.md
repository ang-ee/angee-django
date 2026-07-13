# Addon Runtime Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let addons ship lossless, ordinary Django migrations that `angee build` materializes into each downstream runtime migration graph before normal `makemigrations` and `migrate` run.

**Architecture:** Add `AddonMigration` declarations to the existing addon manifest owner, then add a focused `RuntimeMigrations` materializer under `angee.compose`. The materializer plans against Django's `MigrationLoader` and historical `ProjectState`, copies complete applicable source modules with deterministic origin/dependency metadata, and is called only by explicit `Runtime.build()`/`Runtime.check()` paths. Django continues to own migration state, graph validation, execution, rollback, routing, and recording.

**Tech Stack:** Python 3.14, Django 6.0 migrations (`Migration`, `MigrationLoader`, `MigrationGraph`, `ProjectState`), TOML addon manifests, pytest/pytest-django, Ruff, Mypy.

## Global Constraints

- Work only in `/Users/alexis/.angee/workspaces/manual-migrations` on `workspace/manual-migrations`.
- Follow the approved spec at `docs/superpowers/specs/2026-07-13-addon-runtime-migrations-design.md`.
- Materialization runs only during explicit `angee build`; normal `emit_if_stale()` remains migration-write-free.
- The complete source module is copied so local `RunPython`/`RunSQL` code remains self-contained.
- Source modules live under `runtime_migrations/`, never Django's conventional `migrations/` package.
- Existing materialized migrations are append-only and immutable; origin and SHA-256 drift fail loudly.
- App and declaration order are deterministic; no timestamps, random identifiers, or filesystem iteration order enter output.
- Planned writes happen only after every declaration and simulated state transition validates.
- Django owns all database execution and migration recording; the build step performs no schema or data operation.
- Runtime cleanup continues preserving every path under a migration directory.
- Use test-driven development: observe each focused test fail before adding its production behavior.

---

## File Structure

- `angee/addons.py`: owns `AddonMigration` and manifest parsing into `AddonContract.migrations`.
- `angee/compose/migrations.py`: owns declaration validation, historical-state planning, graph attachment, integrity checks, deterministic footer rendering, and atomic materialization.
- `angee/compose/runtime.py`: owns the explicit build/check integration and delegates migration details to `RuntimeMigrations`.
- `angee/compose/management/commands/angee.py`: remains a thin CLI dispatcher and calls `Runtime.build()`.
- `addons/angee/parties/runtime_migrations/relationship_anchor.py`: addon-owned ordinary Django migration for the first lossless transition.
- `addons/angee/parties/addon.toml`: declares the parties migration origin, target, and module.
- `tests/test_addons.py`: verifies manifest parsing and declaration order.
- `tests/test_runtime_migrations.py`: focused materializer, graph, integrity, lifecycle, and parties acceptance tests.
- `tests/test_compose.py`: verifies the management command delegates to the new `Runtime.build()` owner.
- `tests/conftest.py`: keeps fake `AddonContract` construction explicit with an empty migration tuple.
- `docs/composer.md`: explains the build-time manual migration seam.
- `docs/backend/guidelines.md`: records append-only, historical-model, and self-contained migration rules.

---

### Task 1: Add the addon migration declaration contract

**Files:**
- Modify: `angee/addons.py:23-160`
- Modify: `tests/test_addons.py:10-58`
- Modify: `tests/conftest.py:754-772`

**Interfaces:**
- Produces: `AddonMigration(name: str, app_label: str, module: str)`.
- Produces: `AddonContract.migrations: tuple[AddonMigration, ...]`.
- Consumes later: `RuntimeMigrations` reads declarations through `addon_contract(app_config)`.

- [ ] **Step 1: Write failing parser and validation tests**

Add imports and tests to `tests/test_addons.py`:

```python
import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.addons import AddonContract, AddonMigration, AvailableAddon, _read_addon_contract, available_addons


def test_addon_contract_parses_ordered_runtime_migrations(tmp_path) -> None:
    marker = tmp_path / "addon.toml"
    marker.write_text(
        """\
[addon]
name = "example.demo"

[[migrations]]
name = "rename_owner"
app_label = "demo"
module = "runtime_migrations.rename_owner"

[[migrations]]
name = "backfill_owner"
app_label = "demo"
module = "example.demo.runtime_migrations.backfill_owner"
""",
        encoding="utf-8",
    )

    contract = _read_addon_contract(str(marker))

    assert contract is not None
    assert contract.migrations == (
        AddonMigration("rename_owner", "demo", "runtime_migrations.rename_owner"),
        AddonMigration("backfill_owner", "demo", "example.demo.runtime_migrations.backfill_owner"),
    )


@pytest.mark.parametrize(
    "body, message",
    [
        ("[migrations]\nname = 'bad'\n", "migrations must be an array of tables"),
        ("[[migrations]]\nname = 'bad'\n", "requires string app_label"),
        ("[[migrations]]\nname = 3\napp_label = 'demo'\nmodule = 'm.x'\n", "requires string name"),
    ],
)
def test_addon_contract_rejects_invalid_runtime_migration_entries(tmp_path, body: str, message: str) -> None:
    marker = tmp_path / "addon.toml"
    marker.write_text(f'[addon]\nname = "example.demo"\n\n{body}', encoding="utf-8")

    with pytest.raises(ImproperlyConfigured, match=message):
        _read_addon_contract(str(marker))
```

Update `make_contract()` defaults in `tests/conftest.py` with `"migrations": ()`.

- [ ] **Step 2: Run the tests and verify the contract is absent**

Run:

```bash
uv run python -m pytest tests/test_addons.py::test_addon_contract_parses_ordered_runtime_migrations tests/test_addons.py::test_addon_contract_rejects_invalid_runtime_migration_entries -v
```

Expected: collection/import failure because `AddonMigration` is not defined.

- [ ] **Step 3: Implement the immutable declaration and parser**

Add beside `AddonContract` in `angee/addons.py`:

```python
@dataclass(frozen=True, slots=True)
class AddonMigration:
    """One addon-owned Django migration materialized into a runtime app."""

    name: str
    app_label: str
    module: str


def _parse_migrations(raw: Any, *, marker: Path) -> tuple[AddonMigration, ...]:
    """Parse ordered ``[[migrations]]`` declarations from one addon manifest."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ImproperlyConfigured(f"{marker}: migrations must be an array of tables")
    migrations: list[AddonMigration] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ImproperlyConfigured(f"{marker}: migrations[{index}] must be a table")
        values: dict[str, str] = {}
        for key in ("name", "app_label", "module"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise ImproperlyConfigured(f"{marker}: migrations[{index}] requires string {key}")
            values[key] = value
        migrations.append(AddonMigration(**values))
    return tuple(migrations)
```

Add `migrations: tuple[AddonMigration, ...] = ()` to `AddonContract`, include the seam in its docstring, and pass `migrations=_parse_migrations(data.get("migrations"), marker=path)` from `_read_addon_contract()`.

- [ ] **Step 4: Run the addon tests**

Run:

```bash
uv run python -m pytest tests/test_addons.py -v
```

Expected: all addon tests pass.

- [ ] **Step 5: Commit the contract**

```bash
git add angee/addons.py tests/test_addons.py tests/conftest.py
git commit -m "feat(addons): declare runtime migrations"
```

---

### Task 2: Materialize applicable source migrations onto a runtime leaf

**Files:**
- Create: `angee/compose/migrations.py`
- Create: `tests/test_runtime_migrations.py`

**Interfaces:**
- Consumes: `AddonContract.migrations` from Task 1.
- Produces: `RuntimeMigrationPlan(origin, app_label, name, source_path, output_path, source_sha256, dependencies, latest_dependencies, migration_class)`.
- Produces: `RuntimeMigrations(addons, runtime_dir, runtime_module, labels)`.
- Produces: `RuntimeMigrations.plan() -> tuple[RuntimeMigrationPlan, ...]`.
- Produces: `RuntimeMigrations.materialize() -> tuple[Path, ...]`.

- [ ] **Step 1: Create a focused migration-graph test fixture and failing happy-path test**

In `tests/test_runtime_migrations.py`, create a helper that:

1. prepends `tmp_path` to `sys.path` with `monkeypatch.syspath_prepend()`;
2. creates a unique runtime package containing
   `resources/migrations/0001_legacy.py` with a `Legacy` model and `old_name` field;
3. points `settings.MIGRATION_MODULES["resources"]` to that package;
4. creates an `example.demo.runtime_migrations.rename_legacy` source module with a local
   `RunPython` function, `RenameField`, `applies(ProjectState)`, and a trailing newline;
5. returns a fake addon carrying `make_contract(migrations=(AddonMigration(...),))`.

The central test is:

```python
def test_materialize_copies_complete_source_and_attaches_current_leaf(runtime_migration_probe) -> None:
    materializer, source_path, runtime_dir = runtime_migration_probe

    written = materializer.materialize()

    output = runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py"
    assert written == (output,)
    text = output.read_text(encoding="utf-8")
    assert text.startswith(source_path.read_text(encoding="utf-8"))
    assert "def forwards(apps, schema_editor):" in text
    assert 'Migration.dependencies.append(("resources", "0001_legacy"))' in text
    assert 'Migration.angee_origin = "example.demo:rename_legacy"' in text

    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.graph.leaf_nodes("resources") == [("resources", "0002_rename_legacy")]
    state = loader.project_state()
    assert "new_name" in state.models["resources", "legacy"].fields
    assert "old_name" not in state.models["resources", "legacy"].fields
```

Also add failing tests proving `applies=False` writes nothing and two applicable declarations targeting the same label become `0002` then `0003` with a dependency chain.

- [ ] **Step 2: Run the focused tests and verify the materializer is absent**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -k "materialize or sequential or applies_false" -v
```

Expected: collection/import failure because `angee.compose.migrations` does not exist.

- [ ] **Step 3: Implement planning and basic materialization**

Create `angee/compose/migrations.py` with these public shapes:

```python
from __future__ import annotations

import hashlib
import importlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from django.apps import AppConfig
from django.db.migrations import Migration
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader

from angee.addons import AddonMigration, addon_contract
from angee.fs import write_atomic

MATERIALIZED_FOOTER = "# ANGEE MATERIALIZED MIGRATION - DO NOT EDIT"


@dataclass(frozen=True, slots=True)
class RuntimeMigrationPlan:
    origin: str
    app_label: str
    name: str
    source_path: Path
    output_path: Path
    source_sha256: str
    dependencies: tuple[tuple[str, str], ...]
    latest_dependencies: tuple[tuple[tuple[str, str], tuple[str, str]], ...]
    migration_class: type[Migration]


class RuntimeMigrations:
    """Plan and materialize addon-owned migrations into runtime app graphs."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path,
        runtime_module: str,
        labels: Iterable[str],
    ) -> None:
        self.addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.runtime_module = runtime_module
        self.labels = frozenset(labels)

    def materialize(self) -> tuple[Path, ...]:
        plans = self.plan()
        for plan in plans:
            write_atomic(plan.output_path, self._render(plan))
        importlib.invalidate_caches()
        MigrationLoader(None, ignore_no_migrations=True)
        return tuple(plan.output_path for plan in plans)
```

Implement `plan()` so it:

- loads `MigrationLoader(None, ignore_no_migrations=True)` and its project state;
- iterates addons in given order and each contract's migration tuple in authored order;
- resolves relative source modules under `addon.name`;
- validates `Migration` subclasses and boolean `applies(state.clone())` results;
- rejects names that are not lower-case Python identifiers and targets outside `labels`;
- calculates the next number from `MigrationAutodetector.parse_number()` over target disk nodes;
- adds the single target leaf dependency when present;
- instantiates `migration_class(name, app_label)` and advances the simulated state with `mutate_state()`;
- adds planned nodes/dependencies to the loader graph and calls `validate_consistency()` and `ensure_not_cyclic()`;
- returns every plan only after the complete loop succeeds.

Implement `_render()` by copying `source_path.read_text(encoding="utf-8")`, requiring a trailing newline, appending `MATERIALIZED_FOOTER`, appending the automatic target dependency only when absent, and recording origin plus SHA-256. Do not write from `plan()`.

- [ ] **Step 4: Run focused happy-path tests**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -k "materialize or sequential or applies_false" -v
```

Expected: selected tests pass; the materialized module is loadable by Django.

- [ ] **Step 5: Commit basic materialization**

```bash
git add angee/compose/migrations.py tests/test_runtime_migrations.py
git commit -m "feat(compose): materialize addon migrations"
```

---

### Task 3: Add graph dependency resolution and immutable-origin checks

**Files:**
- Modify: `angee/compose/migrations.py`
- Modify: `tests/test_runtime_migrations.py`

**Interfaces:**
- Extends: source `Migration.dependencies` accepts exact Django dependencies and `(app_label, "__latest__")`.
- Produces: `RuntimeMigrations.check() -> None`, raising `RuntimeError` for pending or invalid materializations.
- Produces generated metadata: `Migration.angee_origin` and `Migration.angee_source_sha256`.

- [ ] **Step 1: Write failing dependency, integrity, and fail-fast tests**

Add focused tests with these assertions:

```python
def test_latest_dependency_resolves_to_other_runtime_leaf(cross_app_probe) -> None:
    materializer, runtime_dir = cross_app_probe

    materializer.materialize()

    text = (runtime_dir / "resources" / "migrations" / "0002_cross_app.py").read_text(encoding="utf-8")
    assert '("iam", "0004_current") if dependency == ("iam", "__latest__")' in text


def test_materialization_is_idempotent(runtime_migration_probe) -> None:
    materializer, _, _ = runtime_migration_probe

    first = materializer.materialize()
    second = materializer.materialize()

    assert len(first) == 1
    assert second == ()


def test_check_reports_pending_without_writing(runtime_migration_probe) -> None:
    materializer, _, runtime_dir = runtime_migration_probe

    with pytest.raises(RuntimeError, match="pending addon runtime migration example.demo:rename_legacy"):
        materializer.check()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_changed_released_source_fails_instead_of_rewriting(runtime_migration_probe) -> None:
    materializer, source_path, _ = runtime_migration_probe
    materializer.materialize()
    source_path.write_text(source_path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="source digest changed"):
        materializer.materialize()
```

Add parameterized failure tests for duplicate origins, invalid migration names, unknown target labels, missing `Migration`, missing/non-boolean `applies`, multiple target leaves, unresolved `__latest__`, output collision, copied-body edits, non-empty `replaces`, and graph cycles. Each assertion must match a message containing the addon origin and target where available.

- [ ] **Step 2: Run the hardening tests and verify failures**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -k "latest or idempotent or pending or changed or rejects" -v
```

Expected: failures demonstrate absent origin scanning, digest validation, `__latest__` resolution, and `check()`.

- [ ] **Step 3: Implement deterministic footers and origin integrity**

Use exact metadata attributes and source hashing:

```python
ORIGIN_ATTR = "angee_origin"
SOURCE_SHA256_ATTR = "angee_source_sha256"


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

Scan `loader.disk_migrations` for instances carrying `angee_origin`. Map each origin to `(node, migration, output_path)` and fail on duplicates. Split copied files once on `MATERIALIZED_FOOTER`; hash the pre-footer source body and require it to equal the stored digest. For every currently declared origin already present, compare the current source digest to both stored values and skip it only when all values agree.

Render `__latest__` without serializing or replacing unrelated dependency objects:

```python
Migration.dependencies = [
    ("iam", "0004_current") if dependency == ("iam", "__latest__") else dependency
    for dependency in Migration.dependencies
]
```

Then append the automatic target leaf only when it is not already in `Migration.dependencies`. Sort generated replacement clauses by source dependency tuple and use `repr()` only for plain `(str, str)` nodes.

Implement `check()` as:

```python
def check(self) -> None:
    plans = self.plan()
    if plans:
        origins = ", ".join(plan.origin for plan in plans)
        raise RuntimeError(f"pending addon runtime migration {origins}")
```

Wrap source import, applicability, state mutation, and graph exceptions in `RuntimeError` messages naming the declaration origin. Reject `Migration.replaces`; validate standard `run_before` edges through Django's graph and reject cycles.

- [ ] **Step 4: Run the complete materializer test file**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -v
```

Expected: all materializer tests pass.

- [ ] **Step 5: Commit dependency and integrity behavior**

```bash
git add angee/compose/migrations.py tests/test_runtime_migrations.py
git commit -m "fix(compose): harden runtime migration history"
```

---

### Task 4: Integrate materialization with explicit Runtime build/check

**Files:**
- Modify: `angee/compose/runtime.py:93-290`
- Modify: `angee/compose/management/commands/angee.py:75-89`
- Modify: `tests/test_compose.py:1437-1475`
- Modify: `tests/test_runtime_migrations.py`

**Interfaces:**
- Produces: `Runtime.build() -> tuple[Path, ...]`.
- Produces: `Runtime.runtime_migrations() -> RuntimeMigrations`.
- Extends: `Runtime.check()` validates both generated sources and pending addon migrations.
- CLI consumes: `Command._handle_build()` calls `Runtime.build()` for a write build.

- [ ] **Step 1: Write failing Runtime and command delegation tests**

Replace the old command emit test with:

```python
def test_build_command_delegates_the_complete_write_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeRuntime:
        def build(self) -> tuple[Path, ...]:
            calls.append("build")
            return ()

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    Command()._handle_build({"check": False})

    assert calls == ["build"]
```

Add Runtime tests proving:

- `build()` emits stale sources before calling `materialize()`;
- `build()` still calls `materialize()` when sources are current;
- `check()` calls migration `check()` only after generated-source drift is clean;
- `emit_if_stale()` never constructs or invokes `RuntimeMigrations`;
- `clean()` continues preserving materialized files through the existing migration-directory rule.

- [ ] **Step 2: Run the lifecycle tests and verify `Runtime.build` is absent**

Run:

```bash
uv run python -m pytest tests/test_compose.py -k "build_command or build_check" tests/test_runtime_migrations.py -k "runtime_build or emit_if_stale or clean" -v
```

Expected: failures because `Runtime.build()` and migration-aware `check()` are not implemented.

- [ ] **Step 3: Implement the Runtime-owned lifecycle**

Add to `Runtime`:

```python
def runtime_migrations(self) -> RuntimeMigrations:
    """Return the manual migration materializer for this composed runtime."""

    return RuntimeMigrations(
        self.addons,
        runtime_dir=self.runtime_dir,
        runtime_module=self.runtime_module,
        labels=self.labels,
    )

def build(self) -> tuple[Path, ...]:
    """Emit generated sources when stale, then materialize addon migrations."""

    if not self.is_current():
        self.emit()
    return self.runtime_migrations().materialize()
```

Extend `check()` only after its existing source-drift branch:

```python
if drift:
    rendered = ", ".join(str(path) for path in drift)
    raise RuntimeError(f"generated runtime is stale: {rendered}")
self.runtime_migrations().check()
```

Change `_handle_build()` write mode to `runtime.build()` while leaving check mode as `runtime.check()`. Keep `emit_if_stale()` unchanged.

- [ ] **Step 4: Run lifecycle and regression tests**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py tests/test_compose.py tests/test_addons.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Runtime integration**

```bash
git add angee/compose/runtime.py angee/compose/management/commands/angee.py tests/test_compose.py tests/test_runtime_migrations.py
git commit -m "feat(compose): materialize migrations during build"
```

---

### Task 5: Ship the lossless parties Relationship migration

**Files:**
- Create: `addons/angee/parties/runtime_migrations/__init__.py`
- Create: `addons/angee/parties/runtime_migrations/relationship_anchor.py`
- Modify: `addons/angee/parties/addon.toml:3-13`
- Modify: `tests/test_runtime_migrations.py`

**Interfaces:**
- Produces declaration origin: `angee.parties:relationship_anchor`.
- Targets runtime label: `parties`.
- Source module: `angee.parties.runtime_migrations.relationship_anchor`.
- Applicability: old `from_party`/`to_party` state applies; complete new state and absent model skip; mixed state raises `ImproperlyConfigured`.

- [ ] **Step 1: Write failing parties applicability and state-transition tests**

Build an old state from `ModelState.from_model(angee.parties.models.Relationship)`: clone the model state, replace `party` with a `from_party` foreign key, replace `other_party` with a required/CASCADE `to_party` foreign key, remove `other_name`, restore old ordering, and restore the old uniqueness/distinct-party constraints. Add it to `ProjectState`.

Test:

```python
def test_parties_relationship_migration_preserves_renamed_foreign_keys() -> None:
    module = importlib.import_module("angee.parties.runtime_migrations.relationship_anchor")
    old_state = _old_relationship_state()

    assert module.applies(old_state) is True
    migrated = module.Migration("probe", "parties").mutate_state(old_state)
    relationship = migrated.models["parties", "relationship"]

    assert "party" in relationship.fields
    assert "other_party" in relationship.fields
    assert "other_name" in relationship.fields
    assert "from_party" not in relationship.fields
    assert "to_party" not in relationship.fields
    assert relationship.fields["other_party"].null is True
    assert relationship.fields["other_party"].remote_field.on_delete is models.SET_NULL
    assert relationship.options["ordering"] == ("party", "sqid")
    assert {constraint.name for constraint in relationship.options["constraints"]} == {
        "uq_relationship_edge",
        "ck_relationship_distinct_parties",
        "ck_relationship_has_other",
    }
```

Also assert `applies()` is false for `ProjectState()` and for the current complete model state, and raises for a mixed state containing both `from_party` and `party`.

- [ ] **Step 2: Run the parties tests and verify the module is absent**

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -k "parties_relationship" -v
```

Expected: import failure because the source migration module does not exist.

- [ ] **Step 3: Author the complete Django migration module and manifest declaration**

Add to `addons/angee/parties/addon.toml` before `[resources]`:

```toml
[[migrations]]
name = "relationship_anchor"
app_label = "parties"
module = "runtime_migrations.relationship_anchor"
```

Create `relationship_anchor.py` with:

```python
"""Preserve Relationship counterparties while moving to anchor vocabulary."""

from __future__ import annotations

import django.db.models.deletion
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether the exact pre-anchor Relationship state is present."""

    model = project_state.models.get(("parties", "relationship"))
    if model is None:
        return False
    fields = frozenset(model.fields)
    old = frozenset({"from_party", "to_party"})
    new = frozenset({"party", "other_party", "other_name"})
    if old <= fields and not fields & new:
        return True
    if new <= fields and not fields & old:
        return False
    raise ImproperlyConfigured(
        "angee.parties:relationship_anchor found a partial Relationship field transition: "
        f"{sorted(fields & (old | new))}"
    )


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint(model_name="relationship", name="uq_relationship_edge"),
        migrations.RemoveConstraint(model_name="relationship", name="ck_relationship_distinct_parties"),
        migrations.RenameField(model_name="relationship", old_name="from_party", new_name="party"),
        migrations.RenameField(model_name="relationship", old_name="to_party", new_name="other_party"),
        migrations.AlterField(
            model_name="relationship",
            name="other_party",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inbound_relationships",
                to="parties.party",
            ),
        ),
        migrations.AddField(
            model_name="relationship",
            name="other_name",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AlterModelOptions(name="relationship", options={"ordering": ("party", "sqid")}),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.UniqueConstraint(
                condition=models.Q(other_party__isnull=False),
                fields=("party", "other_party", "kind"),
                name="uq_relationship_edge",
            ),
        ),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.CheckConstraint(
                condition=models.Q(other_party__isnull=True) | ~models.Q(party=models.F("other_party")),
                name="ck_relationship_distinct_parties",
            ),
        ),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.CheckConstraint(
                condition=models.Q(other_party__isnull=False) | ~models.Q(other_name=""),
                name="ck_relationship_has_other",
            ),
        ),
    ]
```

- [ ] **Step 4: Prove the transition and no destructive autodetection remains**

Extend the acceptance test to run `MigrationAutodetector(migrated, current_state)._detect_changes()` and assert no `RemoveField` or `AddField` for `party`/`other_party`; if the authored migration fully matches, assert no `parties` changes at all.

Run:

```bash
uv run python -m pytest tests/test_runtime_migrations.py -k "parties_relationship" -v
```

Expected: all parties acceptance tests pass.

- [ ] **Step 5: Commit the first addon migration**

```bash
git add addons/angee/parties/addon.toml addons/angee/parties/runtime_migrations tests/test_runtime_migrations.py
git commit -m "feat(parties): preserve relationship fields on upgrade"
```

---

### Task 6: Document the seam and verify the complete change

**Files:**
- Modify: `docs/composer.md:110-155`
- Modify: `docs/backend/guidelines.md:480-520`
- Modify: `angee/addons.py` docstrings
- Modify: `angee/compose/runtime.py` docstrings
- Modify: `angee/compose/management/commands/angee.py:101-125`

**Interfaces:**
- Documents the exact `[[migrations]]`, module `Migration`/`applies`, append-only, build-only, and Django-owned execution contracts.

- [ ] **Step 1: Update owner docstrings and durable documentation**

In `docs/composer.md`, add a subsection after runtime migrations explaining:

```markdown
### Addon-owned runtime migrations

An addon may declare ordered `[[migrations]]` entries in `addon.toml`. Each
entry names a stable source module, target runtime label, and migration name.
Explicit `angee build` copies an applicable ordinary Django migration onto the
target's current downstream leaf before `makemigrations`; normal Django boot
never materializes migrations. The module's pure `applies(ProjectState)` guard
selects only its exact historical transition, while Django owns all later graph
loading, execution, rollback, and recording.
```

In `docs/backend/guidelines.md`, add one migration pitfall/rule: source manual migrations are append-only and self-contained; data functions use historical apps and `_base_manager`; `applies` must fail on recognized partial state; never edit a materialized runtime migration or import current models.

Update the owning code docstrings without duplicating field inventories.

- [ ] **Step 2: Run formatting, static checks, and focused tests**

Run each command separately:

```bash
uv run python -m ruff check angee/addons.py angee/compose addons/angee/parties/runtime_migrations tests/test_addons.py tests/test_compose.py tests/test_runtime_migrations.py --no-cache
uv run python -m mypy angee addons
uv run python -m pytest tests/test_addons.py tests/test_compose.py tests/test_runtime_migrations.py
```

Expected: every command exits 0.

- [ ] **Step 3: Run the full backend regression suite**

Run:

```bash
uv run python -m pytest
```

Expected: all tests pass with no new warnings attributable to this change.

- [ ] **Step 4: Verify a real runtime build and check**

Run each command separately from the workspace root:

```bash
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py angee build --check
```

Expected: build succeeds, check prints `angee build --check: ok`, and a fresh project with no old parties state does not materialize `relationship_anchor`.

- [ ] **Step 5: Commit documentation and final verification fixes**

```bash
git add docs/composer.md docs/backend/guidelines.md angee/addons.py angee/compose/runtime.py angee/compose/management/commands/angee.py
git commit -m "docs(compose): explain addon runtime migrations"
```

- [ ] **Step 6: Confirm final branch state**

Run:

```bash
git status --short
git log --oneline --decorate -7
```

Expected: clean worktree; the design, plan, contract, materializer, runtime integration, parties migration, and docs commits are present on `workspace/manual-migrations`.
