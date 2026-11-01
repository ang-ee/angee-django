"""Native mixed-capability protected source declarations for the minimal IAM roster."""

from django.db import models

from angee.base.querysets import MergeableQuerySet
from angee.iam.managers import ProtectedManager
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.derived import ProtectedDerivedFrom
from angee.iam.models.locks import ProtectedRowLockMixin, ProtectedRowLockQuerySet
from angee.iam.models.merge import ProtectedMergeableMixin
from angee.iam.querysets import ProtectedQuerySet


class LockedQuerySet(ProtectedRowLockQuerySet, ProtectedQuerySet):
    """The source's own row-lock collection."""


class MergeQuerySet(MergeableQuerySet, ProtectedQuerySet):
    """The source's own operational-identity collection."""


class Evidence(ProtectedDerivedFrom, concrete=True):
    label = models.CharField(max_length=80)
    objects = ProtectedManager.from_queryset(ProtectedQuerySet)()

    class Meta(ProtectedDerivedFrom.Meta):
        abstract = True
        rebac_resource_type = "permissioned/evidence"


class Locked(ProtectedRowLockMixin, ProtectedDataModel, concrete=True):
    label = models.CharField(max_length=80)
    objects = ProtectedManager.from_queryset(LockedQuerySet)()

    class Meta(ProtectedDataModel.Meta):
        abstract = True
        rebac_resource_type = "permissioned/locked"


class Mergeable(ProtectedMergeableMixin, ProtectedDataModel, concrete=True):
    label = models.CharField(max_length=80)
    objects = ProtectedManager.from_queryset(MergeQuerySet)()

    class Meta(ProtectedDataModel.Meta):
        abstract = True
        rebac_resource_type = "permissioned/mergeable"
