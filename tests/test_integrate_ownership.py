"""Ownership guards and explicit projection imports share immutable source facts."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models.signals import pre_delete
from rebac import system_context

from angee.base.models import AngeeModel
from angee.integrate.ownership import (
    ExternalOwnershipContribution,
    ExternalOwnershipDeclaration,
    ExternalOwnershipError,
    ExternalOwnershipMixin,
    check_external_ownership_declarations,
    check_external_ownership_delete,
)
from tests.conftest import _create_missing_tables


class OwnershipCompany(models.Model):
    """Unowned scope whose cascade must retain externally owned children."""

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_ownership_company"


class OwnedProjection(ExternalOwnershipMixin, AngeeModel):
    """A remote name and a locally editable annotation."""

    name = models.CharField(max_length=64)
    note = models.CharField(max_length=64, blank=True)
    company = models.ForeignKey(OwnershipCompany, null=True, on_delete=models.CASCADE)
    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"name", "company"},
        local_fields={"note"},
        company_field="company",
    )

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_owned_projection"


class OwnedRelationship(ExternalOwnershipMixin, AngeeModel):
    """A relation may only point into the same external source and scope."""

    target = models.ForeignKey(OwnedProjection, on_delete=models.CASCADE)
    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"target"},
        protected_relations={"target"},
        company_field="target__company",
    )

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_owned_relationship"


@pytest.fixture
def ownership_tables() -> Iterator[None]:
    """Register the same sender-scoped deletion guards as AppConfig.ready()."""

    created = _create_missing_tables((OwnershipCompany, OwnedProjection, OwnedRelationship))
    for model in (OwnedProjection, OwnedRelationship):
        pre_delete.connect(check_external_ownership_delete, sender=model)
    try:
        with system_context(reason="test.integrate.ownership"):
            yield
    finally:
        for model in (OwnedProjection, OwnedRelationship):
            pre_delete.disconnect(check_external_ownership_delete, sender=model)
        with connection.schema_editor() as editor:
            for model in reversed(created):
                editor.delete_model(model)


def projection(**kwargs: object) -> OwnedProjection:
    """Build one fixture with the complete source identity attached at insert."""

    return OwnedProjection.objects.create(
        name="remote name",
        external_source_type="directory",
        external_source_id="source-1",
        external_source_key="record-1",
        **kwargs,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry", ["save", "update", "bulk_update", "validated_bulk_update"])
def test_local_source_field_edits_are_refused(ownership_tables: None, entry: str) -> None:
    row = projection()
    row.name = "local rewrite"
    with pytest.raises(ExternalOwnershipError, match="apply_external"):
        if entry == "save":
            row.save(update_fields=["name"])
        elif entry == "update":
            OwnedProjection.objects.filter(pk=row.pk).update(name=row.name)
        else:
            getattr(OwnedProjection.objects, entry)([row], ["name"])
    row.refresh_from_db()
    assert row.name == "remote name"


@pytest.mark.django_db(transaction=True)
def test_import_command_preserves_local_overlay_and_source_identity(ownership_tables: None) -> None:
    row = projection()
    row.note = "local review"
    row.save(update_fields=["note"])
    actual = OwnedProjection.objects.apply_external(
        row.pk,
        {"name": "updated remote name"},
        source=("directory", "source-1"),
        source_key="record-1",
        company=None,
        using="default",
    )
    assert actual.name == "updated remote name"
    row.refresh_from_db()
    assert (row.name, row.note) == ("updated remote name", "local review")
    assert row.import_source() == ("directory", "source-1")
    with pytest.raises(ExternalOwnershipError, match="different source"):
        OwnedProjection.objects.apply_external(
            row.pk,
            {"name": "wrong peer"},
            source=("directory", "source-2"),
            source_key="record-1",
            company=None,
        )
    with pytest.raises(ExternalOwnershipError, match="declared source fields"):
        OwnedProjection.objects.apply_external(
            row.pk,
            {"note": "remote overwrite"},
            source=("directory", "source-1"),
            source_key="record-1",
            company=None,
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("field", ["external_source_type", "external_source_id", "external_source_key"])
def test_provenance_is_immutable_on_model_and_bulk_paths(ownership_tables: None, field: str) -> None:
    row = projection()
    setattr(row, field, "another")
    with pytest.raises(ExternalOwnershipError, match="immutable"):
        row.save(update_fields=[field])
    with pytest.raises(ExternalOwnershipError, match="immutable"):
        OwnedProjection.objects.filter(pk=row.pk).update(**{field: "another"})
    with pytest.raises(ExternalOwnershipError, match="immutable"):
        OwnedProjection.objects.validated_bulk_update([row], [field])


@pytest.mark.django_db(transaction=True)
def test_company_is_immutable_even_during_an_explicit_import(ownership_tables: None) -> None:
    company = OwnershipCompany.objects.create()
    other = OwnershipCompany.objects.create()
    row = projection(company=company)
    with pytest.raises(ExternalOwnershipError, match="company is immutable"):
        OwnedProjection.objects.apply_external(
            row.pk,
            {"company_id": other.pk},
            source=("directory", "source-1"),
            source_key="record-1",
            company=("integrate.ownershipcompany", str(company.pk)),
        )
    row.refresh_from_db()
    assert row.company_id == company.pk


@pytest.mark.django_db(transaction=True)
def test_claim_is_once_only_and_cascades_retain_source_rows(ownership_tables: None) -> None:
    company = OwnershipCompany.objects.create()
    row = OwnedProjection.objects.create(name="local", company=company)
    row.claim_external_ownership("record-1", source=("directory", "source-1"))
    with pytest.raises(ExternalOwnershipError, match="immutable"):
        row.claim_external_ownership("record-2", source=("directory", "source-1"))
    with pytest.raises(ExternalOwnershipError, match="cannot be deleted"):
        company.delete()
    assert OwnedProjection.objects.filter(pk=row.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_partial_identity_and_foreign_source_relationship_are_refused(ownership_tables: None) -> None:
    with pytest.raises(ExternalOwnershipError, match="complete"):
        OwnedProjection.objects.create(name="incomplete", external_source_type="directory")
    row = projection()
    with pytest.raises(ExternalOwnershipError, match="same source"):
        OwnedRelationship.objects.create(
            target=row,
            external_source_type="directory",
            external_source_id="source-2",
            external_source_key="edge-1",
        )


@pytest.mark.django_db(transaction=True)
def test_deferred_reads_and_relation_reload_use_the_operation_alias(ownership_tables: None) -> None:
    company = OwnershipCompany.objects.create()
    row = projection(company=company)
    deferred = OwnedProjection.objects.only("note").get(pk=row.pk)
    deferred.note = "local review"
    with patch("django.db.router.db_for_read", side_effect=AssertionError("implicit read route")):
        deferred.save(update_fields=["note"], using="default")
        deferred.require_external_identity(
            source=("directory", "source-1"),
            source_key="record-1",
            company=("integrate.ownershipcompany", str(company.pk)),
            using="default",
        )


@pytest.mark.django_db(transaction=True)
def test_nondefault_import_fails_before_alias_unsafe_authorization(ownership_tables: None) -> None:
    row = projection()
    with pytest.raises(ValidationError, match="default authorization database"):
        OwnedProjection.objects.apply_external(
            row.pk,
            {"name": "updated"},
            source=("directory", "source-1"),
            source_key="record-1",
            company=None,
            using="unsupported_alias",
        )


def test_static_contributions_keep_one_policy_and_reject_collisions() -> None:
    class LocalOverlay:
        external_ownership = ExternalOwnershipContribution(local_fields={"review"})

    class Composed(LocalOverlay, OwnedProjection):
        class Meta:
            abstract = True
            app_label = "integrate"

    declaration = ExternalOwnershipDeclaration.for_model(Composed)
    assert declaration.local_fields == {"note", "review"}
    with pytest.raises(ValueError, match="both source-owned and local"):
        ExternalOwnershipDeclaration(source_owned_fields={"name"}, local_fields={"name"})


def test_source_owned_many_to_many_requires_an_owned_through_model() -> None:
    class UnsupportedProjection(OwnedProjection):
        tags = models.ManyToManyField(OwnershipCompany)
        external_ownership = ExternalOwnershipDeclaration(
            source_owned_fields={"name", "company", "tags"},
            local_fields={"note"},
        )

        class Meta:
            abstract = True
            app_label = "integrate"

    with patch("angee.integrate.ownership.apps.get_models", return_value=[UnsupportedProjection]):
        errors = check_external_ownership_declarations()
    assert len(errors) == 1
    assert "through model" in errors[0].msg


def test_conflict_bulk_insert_refuses_native_positional_update_flag() -> None:
    with pytest.raises(ExternalOwnershipError, match="conflict insertion"):
        OwnedProjection.objects.bulk_create([], None, False, True, ["name"], ["id"])
