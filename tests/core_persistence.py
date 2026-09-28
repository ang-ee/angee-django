"""Shared concrete probes for the base persistence contracts.

Django's installed, unmigrated test app owns their tables; permission tests use
the native db fixture and local schema backend. Their complete in-memory policy
needs no synchronization of unrelated addon schemas.
"""

import pytest
from django.conf import settings
from django.db import models
from rebac.backends import backend, reset_backend
from rebac.schema import parse_zed

from angee.base.mixins import (
    AuditMixin,
    CreationKeyMixin,
    CreationKeyQuerySet,
    HistoryMixin,
    ImmutableFieldsMixin,
    ItemOwnershipMixin,
    OptimisticLockMixin,
    OwnerMixin,
    OwnerQuerySet,
)
from angee.base.models import AngeeModel, AngeeQuerySet


class RevisionRow(OptimisticLockMixin):
    title = models.CharField(max_length=40, default="original")
    other = models.CharField(max_length=40, default="retained")

    class Meta:
        app_label = "scopedemo"


class RevisionChild(RevisionRow):
    child_id = models.IntegerField(primary_key=True)
    detail = models.CharField(max_length=40, default="child")

    class Meta:
        app_label = "scopedemo"


class OwnershipContainer(ItemOwnershipMixin):
    class Meta:
        app_label = "scopedemo"


class OwnedRows(OwnerQuerySet, AngeeQuerySet):
    """Compose release with the framework's ordinary queryset mechanics."""


class OwnedRow(OwnerMixin, HistoryMixin, AngeeModel):
    owner_container = "container"
    container = models.ForeignKey(OwnershipContainer, null=True, on_delete=models.CASCADE)
    title = models.CharField(max_length=40, default="owned")
    objects = OwnedRows.as_manager()

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/owned_row"


class CreationRow(CreationKeyMixin, AuditMixin):
    title = models.CharField(max_length=40, default="original")
    objects = CreationKeyQuerySet.as_manager()

    class Meta:
        app_label = "scopedemo"
        constraints = [CreationKeyMixin.creation_key_constraint()]


class ReceiptRow(ImmutableFieldsMixin):
    immutable_fields = ("identity", "recipient_id")
    identity = models.CharField(max_length=40)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=40, default="original")

    class Meta:
        app_label = "scopedemo"


OWNERSHIP_SCHEMA = """
definition auth/user {}
definition scopedemo/owned_row {
    relation owner: auth/user // rebac:field=owner
    permission read = owner
    permission create = authenticated
    permission write = owner
    permission delete = owner
    permission transfer = owner
}
"""


@pytest.fixture
def ownership_tables(db):
    backend().set_schema(parse_zed(OWNERSHIP_SCHEMA))
    try:
        yield
    finally:
        reset_backend()
