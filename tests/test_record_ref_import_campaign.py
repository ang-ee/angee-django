"""Record-reference columns participate in the real resource import lifecycle."""

from pathlib import Path

import pytest
import tablib
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from import_export.results import RowResult
from rebac import system_context

from angee.base.refs import RecordRefMixin
from angee.integrate.models import RecordLink
from angee.money.models import CurrencyRate
from angee.resources.entries import ResourceEntry
from angee.resources.loader import build_resource
from angee.resources.models import Resource
from angee.workflows.models import WorkflowRun
from tests.conftest import Page, RecordBinding, Vault, make_addon
from tests.mtidemo.models import MtiChild, MtiParent
from tests.tables import model_tables
from tests.test_record_refs import (
    RecordRefNullableEdge,
    RecordRefSubjectEdge,
    RecordRefTargetEdge,
)
from tests.test_record_refs import (
    record_ref_tables as record_ref_tables,
)
from tests.test_workflows_resources import WorkflowResourceLedger


class CustomColumnEdge(RecordRefMixin, models.Model):
    """An alternate column adopter also requires native string-id conversion."""

    target_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    target_id = models.CharField(max_length=64, null=True, blank=True)
    target = GenericForeignKey("target_ct", "target_id")

    class Meta:
        app_label = "auth"
        db_table = "test_record_ref_custom_columns"


@pytest.fixture
def import_reference(composed_tables, record_ref_tables, tmp_path):
    """Bind native resources to the existing suite ledger and model-table owner."""

    addon = make_addon(name="tests.reference_import", label="reference_import", path=tmp_path)

    def resource(model):
        return build_resource(
            model,
            ResourceEntry(
                addon=addon,
                tier=Resource.Tier.INSTALL,
                source_value=f"{model._meta.label_lower}.yaml",
                model=model._meta.label,
            ),
            ledger_model=WorkflowResourceLedger,
            addon_aliases={"reference_import": addon.name},
        )

    with model_tables((CustomColumnEdge,)):
        yield resource


def _import(resource, headers, *rows, **kwargs):
    with system_context(reason="test.record_reference.import"):
        return resource.import_data(tablib.Dataset(*rows, headers=headers), **kwargs)


def _seed_target(import_reference):
    result = _import(
        import_reference(MtiChild), ["_xref", "title", "detail"], ["child", "Reference target", "Child body"],
        raise_errors=True,
    )
    return result.rows[0].instance


@pytest.mark.parametrize(
    "model,columns",
    [
        (RecordBinding, ("content_type", "object_id")),
        (RecordRefTargetEdge, ("content_type", "object_id")),
        (RecordRefNullableEdge, ("content_type", "object_id")),
        (RecordRefSubjectEdge, ("subject_content_type", "subject_object_id")),
        (WorkflowRun, ("subject_content_type", "subject_object_id")),
        (CurrencyRate, ("context_content_type", "context_object_id")),
        (RecordLink, ("target_ct", "target_id")),
        (CustomColumnEdge, ("target_ct", "target_id")),
    ],
)
def test_record_ref_field_describes_each_adopter_shape(model, columns):
    reference = model.record_ref_field()
    assert (reference.ct_field, reference.fk_field) == columns
    for name in columns:
        assert model._meta.get_field(name).name == name


@pytest.mark.parametrize("model", [RecordRefTargetEdge, RecordRefSubjectEdge, CustomColumnEdge])
def test_import_resolves_child_xref_to_canonical_parent_and_reimport_keeps_identity(import_reference, model):
    child = _seed_target(import_reference)
    prefix = model.record_ref_field().name
    first = _import(import_reference(model), ["_xref", prefix], ["edge", "reference_import.child"], raise_errors=True)
    edge = first.rows[0].instance
    reference = model.record_ref_field()
    ct_name, id_name = reference.ct_field, reference.fk_field
    assert getattr(edge, ct_name) == ContentType.objects.get_for_model(MtiParent)
    assert getattr(edge, id_name) == (str(child.pk) if model is CustomColumnEdge else child.pk)
    assert edge.record_model_label == MtiParent._meta.label

    replay = _import(import_reference(model), ["_xref", prefix], ["edge", "reference_import.child"], raise_errors=True)
    assert replay.rows[0].import_type == RowResult.IMPORT_TYPE_SKIP
    assert model.objects.get().pk == edge.pk
    assert WorkflowResourceLedger.objects.filter(xref="edge").count() == 1


def test_unresolved_xref_reports_the_failing_row_and_does_not_persist_it(import_reference):
    _seed_target(import_reference)
    result = _import(
        import_reference(RecordRefTargetEdge), ["_xref", "target"],
        ["valid", "reference_import.child"], ["invalid", "reference_import.missing"],
    )
    assert not result.has_errors()
    assert len(result.invalid_rows) == 1
    invalid = result.invalid_rows[0]
    assert invalid.number == 2
    assert "unresolved xref 'reference_import.missing'" in str(invalid.error)
    assert not WorkflowResourceLedger.objects.filter(xref="invalid").exists()


