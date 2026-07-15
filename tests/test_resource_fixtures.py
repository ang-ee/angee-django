"""Check every addon's declared resource fixtures against their model fields.

``manage.py resources load`` writes each declared row through the model, so a
fixture value the model no longer accepts breaks a documented bring-up step. No
lint, type, or unit check reads fixture *values* against the field that stores
them; this module does.

It runs off the composer rather than the concrete models: the resources addon
reads each addon's declared fixtures from disk, and ``Runtime`` groups every
addon's abstract source models by composition label with no database and no
emission — so the sweep covers addons whose concrete models the bare
``tests.settings`` registry never builds, which is where this drift class has
lived. Django still labels each source from the app registry; ``composed_runtime``
owns what that costs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import Field
from django.test import override_settings

from angee.addons import available_addons, is_angee_addon
from angee.base.models import AngeeModel
from angee.compose.appgraph import AppGraph
from angee.compose.runtime import Runtime
from angee.resources.entries import GRANT_KIND, ResourceEntry, resource_manifest_for

ADDON_DIRS = (
    Path(__file__).resolve().parents[1] / "addons",
    Path(__file__).resolve().parents[1] / "examples" / "notes-angee" / "addons",
)
"""The repository's two addon source roots — the base addons and the example's consumer addons.

Discovery reads every ``addon.toml`` below these roots, so a new addon joins the
sweep by existing. A hand-written addon list is what let the ``lifecycle: active``
drift survive.
"""


_EXAMPLE_ADDON = "example.notes"
"""The example project's consumer addon — installed per-fixture, not in settings."""


def _addon_names() -> tuple[str, ...]:
    """Return every addon name declared by an ``addon.toml`` under `ADDON_DIRS`."""

    return tuple(available_addons(ADDON_DIRS))


def _composed_field(runtime: Runtime, model: type[AngeeModel], name: str) -> Field[Any, Any]:
    """Return the field named ``name`` on the model composed from ``model``.

    An abstract source carries only its own fields, while the emitted concrete
    class also carries its ``extends`` donors' fields and, for a materialized
    child, its runtime parent's — ``integrate.Integration`` owns the ``lifecycle``
    its ``VcsBridge``/``InferenceProvider`` children are seeded with. So the
    lookup follows the same declarations the composer emits from: the source, its
    donors, then the runtime parent, recursively.

    Raises ``FieldDoesNotExist`` when no composed base declares ``name``.
    """

    donors = tuple(
        base
        for extension in runtime.extensions.get(model._meta.label_lower, ())
        for base in extension.get_extension_bases()
    )
    for base in (model, *donors):
        try:
            return base._meta.get_field(name)
        except FieldDoesNotExist:
            continue
    target = model.get_extension_target()
    if target is not None and model.is_runtime_model():
        return _composed_field(runtime, runtime.source_models_by_composition_label[target], name)
    raise FieldDoesNotExist(f"{model._meta.label} composes no field named {name!r}")


@dataclass(slots=True)
class _FixtureSweep:
    """One pass over every declared resource fixture row, collecting rejections.

    ``run`` reads each addon's ``[resources]`` manifest, parses every row of every
    non-grant entry, and asks the field that owns each declared non-relational
    value to coerce it. Relational values are xref handles resolved by the
    loader's widgets against the ledger, not literals the field can coerce, so
    they are skipped.
    """

    runtime: Runtime
    """Composed runtime whose source models resolve each row's ``model_label``."""

    failures: list[str] = field(default_factory=list)
    """One message per rejected value, unknown field, or unresolvable model label."""

    checked_values: int = 0
    """Declared non-relational values coerced by their field."""

    def run(self) -> _FixtureSweep:
        """Sweep every discovered addon's declared resource rows."""

        for addon in self.runtime.addons:
            for entry in self._entries(addon):
                self._check_entry(entry)
        return self

    def _entries(self, addon: AppConfig) -> Iterator[ResourceEntry]:
        """Yield one addon's declared row entries, in tier then declaration order."""

        for tier, declarations in sorted(resource_manifest_for(addon).items()):
            for declaration in declarations:
                entry = ResourceEntry.from_declaration(addon, tier, declaration)
                # A grants entry declares REBAC tuples, not model rows.
                if entry.kind != GRANT_KIND:
                    yield entry

    def _check_entry(self, entry: ResourceEntry) -> None:
        """Check every declared value of every row in one resource entry."""

        for row in entry.read_resource_rows():
            model = self.runtime.source_models_by_composition_label.get(row.model_label.lower())
            if model is None:
                self._fail(entry, row.xref, f"targets unknown model {row.model_label!r}")
                continue
            for name, value in row.values.items():
                self._check_value(entry, row.xref, model, name, value)

    def _check_value(
        self,
        entry: ResourceEntry,
        xref: str,
        model: type[AngeeModel],
        name: str,
        value: Any,
    ) -> None:
        """Ask the field that owns one declared value to coerce it."""

        try:
            model_field = _composed_field(self.runtime, model, name)
        except FieldDoesNotExist as error:
            self._fail(entry, xref, str(error))
            return
        if model_field.is_relation:
            return
        self.checked_values += 1
        try:
            # The coercion `Field.pre_save` runs on every write: a value it
            # rejects fails `manage.py resources load`.
            model_field.to_python(value)
        except ValidationError as error:
            self._fail(entry, xref, f"{name}={value!r} rejected by {type(model_field).__name__}: {error.messages}")

    def _fail(self, entry: ResourceEntry, xref: str, problem: str) -> None:
        """Record one failure, naming the file and row that declared it."""

        self.failures.append(f"{entry.display} [{entry.tier}] row {xref!r}: {problem}")


@pytest.fixture(scope="module")
def composed_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Runtime]:
    """Return a runtime composing every addon declared under `ADDON_DIRS`.

    Construction alone resolves and groups the source models; nothing is emitted,
    so ``runtime_dir`` is an unused temporary path.

    Django labels an abstract source from the app registry when the class is
    created, so every addon that declares models must be installed for its
    sources to compose. ``tests.settings`` names the ``angee.*`` ones. The
    example's consumer addon cannot go there: ``example`` resolves only through
    pytest's ``pythonpath``, which is not applied by the time ``django.setup()``
    reads ``INSTALLED_APPS``, so naming it there fails the whole session. It is
    installed here instead — late enough to import, and safe because no other
    test imports its models first.
    """

    names = _addon_names()
    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, _EXAMPLE_ADDON]):
        yield Runtime(
            tuple(config for config in AppGraph().resolve(names) if is_angee_addon(config)),
            runtime_dir=tmp_path_factory.mktemp("runtime"),
        )


def test_declared_resource_values_are_accepted_by_their_model_field(composed_runtime: Runtime) -> None:
    """Every value seeded by every addon's resource fixtures is one its field still accepts.

    For each addon declaring ``[resources]``, each declared tier, and every row of
    every non-grant entry: the row's ``model_label`` resolves to a composed source
    model, each declared name is a field that model composes, and each
    non-relational value passes that field's ``to_python``. An unresolvable label
    and an unknown field name are failures, not skips. Every failure is collected
    so one run names every bad fixture.
    """

    sweep = _FixtureSweep(composed_runtime).run()

    assert sweep.checked_values, "no resource fixture values were discovered"
    assert not sweep.failures, "resource fixtures declare values their model rejects:\n" + "\n".join(sweep.failures)
