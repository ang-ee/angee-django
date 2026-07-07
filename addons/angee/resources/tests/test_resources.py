"""Tests for resource declarations, ordering, fetching, and loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, models
from import_export.results import Result, RowResult
from rebac import system_context

from angee.addons import AddonContract
from angee.base.models import AngeeModel
from angee.resources.entries import EntryGraph, GrantGroup, GrantRow, LoadResult, ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.grants import _grant_tuples, materialize_grant_groups
from angee.resources.loader import build_resource
from angee.resources.models import Resource
from angee.resources.tiers import ResourceTier
from angee.resources.widgets import (
    XrefForeignKeyWidget,
    XrefManyToManyWidget,
    resolve_ledger_xref,
    resolve_xref,
)


@dataclass(slots=True)
class Addon:
    """Small addon stand-in exposing normalized resource declarations."""

    name: str
    """Full dotted addon name."""

    label: str
    """Django app label."""

    path: str
    """Filesystem root for local resource files."""

    _addon_contract: AddonContract
    """In-memory addon contract used by the resources manifest owner."""


def addon(
    tmp_path: Path,
    *,
    name: str = "tests.resource_addon",
    label: str = "resource_addon",
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> Addon:
    """Return a resource addon rooted at ``tmp_path``."""

    return Addon(
        name=name,
        label=label,
        path=str(tmp_path),
        _addon_contract=AddonContract(
            name=name,
            resources=manifest or {"master": (), "install": (), "demo": ()},
        ),
    )


def entry(
    tmp_path: Path,
    declaration: dict[str, Any],
    *,
    tier: str = "master",
    owner: Addon | None = None,
) -> ResourceEntry:
    """Return a resource entry for one declaration mapping."""

    return ResourceEntry.from_declaration(
        owner or addon(tmp_path),
        tier,
        declaration,
    )


def test_load_result_counts_import_export_totals() -> None:
    """Load accounting delegates row-type counts to django-import-export."""

    result = Result()
    result.totals[RowResult.IMPORT_TYPE_NEW] = 2
    result.totals[RowResult.IMPORT_TYPE_UPDATE] = 3
    result.totals[RowResult.IMPORT_TYPE_SKIP] = 5

    counted = LoadResult(created=1, updated=1, skipped=1).with_result(result)

    assert counted == LoadResult(created=3, updated=4, skipped=6)


def test_resource_condition_q_evaluator_supported_lookup_set(tmp_path: Path) -> None:
    """Resource adoption conditions intentionally support exact/isnull on concrete fields."""

    class ConditionThing(AngeeModel):
        """Model used to exercise resource adoption condition matching."""

        name = models.CharField(max_length=40)
        status = models.CharField(max_length=40, default="active")
        flag = models.CharField(max_length=40, blank=True, null=True)

        class Meta:
            """Django model options for the condition-matching test model."""

            app_label = "base"

    class ConditionLedger(Resource):
        """Concrete resource ledger for condition-matching tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource = build_resource(
        ConditionThing,
        entry(tmp_path, {"path": "resources/010_base.conditionthing.csv"}),
        ledger_model=ConditionLedger,
        addon_aliases={},
    )
    row = {"_xref": "one", "name": "api-key", "status": "active"}

    assert resource._row_matches_condition(
        row,
        models.Q(flag__isnull=True) & models.Q(status__exact="active") & ~models.Q(name__exact="blocked"),
    )
    assert resource._row_matches_condition(row, models.Q(status="active"))
    with pytest.raises(ImproperlyConfigured, match="lookup 'name__icontains' is not supported"):
        resource._row_matches_condition(row, models.Q(name__icontains="api"))


def test_resource_entry_reads_structured_rows_and_fields(
    tmp_path: Path,
) -> None:
    """YAML resources can declare a model and field envelope."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n  model: base.ImportNote\nrows:\n  - _xref: n1\n    fields:\n      title: First\n",
        encoding="utf-8",
    )

    rows = entry(
        tmp_path,
        {"path": "resources/notes.yaml"},
    ).read_resource_rows()

    assert len(rows) == 1
    assert rows[0].model_label == "base.ImportNote"
    assert rows[0].xref == "n1"
    assert rows[0].dataset_row == {"_xref": "n1", "title": "First"}


def test_resource_entry_rejects_reserved_keys_in_structured_fields(
    tmp_path: Path,
) -> None:
    """Structured field envelopes cannot override loader-owned keys."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n  model: base.ImportNote\nrows:\n  - _xref: n1\n    fields:\n      _xref: n2\n      title: First\n",
        encoding="utf-8",
    )

    with pytest.raises(ImproperlyConfigured, match="reserved keys: _xref"):
        entry(
            tmp_path,
            {"path": "resources/notes.yaml"},
        ).read_resource_rows()


