"""The foundation works as ordinary Django without a permission provider.

Run independently with --noconftest --ds=tests.permissionless_settings: the
framework's full-host conftest intentionally selects optional capabilities.
"""

from __future__ import annotations

import sys

import pytest
from django.conf import settings

if settings.SETTINGS_MODULE != "tests.permissionless_settings":
    pytest.skip("Run with its documented minimal source-fixture settings.", allow_module_level=True)
from django.apps import apps
from django.db import models

from angee.base.models.base import AngeeModel
from angee.base.querysets import AngeeQuerySet
from angee.base.refs import generic_pointer_target
from tests.permissionless_app.models import Entry, PlainEntry, Reference


def test_base_imports_and_app_lifecycle_load_no_iam_or_rebac():
    assert not apps.is_installed("angee.iam")
    assert not apps.is_installed("django.contrib.auth")
    assert not any(name == "rebac" or name.startswith("rebac.") for name in sys.modules)
    assert not any(name == "angee.iam" or name.startswith("angee.iam.") for name in sys.modules)
    assert issubclass(AngeeModel, models.Model)
    assert isinstance(Entry.objects.all(), AngeeQuerySet)
    assert not hasattr(Entry, "require_access")
    assert not hasattr(Entry.objects, "with_actor")
    assert not hasattr(Entry.objects, "scoped_for_aggregate")


@pytest.mark.django_db
def test_normal_instance_queryset_bulk_and_base_manager_crud():
    row = Entry.objects.create(name="Created")
    assert Entry.objects.from_public_id(row.public_id) == row
    row.name = "Saved"
    row.save(update_fields=["name"])
    Entry.objects.filter(pk=row.pk).update(name="Updated")
    Entry.objects.bulk_create([Entry(name="Bulk")])
    assert set(Entry._base_manager.values_list("name", flat=True)) == {"Updated", "Bulk"}
    assert Entry.objects.aggregate(total=models.Count("pk")) == {"total": 2}
    Entry.objects.filter(name="Bulk").delete()
    row.delete()
    assert not Entry.objects.exists()


@pytest.mark.django_db
def test_generic_references_accept_normal_django_targets_and_own_deletion():
    row = PlainEntry.objects.create(name="Plain")
    Reference.validate_target(row)
    content_type, object_id = generic_pointer_target(row)
    edge = Reference.objects.create(content_type=content_type, object_id=object_id)
    assert edge.target == row
    assert edge.record_ref.model_label == "permissionless.PlainEntry"
    row.delete()
    assert not Reference.objects.filter(pk=edge.pk).exists()
