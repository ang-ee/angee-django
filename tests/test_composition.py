"""Tests for Angee model composition primitives."""

from __future__ import annotations

import pytest
from django.db import connection, models
from rebac import RebacMixin, system_context

from angee.base.mixins import SqidMixin
from angee.base.models import (
    AngeeModel,
    instance_from_public_id,
    public_id_of,
)


class PublicIdThing(SqidMixin, AngeeModel):
    """Concrete test model with an Angee public identifier."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class PlainPublicIdThing(models.Model):
    """Concrete test model that does not use AngeeModel."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


def test_every_angee_model_carries_the_rebac_mixin() -> None:
    """AngeeModel wires REBAC behavior into every source model."""

    assert issubclass(AngeeModel, RebacMixin)


@pytest.mark.django_db(transaction=True)
def test_public_id_helpers_support_angee_and_plain_django_models() -> None:
    """ID helpers use Angee public IDs for Angee models and PKs otherwise."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PublicIdThing)
        schema_editor.create_model(PlainPublicIdThing)

    try:
        angee_instance = PublicIdThing.objects.create(name="angee")
        plain_instance = PlainPublicIdThing.objects.create(name="plain")

        assert public_id_of(angee_instance) == angee_instance.sqid
        assert public_id_of(plain_instance) == str(plain_instance.pk)
        with system_context(reason="test public-id lookup"):
            assert (
                instance_from_public_id(
                    PublicIdThing, angee_instance.public_id
                )
                == angee_instance
            )
            assert (
                PublicIdThing.from_public_id(angee_instance.public_id)
                == angee_instance
            )
        assert (
            instance_from_public_id(PlainPublicIdThing, str(plain_instance.pk))
            == plain_instance
        )
        with system_context(reason="test missing public-id lookup"):
            assert instance_from_public_id(PublicIdThing, "missing") is None
        assert instance_from_public_id(PlainPublicIdThing, "0") is None
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PlainPublicIdThing)
            schema_editor.delete_model(PublicIdThing)