def test_resource_entry_allows_model_field_in_structured_fields(
    tmp_path: Path,
) -> None:
    """`model` is a real field name under an explicit `fields:` (label lives in `_meta`)."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "agents.yaml").write_text(
        "_meta:\n  model: base.ImportNote\nrows:\n  - _xref: a1\n    fields:\n      model: notes.model_x\n",
        encoding="utf-8",
    )

    rows = entry(tmp_path, {"path": "resources/agents.yaml"}).read_resource_rows()

    assert rows[0].model_label == "base.ImportNote"
    assert rows[0].dataset_row == {"_xref": "a1", "model": "notes.model_x"}


def test_resource_entry_rejects_model_conflicts(tmp_path: Path) -> None:
    """File metadata cannot disagree with the entry model."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.yaml").write_text(
        "_meta:\n  model: base.Other\nrows:\n  - _xref: a\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLoadError, match="model conflict"):
        entry(
            tmp_path,
            {"path": "resources/data.yaml", "model": "base.ImportNote"},
        ).read_resource_rows()


def test_resource_entry_rejects_unsupported_formats(tmp_path: Path) -> None:
    """Only text formats owned by the resource loader are accepted."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.xlsx").write_bytes(b"binary")

    with pytest.raises(ImproperlyConfigured, match="unsupported format"):
        entry(tmp_path, {"path": "resources/data.xlsx"}).read_resource_rows()


def test_entry_graph_respects_same_and_cross_addon_dependencies(
    tmp_path: Path,
) -> None:
    """Dependency edges order resources across selected addons."""

    upstream_addon = addon(tmp_path, name="tests.upstream", label="upstream")
    downstream_addon = addon(
        tmp_path,
        name="tests.downstream",
        label="downstream",
    )
    first = ResourceEntry.from_declaration(
        upstream_addon,
        "master",
        {"path": "a.csv"},
    )
    second = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "b.csv", "depends_on": ("upstream:a.csv",)},
    )
    third = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "c.csv", "depends_on": ("b.csv",)},
    )

    ordered = EntryGraph.from_entries([third, second, first]).ordered()

    assert [item.source for item in ordered] == ["a.csv", "b.csv", "c.csv"]


def test_resource_entry_uses_normalized_dependency_tuple(
    tmp_path: Path,
) -> None:
    """Resource entries consume dependency tuples normalized by AppConfig."""

    resource_entry = ResourceEntry.from_declaration(
        addon(tmp_path),
        "master",
        {"path": "b.csv", "depends_on": ("a.csv",)},
    )

    assert resource_entry.depends_on == ("a.csv",)


def test_resource_tiers_include_prerequisites() -> None:
    """Selecting a later tier also selects the earlier tiers it builds on."""

    assert ResourceTier.with_prerequisites(["master"]) == ("master",)
    assert ResourceTier.with_prerequisites(["install"]) == ("master", "install")
    assert ResourceTier.with_prerequisites(["demo"]) == ("master", "install", "demo")
    assert ResourceTier.with_prerequisites(["demo", "master"]) == ("master", "install", "demo")


def test_entry_graph_detects_cycles(tmp_path: Path) -> None:
    """Dependency cycles fail before any rows load."""

    owner = addon(tmp_path)
    first = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "a.csv", "depends_on": ("b.csv",)},
    )
    second = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "b.csv", "depends_on": ("a.csv",)},
    )

    with pytest.raises(ResourceLoadError, match="cycle"):
        EntryGraph.from_entries([first, second]).ordered()


def test_path_source_materializes_to_the_addon_file(tmp_path: Path) -> None:
    """The built-in ``path`` source materializes to the addon-relative local file."""

    owner = addon(tmp_path)
    resource_entry = ResourceEntry.from_declaration(owner, "master", {"path": "data/users.csv"})

    assert resource_entry.source_key == "path"
    assert resource_entry.source == "data/users.csv"
    assert resource_entry.materialize() == Path(owner.path) / "data/users.csv"


def test_entry_requires_exactly_one_registered_source(tmp_path: Path) -> None:
    """A declaration must name exactly one configured source key."""

    owner = addon(tmp_path)
    with pytest.raises(ImproperlyConfigured, match="exactly one source"):
        ResourceEntry.from_declaration(owner, "master", {"model": "base.ImportNote"})


def test_unregistered_source_key_raises_with_install_hint(tmp_path: Path) -> None:
    """Materializing an entry whose source kind is not registered fails with a hint."""

    unknown = ResourceEntry(addon=addon(tmp_path), tier="master", source_key="ipfs", source_value="x")
    with pytest.raises(ImproperlyConfigured, match="not registered"):
        unknown.materialize()


def test_resource_unique_constraint_is_addon_xref_pair() -> None:
    """The ledger identity is the source addon and row xref."""

    constraints = {
        constraint.name: constraint
        for constraint in Resource.Meta.constraints
        if isinstance(constraint, models.UniqueConstraint)
    }

    assert constraints["%(app_label)s_resource_addon_xref"].fields == (
        "source_addon",
        "xref",
    )


@pytest.mark.django_db(transaction=True)
def test_resolve_xref_accepts_addon_label_alias() -> None:
    """A label-form xref resolves to the canonical addon ledger row."""

    class ResolveExactTarget(models.Model):
        """Target model resolved by exact xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveExactLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (
        ResolveExactTarget,
        ResolveExactLedger,
    )
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        target = ResolveExactTarget.objects.create(name="target")
        ResolveExactLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="target",
            target_model="base.ResolveExactTarget",
            target_id=str(target.pk),
        )

        resolved = resolve_xref(
            "resource_addon.target",
            ResolveExactLedger,
            {
                "resource_addon": "tests.resource_addon",
                "tests.resource_addon": "tests.resource_addon",
            },
        )

        assert resolved == target
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resolve_xref_reports_ambiguous_source_rows() -> None:
    """Duplicate addon/xref ledger rows fail before choosing a target."""

    class ResolveAmbiguousTargetA(models.Model):
        """First target model for ambiguous xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveAmbiguousTargetB(models.Model):
        """Second target model for ambiguous xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveAmbiguousLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (
        ResolveAmbiguousTargetA,
        ResolveAmbiguousTargetB,
        ResolveAmbiguousLedger,
    )
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        first = ResolveAmbiguousTargetA.objects.create(name="first")
        second = ResolveAmbiguousTargetB.objects.create(name="second")
        ResolveAmbiguousLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="shared",
            target_model="base.ResolveAmbiguousTargetA",
            target_id=str(first.pk),
        )
        ResolveAmbiguousLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="shared",
            target_model="base.ResolveAmbiguousTargetB",
            target_id=str(second.pk),
        )

        with pytest.raises(ValueError, match="ambiguous xref"):
            resolve_xref(
                "tests.resource_addon.shared",
                ResolveAmbiguousLedger,
                {"tests.resource_addon": "tests.resource_addon"},
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resolve_ledger_xref_binds_ledger_and_app_registry_aliases(monkeypatch) -> None:
    """The loader owns persona lookup: ledger + app-registry aliases in one call.

    A demo-seed hook resolves ``<addon>.<xref>`` by the same alias convention the
    loader builds per addon — the installed app's canonical name resolves from both
    its dotted name and its short label — so the persona-lookup logic lives once
    here, not copied byte-for-byte into every ``after_resource_load`` donor.
    """

    class LedgerXrefTarget(models.Model):
        """Target row a ledger xref points at."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test target model."""

            app_label = "base"

    class LedgerXrefLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (LedgerXrefTarget, LedgerXrefLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    # No concrete ``resources.Resource`` exists under bare test settings (the composer
    # is not run), so stand the ledger model in for the helper's own ledger lookup only;
    # every other ``get_model`` (the target-model resolution inside ``resolve_xref``)
    # delegates to the real registry, and the addon-alias map is built from the real
    # installed apps.
    real_get_model = apps.get_model

    def fake_get_model(app_label: str, model_name: str, *args: Any, **kwargs: Any) -> Any:
        if (app_label, model_name) == ("resources", "Resource"):
            return LedgerXrefLedger
        return real_get_model(app_label, model_name, *args, **kwargs)

    monkeypatch.setattr(apps, "get_model", fake_get_model)
    try:
        target = LedgerXrefTarget.objects.create(name="alice")
        LedgerXrefLedger.objects.create(
            source_addon="angee.resources",
            xref="user_alice",
            target_model="base.LedgerXrefTarget",
            target_id=str(target.pk),
        )

        # ``resources`` is the short label of the ``angee.resources`` app, so the
        # app-registry alias map resolves the label form to the canonical addon name.
        assert resolve_ledger_xref("resources.user_alice") == target
        # An unresolved handle is a graceful ``None``, never a raise.
        assert resolve_ledger_xref("resources.user_missing") is None
        assert resolve_ledger_xref("no_such_addon.user_alice") is None
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_xref_widgets_resolve_mti_descendant_to_parent_fk() -> None:
    """An xref on an MTI child resolves a foreign key / m2m to its parent (F-d).

    Mirrors the production shape ``parties.Party`` (MTI parent) ← a
    ``parties.Organization`` (materialized child) xref: a ``runtime = True``
    child shares its parent's primary key, so binding it to a ``Party`` relation
    is valid. The guard still fails fast on a genuinely unrelated type and never
    reaches *downward* from a parent xref to a child-bound relation.
    """

    class XrefMtiParent(models.Model):
        """MTI parent standing in for ``parties.Party``."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the MTI parent test model."""

            app_label = "base"

    class XrefMtiChild(XrefMtiParent):
        """MTI child standing in for ``parties.Organization``."""

        detail = models.CharField(max_length=40, blank=True, default="")

        class Meta:
            """Django model options for the MTI child test model."""

            app_label = "base"

    class XrefMtiPeer(models.Model):
        """Unrelated model proving the fail-fast guard still bites."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the unrelated peer test model."""

            app_label = "base"

    class XrefMtiLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (
        XrefMtiParent,
        XrefMtiChild,
        XrefMtiPeer,
        XrefMtiLedger,
    )
    aliases = {"tests.resource_addon": "tests.resource_addon"}

    def _bind(widget: Any) -> Any:
        widget.ledger_model = XrefMtiLedger
        widget.addon_aliases = aliases
        return widget

    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        child = XrefMtiChild.objects.create(name="Acme", detail="org")
        peer = XrefMtiPeer.objects.create(name="Nope")
        parent_row = XrefMtiParent.objects.create(name="Bare")
        for xref, target in (
            ("acme", "base.XrefMtiChild"),
            ("nope", "base.XrefMtiPeer"),
            ("bare", "base.XrefMtiParent"),
        ):
            XrefMtiLedger.objects.create(
                source_addon="tests.resource_addon",
                xref=xref,
                target_model=target,
                target_id=str({"acme": child, "nope": peer, "bare": parent_row}[xref].pk),
            )

        parent_fk = _bind(XrefForeignKeyWidget(model=XrefMtiParent))
        parent_m2m = _bind(XrefManyToManyWidget(model=XrefMtiParent))

        # The FK/M2M to the MTI parent accepts the child xref (the F-d fix).
        assert parent_fk.clean("tests.resource_addon.acme") == child
        assert parent_m2m.clean("tests.resource_addon.acme") == [child]

        # A genuinely unrelated xref still fails fast.
        with pytest.raises(ValueError, match="not base.XrefMtiParent"):
            parent_fk.clean("tests.resource_addon.nope")

        # A parent-only xref bound to a child relation fails fast (no downward reach).
        child_fk = _bind(XrefForeignKeyWidget(model=XrefMtiChild))
        with pytest.raises(ValueError, match="not base.XrefMtiChild"):
            child_fk.clean("tests.resource_addon.bare")
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_manager_loads_rows_and_resolves_xrefs(
    tmp_path: Path,
) -> None:
    """Resource rows load with label-form FK xrefs on a fresh ledger."""

    class ImportUser(AngeeModel):
        """User-like model loaded from resources."""

        username = models.CharField(max_length=40, unique=True)
        first_name = models.CharField(max_length=40, blank=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"
            rebac_resource_type = "base/import-user"

    class ImportNote(AngeeModel):
        """Note-like model referencing a resource-loaded user."""

        title = models.CharField(max_length=80)
        tags = models.JSONField(default=list, blank=True)
        created_by = models.ForeignKey(ImportUser, on_delete=models.CASCADE)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"
            rebac_resource_type = "base/import-note"

    class ResourceLedger(Resource):
        """Concrete resource ledger for the test database."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    owner = _write_resource_files(tmp_path)
    models_to_create = (ImportUser, ImportNote, ResourceLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        result = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )

        with system_context(reason="resource load assertions"):
            alice = ImportUser.objects.get(username="alice")
            note = ImportNote.objects.get(title="Framework map")
        ledger_xrefs = set(ResourceLedger.objects.values_list("xref", flat=True))

        assert result.created == 2
        assert result.loaded == 2
        assert alice.first_name == "Alice"
        assert note.created_by == alice
        assert note.tags == ["composition", "resources"]
        assert ledger_xrefs == {"user_alice", "note_framework_map"}

        second = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 2
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_manager_keeps_same_path_groups_addon_scoped(
    tmp_path: Path,
) -> None:
    """Same-named files in separate addons keep addon-local ledger ownership."""

    class SharedUser(AngeeModel):
        """User-like model loaded from multiple addon resource files."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class SharedNote(AngeeModel):
        """Note-like model referencing an addon-local resource user."""

        title = models.CharField(max_length=80)
        created_by = models.ForeignKey(SharedUser, on_delete=models.CASCADE)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class SharedLedger(Resource):
        """Concrete resource ledger for addon-scoped grouping tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    for root in (alpha_root, beta_root):
        (root / "resources").mkdir(parents=True)
    (alpha_root / "resources" / "010_base.shareduser.csv").write_text(
        "_xref,username\nalpha_user,alpha\n",
        encoding="utf-8",
    )
    (beta_root / "resources" / "010_base.shareduser.csv").write_text(
        "_xref,username\nbeta_user,beta\n",
        encoding="utf-8",
    )
    (beta_root / "resources" / "020_base.sharednote.yaml").write_text(
        "- _xref: beta_note\n  title: Beta note\n  created_by: beta.beta_user\n",
        encoding="utf-8",
    )
    alpha = addon(
        alpha_root,
        name="tests.alpha",
        label="alpha",
        manifest={
            "master": ({"path": "resources/010_base.shareduser.csv"},),
            "install": (),
            "demo": (),
        },
    )
    beta = addon(
        beta_root,
        name="tests.beta",
        label="beta",
        manifest={
            "master": (
                {"path": "resources/010_base.shareduser.csv"},
                {
                    "path": "resources/020_base.sharednote.yaml",
                    "depends_on": "resources/010_base.shareduser.csv",
                },
            ),
            "install": (),
            "demo": (),
        },
    )

    models_to_create = (SharedUser, SharedNote, SharedLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        SharedLedger.objects.load_addons(
            (alpha, beta),
            tiers=[Resource.Tier.MASTER],
        )

        with system_context(reason="resource grouping assertions"):
            beta_user = SharedUser.objects.get(username="beta")
            beta_note = SharedNote.objects.get(title="Beta note")
        ledgers = {
            (row.source_addon, row.xref): row.target_model
            for row in SharedLedger.objects.order_by("source_addon", "xref")
        }

        assert beta_note.created_by == beta_user
        assert ledgers[("tests.alpha", "alpha_user")] == "base.SharedUser"
        assert ledgers[("tests.beta", "beta_user")] == "base.SharedUser"
        assert ledgers[("tests.beta", "beta_note")] == "base.SharedNote"
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_load_rejects_existing_xref_for_another_model(
    tmp_path: Path,
) -> None:
    """A later load cannot reuse an addon xref for another target model."""

    class CollisionUser(AngeeModel):
        """First target model for ledger collision tests."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class CollisionNote(AngeeModel):
        """Second target model for ledger collision tests."""

        title = models.CharField(max_length=80)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class CollisionLedger(Resource):
        """Concrete resource ledger for collision tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.collisionuser.csv").write_text(
        "_xref,username\nshared,alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.collisionnote.csv").write_text(
        "_xref,title\nshared,Same xref\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": ({"path": "resources/010_base.collisionuser.csv"},),
            "install": ({"path": "resources/020_base.collisionnote.csv"},),
            "demo": (),
        },
    )

    models_to_create = (CollisionUser, CollisionNote, CollisionLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        CollisionLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )

        with pytest.raises(ResourceLoadError, match="xref collision"):
            CollisionLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.INSTALL],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_validate_cleans_rows_and_resolves_xrefs(
    tmp_path: Path,
) -> None:
    """Validation runs widget cleaning inside a rolled-back import."""

    class ValidateNote(AngeeModel):
        """Note-like model with an FK that must resolve by xref."""

        title = models.CharField(max_length=80)
        created_by = models.ForeignKey(
            "base.ValidateUser",
            on_delete=models.CASCADE,
        )

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ValidateUser(AngeeModel):
        """User-like model referenced by validation rows."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ValidateLedger(Resource):
        """Concrete resource ledger for validation tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.validatenote.yaml").write_text(
        "- _xref: note\n  title: Broken reference\n  created_by: tests.resource_addon.missing\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": ({"path": "resources/010_base.validatenote.yaml"},),
            "install": (),
            "demo": (),
        },
    )

    models_to_create = (ValidateUser, ValidateNote, ValidateLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        with pytest.raises(ResourceLoadError, match="unresolved xref"):
            ValidateLedger.objects.validate_addons(
                (owner,),
                tiers=[Resource.Tier.MASTER],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_is_opt_in(tmp_path: Path) -> None:
    """Rows without ledgers adopt existing targets only when requested."""

    class AdoptUser(AngeeModel):
        """Model with a unique natural key suitable for adoption."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class AdoptLedger(Resource):
        """Concrete resource ledger for adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.adoptuser.csv").write_text(
        "_xref,username\nexisting,alice\n",
        encoding="utf-8",
    )
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] = {
        "master": (),
        "install": (
            {
                "path": "resources/010_base.adoptuser.csv",
                "adopt": True,
            },
        ),
        "demo": (),
    }
    owner = addon(tmp_path, manifest=manifest)
    no_adopt = addon(
        tmp_path,
        manifest={
            **manifest,
            "install": ({"path": "resources/010_base.adoptuser.csv"},),
        },
    )

    models_to_create = (AdoptUser, AdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        AdoptUser.objects.create(username="alice")
        with pytest.raises((IntegrityError, ResourceLoadError)):
            AdoptLedger.objects.load_addons(
                (no_adopt,),
                tiers=[Resource.Tier.INSTALL],
            )

        result = AdoptLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 0
        assert result.updated == 1
        assert result.skipped == 0
        with system_context(reason="resource adoption assertions"):
            assert AdoptUser.objects.count() == 1
        assert AdoptLedger.objects.get(xref="existing").target_id
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_uses_explicit_unique_field(
    tmp_path: Path,
) -> None:
    """A named adoption field resolves ambiguity between unique fields."""

    class ExplicitAdoptUser(AngeeModel):
        """Model with multiple unique fields."""

        username = models.CharField(max_length=40, unique=True)
        email = models.EmailField(unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ExplicitAdoptLedger(Resource):
        """Concrete resource ledger for explicit adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.explicitadoptuser.csv").write_text(
        "_xref,username,email\nexisting,alice,alice@example.test\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.explicitadoptuser.csv",
                    "adopt": "username",
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (ExplicitAdoptUser, ExplicitAdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        ExplicitAdoptUser.objects.create(
            username="alice",
            email="alice@example.test",
        )

        result = ExplicitAdoptLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.updated == 1
        assert result.skipped == 0
        with system_context(reason="explicit adoption assertions"):
            assert ExplicitAdoptUser.objects.count() == 1
        assert ExplicitAdoptLedger.objects.get(xref="existing").target_id
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_repairs_stale_ledger_target(tmp_path: Path) -> None:
    """A stale ledger target is repaired through the declared adoption key."""

    class StaleLedgerUser(AngeeModel):
        """Model with a unique natural key and a mutable seeded value."""

        username = models.CharField(max_length=40, unique=True)
        label = models.CharField(max_length=80, blank=True)

        class Meta:
            """Django model options for the stale-ledger adoption test model."""

            app_label = "base"

    class StaleLedger(Resource):
        """Concrete resource ledger for stale-ledger adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.staleledgeruser.csv").write_text(
        "_xref,username,label\nadmin,admin,Seeded\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.staleledgeruser.csv",
                    "adopt": "username",
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (StaleLedgerUser, StaleLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        existing = StaleLedgerUser.objects.create(username="admin", label="Existing")
        StaleLedger.objects.create(
            source_addon=owner.name,
            source_path="resources/010_base.staleledgeruser.csv",
            tier=Resource.Tier.INSTALL,
            xref="admin",
            content_hash="sha256:stale",
            target_model=StaleLedgerUser._meta.label,
            target_id="usr_stale",
        )

        result = StaleLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 0
        assert result.updated == 1
        assert result.skipped == 0
        existing.refresh_from_db()
        assert existing.label == "Seeded"
        with system_context(reason="stale-ledger adoption assertions"):
            assert StaleLedgerUser.objects.count() == 1
        assert StaleLedger.objects.get(xref="admin").target_id == existing.public_id

        second = StaleLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 1
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_accepts_composite_unique_fields(tmp_path: Path) -> None:
    """Resource adoption can match existing rows by a composite unique key."""

    class CompositeClient(AngeeModel):
        """Model whose public identity is a composite natural key."""

        slug = models.SlugField()
        environment = models.CharField(max_length=32, default="prod")
        label = models.CharField(max_length=80, blank=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"
            constraints = (
                models.UniqueConstraint(
                    fields=("slug", "environment"),
                    name="uniq_resource_composite_client",
                ),
            )

    class CompositeLedger(Resource):
        """Concrete resource ledger for composite adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.compositeclient.csv").write_text(
        "_xref,slug,environment,label\nanthropic,anthropic,prod,Seeded\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.compositeclient.csv",
                    "adopt": ["slug", "environment"],
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (CompositeClient, CompositeLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        existing = CompositeClient.objects.create(slug="anthropic", environment="prod", label="Existing")

        result = CompositeLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 0
        assert result.updated == 1
        assert result.skipped == 0
        existing.refresh_from_db()
        assert existing.label == "Seeded"
        assert CompositeLedger.objects.get(xref="anthropic").target_id == existing.public_id

        second = CompositeLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 1

        (resource_dir / "010_base.compositeclient.csv").write_text(
            "_xref,slug,environment,label\nanthropic,anthropic,prod,Changed\n",
            encoding="utf-8",
        )
        third = CompositeLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )
        assert third.created == 0
        assert third.updated == 1
        assert third.skipped == 0
        existing.refresh_from_db()
        assert existing.label == "Changed"
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_accepts_conditional_composite_unique_fields(tmp_path: Path) -> None:
    """Resource adoption can reuse rows governed by a conditional composite unique key."""

    class ConditionalOwner(AngeeModel):
        """Model addressed by xref from the conditional adoption key."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the conditional owner test model."""

            app_label = "base"

    class ConditionalCredential(AngeeModel):
        """Model whose local identity is unique only for provider-less rows."""

        user = models.ForeignKey(ConditionalOwner, on_delete=models.CASCADE)
        name = models.CharField(max_length=40, blank=True)
        oauth_client = models.CharField(max_length=40, blank=True, null=True)
        label = models.CharField(max_length=80, blank=True)

        class Meta:
            """Django model options for the conditional adoption test model."""

            app_label = "base"
            constraints = (
                models.UniqueConstraint(
                    fields=("user", "name"),
                    condition=models.Q(oauth_client__isnull=True) & ~models.Q(name=""),
                    name="uniq_resource_conditional_credential",
                ),
            )

    class ConditionalLedger(Resource):
        """Concrete resource ledger for conditional adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.conditionalowner.csv").write_text(
        "_xref,username\nadmin,admin\n",
        encoding="utf-8",
    )
    (resource_dir / "010_base.conditionalcredential.csv").write_text(
        "_xref,user,name,label\nstatic-key,resource_addon.admin,api-key,Seeded\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.conditionalcredential.csv").write_text(
        "_xref,user,name,oauth_client,label\nstatic-key-oauth,resource_addon.admin,api-key,client-a,OAuth row\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.conditionalowner.csv",
                    "adopt": "username",
                },
                {
                    "path": "resources/010_base.conditionalcredential.csv",
                    "depends_on": "resources/010_base.conditionalowner.csv",
                    "adopt": ["user", "name"],
                },
                {
                    "path": "resources/020_base.conditionalcredential.csv",
                    "depends_on": "resources/010_base.conditionalcredential.csv",
                    "adopt": ["user", "name"],
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (ConditionalOwner, ConditionalCredential, ConditionalLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        user = ConditionalOwner.objects.create(username="admin")
        existing = ConditionalCredential.objects.create(user=user, name="api-key", label="Existing")

        result = ConditionalLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 1
        assert result.updated == 2
        assert result.skipped == 0
        existing.refresh_from_db()
        assert existing.label == "Seeded"
        with system_context(reason="conditional adoption assertions"):
            assert ConditionalCredential.objects.count() == 2
            oauth_row = ConditionalCredential.objects.get(oauth_client="client-a")
        assert oauth_row.name == "api-key"
        assert oauth_row.label == "OAuth row"
        assert ConditionalLedger.objects.get(xref="static-key").target_id == existing.public_id
        assert ConditionalLedger.objects.get(xref="static-key-oauth").target_id == oauth_row.public_id

        second = ConditionalLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 3
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_rejects_ambiguous_unique_fields(
    tmp_path: Path,
) -> None:
    """Implicit adoption fails when a row has multiple unique candidates."""

    class AmbiguousAdoptUser(AngeeModel):
        """Model with multiple unique fields."""

        username = models.CharField(max_length=40, unique=True)
        email = models.EmailField(unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class AmbiguousAdoptLedger(Resource):
        """Concrete resource ledger for ambiguous adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.ambiguousadoptuser.csv").write_text(
        "_xref,username,email\nexisting,alice,alice@example.test\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.ambiguousadoptuser.csv",
                    "adopt": True,
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (AmbiguousAdoptUser, AmbiguousAdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        AmbiguousAdoptUser.objects.create(
            username="alice",
            email="alice@example.test",
        )

        with pytest.raises(ImproperlyConfigured, match="multiple unique"):
            AmbiguousAdoptLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.INSTALL],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


def test_resource_entry_normalizes_and_validates_grant_kind(tmp_path: Path) -> None:
    """A ``kind = "grants"`` entry normalizes; an unknown kind fails fast."""

    grant_entry = ResourceEntry.from_declaration(
        addon(tmp_path),
        "demo",
        {"path": "grants/010_demo.yaml", "kind": "grants"},
    )
    assert grant_entry.kind == "grants"

    default_entry = ResourceEntry.from_declaration(addon(tmp_path), "demo", {"path": "resources/010_base.thing.yaml"})
    assert default_entry.kind == "rows"

    with pytest.raises(ImproperlyConfigured, match="Unknown resource entry kind"):
        ResourceEntry.from_declaration(addon(tmp_path), "demo", {"path": "grants/010_demo.yaml", "kind": "roles"})


def test_grant_rows_reject_malformed_records(tmp_path: Path) -> None:
    """Grant rows require exactly resource/relation/subject and no model."""

    grants_dir = tmp_path / "grants"
    grants_dir.mkdir()
    manifest = {"master": (), "install": (), "demo": ({"path": "grants/010_demo.yaml", "kind": "grants"},)}

    (grants_dir / "010_demo.yaml").write_text(
        '- resource: "angee/role:admin"\n  relation: "member"\n',
        encoding="utf-8",
    )
    missing = ResourceEntry.from_declaration(addon(tmp_path, manifest=manifest), "demo", manifest["demo"][0])
    with pytest.raises(ResourceLoadError, match="missing subject"):
        missing.read_grant_rows()

    (grants_dir / "010_demo.yaml").write_text(
        '- resource: "angee/role:admin"\n  relation: "member"\n  subject: "iam.alice"\n  caveat: "x"\n',
        encoding="utf-8",
    )
    unknown = ResourceEntry.from_declaration(addon(tmp_path, manifest=manifest), "demo", manifest["demo"][0])
    with pytest.raises(ResourceLoadError, match="unknown field.*caveat"):
        unknown.read_grant_rows()

    (grants_dir / "010_demo.yaml").write_text(
        "_meta:\n  model: base.Thing\nrows:\n"
        '  - resource: "angee/role:admin"\n    relation: "member"\n    subject: "iam.alice"\n',
        encoding="utf-8",
    )
    with_model = ResourceEntry.from_declaration(addon(tmp_path, manifest=manifest), "demo", manifest["demo"][0])
    with pytest.raises(ResourceLoadError, match="grants entry declares no model"):
        with_model.read_grant_rows()


@pytest.mark.django_db(transaction=True)
def test_grant_tuple_resolves_xref_const_role_and_wildcard(tmp_path: Path) -> None:
    """A grant row resolves a row xref, a const-id literal, a role subject-set, and the wildcard."""

    from rebac import ObjectRef, SubjectRef, anonymous_actor, to_object_ref

    class GrantDoc(AngeeModel):
        """Row addressed by xref from the resource side of a grant."""

        title = models.CharField(max_length=40)

        class Meta:
            """Django model options for the grant-resource test model."""

            app_label = "base"
            rebac_resource_type = "base/grant-doc"

    class GrantResolverLedger(Resource):
        """Concrete resource ledger for grant-resolution tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    models_to_create = (GrantDoc, GrantResolverLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    aliases = {"tests.resource_addon": "tests.resource_addon", "resource_addon": "tests.resource_addon"}
    try:
        with system_context(reason="grant resolver setup"):
            doc = GrantDoc.objects.create(title="Doc")
        GrantResolverLedger.objects.create(
            source_addon="tests.resource_addon",
            source_path="grants/010_demo.yaml",
            tier=Resource.Tier.DEMO,
            xref="doc",
            content_hash="sha256:x",
            target_model=GrantDoc._meta.label,
            target_id=doc.public_id,
        )
        entry = ResourceEntry.from_declaration(
            addon(tmp_path), "demo", {"path": "grants/010_demo.yaml", "kind": "grants"}
        )

        def _row(resource: str, relation: str, subject: str) -> GrantRow:
            return GrantRow(entry=entry, resource=resource, relation=relation, subject=subject, index=1)

        # Grants resolve under the loader's elevated context; mirror it here. A
        # plain (non-MTI) resource and a literal ref each name exactly one tuple.
        with system_context(reason="grant resolver assertions"):
            (row_xref,) = _grant_tuples(_row("resource_addon.doc", "direct_member", "*"), GrantResolverLedger, aliases)
            assert row_xref.resource == to_object_ref(doc)
            assert row_xref.relation == "direct_member"
            assert row_xref.subject == anonymous_actor()

            (literal,) = _grant_tuples(
                _row("angee/role:admin", "member", "products/role:mgr#member"), GrantResolverLedger, aliases
            )
            assert literal.resource == ObjectRef("angee/role", "admin")
            assert literal.subject == SubjectRef(ObjectRef("products/role", "mgr"), "member")
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_grant_fixtures_load_and_are_idempotent(tmp_path: Path, monkeypatch: Any) -> None:
    """A grants-tier fixture materializes REBAC tuples idempotently through the loader.

    An unchanged re-load is a *true* no-op: only missing tuples are written, so the
    second load calls ``write_relationships`` with nothing to write — no audit entry
    or zookie bump for grants that already exist.
    """

    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from rebac import to_subject_ref
    from rebac.models import active_relationship_model

    from angee.resources import grants as grants_module

    written: list[int] = []
    real_write = grants_module.write_relationships

    def _spy_write(tuples: Any) -> Any:
        rows = list(tuples)
        written.append(len(rows))
        return real_write(rows)

    monkeypatch.setattr(grants_module, "write_relationships", _spy_write)

    class GrantLoadLedger(Resource):
        """Concrete resource ledger for grant-load tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    user_model = get_user_model()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(GrantLoadLedger)
    # Grants are validated against the composed REBAC schema on write.
    call_command("rebac", "sync", verbosity=0)
    try:
        with system_context(reason="grant load setup"):
            alice = user_model.objects.create(username="grant-alice")
        GrantLoadLedger.objects.create(
            source_addon="tests.resource_addon",
            source_path="grants/010_demo.yaml",
            tier=Resource.Tier.DEMO,
            xref="alice",
            content_hash="sha256:x",
            target_model=user_model._meta.label,
            target_id=alice.public_id,
        )
        grants_dir = tmp_path / "grants"
        grants_dir.mkdir()
        (grants_dir / "010_demo.yaml").write_text(
            '- resource: "angee/role:admin"\n'
            '  relation: "member"\n'
            '  subject: "resource_addon.alice"\n'
            '- resource: "storage/drive:demo-drive"\n'
            '  relation: "editor"\n'
            '  subject: "auth/user:*"\n',
            encoding="utf-8",
        )
        owner = addon(
            tmp_path,
            manifest={
                "master": (),
                "install": (),
                "demo": ({"path": "grants/010_demo.yaml", "kind": "grants"},),
            },
        )

        result = GrantLoadLedger.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)

        assert result.created == 2
        assert result.skipped == 0
        # The first load writes exactly the two missing tuples.
        assert written == [2]
        relationship_model = active_relationship_model()
        alice_subject = to_subject_ref(alice)
        with system_context(reason="grant load assertions"):
            assert relationship_model._default_manager.filter(
                resource_type="angee/role",
                resource_id="admin",
                relation="member",
                subject_type=alice_subject.subject_type,
                subject_id=alice_subject.subject_id,
            ).exists()
            assert relationship_model._default_manager.filter(
                resource_type="storage/drive",
                resource_id="demo-drive",
                relation="editor",
                subject_type="auth/user",
                subject_id="*",
            ).exists()

        second = GrantLoadLedger.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
        assert second.created == 0
        assert second.skipped == 2
        # The unchanged re-load wrote nothing new — a true no-op, no churn.
        assert written == [2]
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(GrantLoadLedger)


@pytest.mark.django_db(transaction=True)
def test_grant_on_mti_child_lands_on_every_identity(tmp_path: Path) -> None:
    """A grant on an MTI child row lands on every REBAC identity it IS-A.

    ``MtiChild`` IS-A ``MtiParent`` (shared primary key), so a ``reader`` grant on
    the child materializes a tuple on both the child type and each REBAC-registered
    parent type — and a ``read`` passes whether the row is reached as the child or
    through a foreign key typed to the parent (the edge the parent-typed FK scopes).
    A plain (non-MTI) grant still writes exactly one tuple.
    """

    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from rebac import ObjectRef, to_object_ref, to_subject_ref
    from rebac.backends import backend
    from rebac.models import active_relationship_model
    from rebac.resources import model_resource_type

    from tests.mtidemo.models import MtiChild, MtiParent

    class MtiGrantLedger(Resource):
        """Concrete resource ledger for the MTI grant test."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    aliases = {"tests.resource_addon": "tests.resource_addon", "resource_addon": "tests.resource_addon"}
    user_model = get_user_model()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(MtiGrantLedger)
    # Grants are checked against the composed REBAC schema on read; sync loads it.
    call_command("rebac", "sync", verbosity=0)
    try:
        with system_context(reason="mti grant setup"):
            child = MtiChild.objects.create(title="Acme", detail="org")
            plain = MtiParent.objects.create(title="Plain")
            reader = user_model.objects.create(username="mti-reader")
            outsider = user_model.objects.create(username="mti-outsider")
        for row, xref in ((child, "child"), (plain, "plain"), (reader, "reader")):
            MtiGrantLedger.objects.create(
                source_addon="tests.resource_addon",
                source_path="grants/010_demo.yaml",
                tier=Resource.Tier.DEMO,
                xref=xref,
                content_hash="sha256:x",
                target_model=type(row)._meta.label,
                target_id=row.public_id,
            )
        entry = ResourceEntry.from_declaration(
            addon(tmp_path), "demo", {"path": "grants/010_demo.yaml", "kind": "grants"}
        )
        def _grant_row(xref: str, index: int) -> GrantRow:
            return GrantRow(
                entry=entry,
                resource=f"resource_addon.{xref}",
                relation="reader",
                subject="resource_addon.reader",
                index=index,
            )

        rows = (_grant_row("child", 1), _grant_row("plain", 2))
        with system_context(reason="mti grant materialize"):
            created, skipped = materialize_grant_groups(
                (GrantGroup(entry=entry, rows=rows),),
                ledger_model=MtiGrantLedger,
                addon_aliases=aliases,
            )

        # The child grant expands to two identities (child + parent); the plain grant
        # to one — three tuples materialize for the two grant rows.
        assert (created, skipped) == (3, 0)

        relationships = active_relationship_model()
        subject = to_subject_ref(reader)
        child_as_child = to_object_ref(child)
        parent_type = model_resource_type(MtiParent)
        assert parent_type is not None
        child_as_parent = ObjectRef(parent_type, child_as_child.resource_id)
        plain_ref = to_object_ref(plain)

        def _reader_tuple(resource: ObjectRef) -> bool:
            return relationships._default_manager.filter(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                relation="reader",
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
            ).exists()

        with system_context(reason="mti grant tuple assertions"):
            # The child grant lands on both identities the child row carries.
            assert _reader_tuple(child_as_child)
            assert _reader_tuple(child_as_parent)
            # The plain (non-MTI) grant writes exactly its one identity — no change.
            assert _reader_tuple(plain_ref)
            assert (
                relationships._default_manager.filter(resource_id=plain_ref.resource_id, relation="reader").count() == 1
            )

        # The subject reads the child row both as the child and through the parent
        # type a foreign key would scope on; the plain row reads through its type.
        for resource in (child_as_child, child_as_parent, plain_ref):
            assert backend().has_access(subject=subject, action="read", resource=resource)
        # No grant, no parent-typed read — the materialized tuple is what opens the edge.
        assert not backend().has_access(
            subject=to_subject_ref(outsider), action="read", resource=child_as_parent
        )
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(MtiGrantLedger)


def _write_resource_files(tmp_path: Path) -> Addon:
    """Write a small resource set and return its declaring addon."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.importuser.csv").write_text(
        "_xref,username,first_name\nuser_alice,alice,Alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.importnote.yaml").write_text(
        "- _xref: note_framework_map\n"
        "  title: Framework map\n"
        "  created_by: resource_addon.user_alice\n"
        "  tags:\n"
        "    - composition\n"
        "    - resources\n",
        encoding="utf-8",
    )
    return addon(
        tmp_path,
        manifest={
            "master": (
                {"path": "resources/010_base.importuser.csv"},
                {"path": "resources/020_base.importnote.yaml"},
            ),
            "install": (),
            "demo": (),
        },
    )