@pytest.mark.parametrize("model,backing", [
    (RecordRefTargetEdge, "content_type"), (RecordRefTargetEdge, "object_id"),
    (RecordRefSubjectEdge, "subject_content_type"), (RecordRefSubjectEdge, "subject_object_id"),
    (CustomColumnEdge, "target_ct"), (CustomColumnEdge, "target_id"),
])
def test_prefix_and_backing_column_collision_is_rejected_before_xref_resolution(import_reference, model, backing):
    prefix = model.record_ref_field().name
    result = _import(
        import_reference(model), ["_xref", prefix, backing],
        ["collision", "reference_import.missing", ""],
    )
    assert result.has_validation_errors()
    assert f"{prefix} cannot be combined with its backing columns" in str(result.invalid_rows[0].error)
    assert "unresolved xref" not in str(result.invalid_rows[0].error)
    assert not model.objects.exists()
    assert not WorkflowResourceLedger.objects.filter(xref="collision").exists()


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_nullable_reference_clears_both_columns_on_reimport(import_reference, empty):
    _seed_target(import_reference)
    first = _import(
        import_reference(RecordRefNullableEdge), ["_xref", "target"],
        ["edge", "reference_import.child"], raise_errors=True,
    )
    result = _import(
        import_reference(RecordRefNullableEdge), ["_xref", "target"], ["edge", empty], raise_errors=True,
    )
    edge = result.rows[0].instance
    assert edge.pk == first.rows[0].instance.pk
    assert edge.content_type_id is None
    assert edge.object_id is None
    assert RecordRefNullableEdge.objects.count() == 1


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_required_reference_is_a_named_field_validation_error(import_reference, empty):
    result = _import(import_reference(RecordRefTargetEdge), ["_xref", "target"], ["edge", empty])
    assert not result.has_errors(), result.row_errors()
    assert result.has_validation_errors()
    assert result.invalid_rows[0].number == 1
    assert "content_type" in result.invalid_rows[0].error.message_dict
    assert not RecordRefTargetEdge.objects.exists()
    assert not WorkflowResourceLedger.objects.filter(xref="edge").exists()


def test_dry_run_resolves_references_without_leaving_rows_or_receipts(import_reference):
    _seed_target(import_reference)
    result = _import(
        import_reference(RecordRefTargetEdge), ["_xref", "target"], ["edge", "reference_import.child"],
        dry_run=True, raise_errors=True,
    )
    assert result.rows[0].import_type == RowResult.IMPORT_TYPE_NEW
    assert not RecordRefTargetEdge.objects.exists()
    assert not WorkflowResourceLedger.objects.filter(xref="edge").exists()


def test_resource_file_loads_ownerless_vault_and_both_binding_kinds(composed_tables, tmp_path: Path):
    addon = make_addon(
        name="tests.knowledge_seed", label="knowledge_seed", path=tmp_path,
        resources={"install": [
            "010_knowledge.vault.yaml", "020_knowledge.page.yaml", "030_mtidemo.mtiparent.yaml",
            "040_knowledge.recordbinding.yaml",
        ]},
    )
    (tmp_path / "010_knowledge.vault.yaml").write_text("- _xref: vault\n  name: Seeded vault\n")
    (tmp_path / "020_knowledge.page.yaml").write_text(
        "- _xref: page\n  vault: knowledge_seed.vault\n  title: Seeded page\n"
    )
    (tmp_path / "030_mtidemo.mtiparent.yaml").write_text("- _xref: record\n  title: Seeded record\n")
    (tmp_path / "040_knowledge.recordbinding.yaml").write_text(
        "- _xref: page_binding\n  page: knowledge_seed.page\n  target: knowledge_seed.record\n"
        "- _xref: vault_binding\n  vault: knowledge_seed.vault\n  target: knowledge_seed.record\n"
    )
    result = WorkflowResourceLedger.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert result.created == 5
    with system_context(reason="test.seed_readback_without_record_owner_arm"):
        vault = Vault.objects.get(name="Seeded vault")
        page = Page.objects.get(vault=vault)
        record = MtiParent.objects.get(title="Seeded record")
        assert vault.owner_id is None
        bindings = list(RecordBinding.objects.for_record(record))
        assert {(row.page_id, row.vault_id) for row in bindings} == {(page.pk, None), (None, vault.pk)}
        assert all(row.content_type_id == ContentType.objects.get_for_model(MtiParent).pk for row in bindings)
    replay = WorkflowResourceLedger.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert (replay.created, replay.updated, replay.skipped) == (0, 0, 5)
