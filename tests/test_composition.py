"""Tests for Angee model composition primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import reversion
from django.apps import apps
from django.db import connection, models
from rebac import MissingActorError, RebacMixin, SubjectRef, system_context

from angee.base.identity import (
    canonical_subject_ref,
    instance_from_public_id,
    public_data_id_field,
    public_id_for,
    public_id_of,
    public_subject_ref,
)
from angee.base.mixins import RevisionMixin
from angee.base.models import (
    AngeeDataModel,
    AngeeModel,
)


class PublicIdThing(AngeeDataModel):
    """Concrete test model with an Angee public identifier."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        rebac_resource_type = "tests/public-id-thing"


class PlainPublicIdThing(models.Model):
    """Concrete test model that does not use AngeeModel."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class RevisionThing(RevisionMixin, models.Model):
    """Concrete test model tracked through django-reversion."""

    revisioned_fields = ("body",)

    title = models.CharField(max_length=32)
    body = models.TextField()

    class Meta:
        """Django model options for the test model."""

        app_label = "auth"


def test_every_angee_model_carries_the_rebac_mixin() -> None:
    """AngeeModel wires REBAC behavior into every source model."""

    assert issubclass(AngeeModel, RebacMixin)


def test_concrete_angee_models_keep_the_manager_queryset_canon() -> None:
    """Concrete Angee models expose the inherited public-id and aggregate scopes."""

    missing: dict[str, list[str]] = {}
    for model in apps.get_models():
        if model._meta.abstract or not issubclass(model, AngeeModel):
            continue
        queryset = model._default_manager.all()
        missing_methods = [
            method_name
            for method_name in ("from_public_id", "scoped_for_aggregate")
            if not callable(getattr(queryset, method_name, None))
        ]
        if missing_methods:
            missing[model._meta.label] = missing_methods

    assert missing == {}


def test_angee_data_model_carries_the_public_data_identity_contract() -> None:
    """AngeeDataModel is the canonical base for sqid-backed data rows."""

    assert issubclass(AngeeDataModel, AngeeModel)
    field = public_data_id_field(PublicIdThing)
    assert field is not None
    assert field.name == "sqid"


def test_system_check_enforces_pk_rebac_identity_only_for_table_backed_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed model identities are PKs while synthetic anchors retain named ids."""

    monkeypatch.setattr(PublicIdThing._meta, "rebac_id_attr", "sqid")
    assert [error.id for error in PublicIdThing._check_rebac_pk_identity()] == ["angee.E018"]
    monkeypatch.setattr(PublicIdThing._meta, "pk", SimpleNamespace(name="parent", attname="parent_id"))
    monkeypatch.setattr(PublicIdThing._meta, "rebac_id_attr", "parent")
    assert [error.id for error in PublicIdThing._check_rebac_pk_identity()] == ["angee.E018"]
    monkeypatch.setattr(PublicIdThing._meta, "rebac_id_attr", "parent_id")
    assert PublicIdThing._check_rebac_pk_identity() == []
    monkeypatch.setattr(PublicIdThing._meta, "managed", False)
    assert PublicIdThing._check_rebac_pk_identity() == []


def test_legacy_rebac_lookup_delegates_to_the_public_identity_owner() -> None:
    """The one-shot upgrade hook decodes old sqids through the model field."""

    public_id = PublicIdThing.public_id_from_pk(42)
    assert PublicIdThing.legacy_rebac_id_lookup(public_id) == {"sqid": public_id}


def test_subject_identity_converts_only_at_the_public_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model subjects store PKs while transport subjects retain opaque public ids."""

    canonical = SubjectRef.of("tests/public-id-thing", "42")
    public = public_subject_ref(canonical)

    assert public.subject_id == PublicIdThing.public_id_from_pk(42)
    assert canonical_subject_ref(str(public)) == canonical

    # The declared field remains the owner without Angee's convenience method.
    monkeypatch.setattr(PublicIdThing, "public_id_from_pk", None)
    assert public_subject_ref(canonical) == public

    with pytest.raises(ValueError, match="invalid public id"):
        canonical_subject_ref("tests/public-id-thing:not-a-public-id")

    monkeypatch.setattr(PublicIdThing._meta, "managed", False)
    assert canonical_subject_ref(str(public)) == public
    assert public_subject_ref(canonical) == canonical


@pytest.mark.django_db(transaction=True)
def test_public_id_helpers_support_angee_and_plain_django_models() -> None:
    """ID helpers use Angee public IDs for Angee models and PKs otherwise."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PublicIdThing)
        schema_editor.create_model(PlainPublicIdThing)

    try:
        with system_context(reason="test public-id setup"):
            angee_instance = PublicIdThing.objects.create(name="angee")
        plain_instance = PlainPublicIdThing.objects.create(name="plain")

        assert public_id_of(angee_instance) == angee_instance.sqid
        assert public_id_of(plain_instance) == str(plain_instance.pk)
        assert public_id_for(PublicIdThing, angee_instance.pk) == angee_instance.sqid
        assert public_id_for(PlainPublicIdThing, plain_instance.pk) == str(plain_instance.pk)
        assert public_id_for(PublicIdThing, None) == ""
        with system_context(reason="test public-id lookup"):
            assert instance_from_public_id(PublicIdThing, angee_instance.public_id) == angee_instance
            assert PublicIdThing.from_public_id(angee_instance.public_id) == angee_instance
            assert PublicIdThing.objects.from_public_id(angee_instance.public_id) == angee_instance
            assert (
                instance_from_public_id(
                    PublicIdThing,
                    angee_instance.public_id,
                    queryset=PublicIdThing.objects.none(),
                )
                is None
            )
        assert instance_from_public_id(PlainPublicIdThing, str(plain_instance.pk)) == plain_instance
        with system_context(reason="test missing public-id lookup"):
            assert instance_from_public_id(PublicIdThing, "missing") is None
        with pytest.raises(MissingActorError):
            PublicIdThing.from_public_id(angee_instance.public_id)
        assert instance_from_public_id(PlainPublicIdThing, "0") is None
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PlainPublicIdThing)
            schema_editor.delete_model(PublicIdThing)


@pytest.mark.django_db(transaction=True)
def test_revision_mixin_restores_declared_fields_from_versions() -> None:
    """Revision helpers expose newest-first versions and restore fields."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RevisionThing)

    reversion.register(RevisionThing, fields=RevisionThing.revisioned_fields)

    try:
        instance = RevisionThing.objects.create(title="Draft", body="v0")
        with reversion.create_revision():
            instance.body = "v1"
            instance.save()
        with reversion.create_revision():
            instance.title = "Final"
            instance.body = "v2"
            instance.save()

        assert instance.revisions.count() == 2
        assert instance.revisions.first().field_dict["body"] == "v2"
        instance.title = "Unsaved stale title"
        instance.revert_to(instance.revisions.last())
        instance.refresh_from_db()

        assert instance.title == "Final"
        assert instance.body == "v1"
        assert instance.revisions.count() == 3
        assert instance.revisions.first().revision.comment.startswith("Reverted to revision ")
    finally:
        reversion.unregister(RevisionThing)
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RevisionThing)
