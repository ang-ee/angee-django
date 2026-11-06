"""Minimal concrete IAM source fixtures without optional capability imports."""

from django.db import models
from rebac import RebacMixin

from angee.base.models.children import OwnedChildrenMixin
from angee.base.models.trash import TrashQuerySet
from angee.base.querysets import MergeableQuerySet
from angee.iam.managers import ProtectedManager, ProtectedUnscopedManager
from angee.iam.models import Group as AbstractGroup
from angee.iam.models import IAMKind as AbstractKind
from angee.iam.models import IAMProtected as AbstractProtected
from angee.iam.models import User as AbstractUser
from angee.iam.models.base import ProtectedDataModel, ProtectedPersistenceMixin
from angee.iam.models.merge import ProtectedMergeableMixin
from angee.iam.models.trash import ProtectedTrashMixin
from angee.iam.querysets import ProtectedQuerySet


class User(AbstractUser):
    class Meta(AbstractUser.Meta):
        abstract = False
        app_label = "iam"
        rebac_resource_type = "auth/user"


class Group(AbstractGroup):
    class Meta(AbstractGroup.Meta):
        abstract = False
        app_label = "iam"
        rebac_resource_type = "auth/group"
        rebac_subject_relation = "member"


class IAMKind(AbstractKind):
    class Meta(AbstractKind.Meta):
        abstract = False
        app_label = "iam"
        managed = False
        rebac_resource_type = "iam/kind"


class IAMProtected(AbstractProtected):
    class Meta(AbstractProtected.Meta):
        abstract = False
        app_label = "iam"
        managed = False
        rebac_resource_type = "iam/protected"


class MergeTargetQuerySet(MergeableQuerySet, ProtectedQuerySet):
    pass


class TrashTargetQuerySet(TrashQuerySet, ProtectedQuerySet):
    pass


class MergeTarget(ProtectedMergeableMixin, ProtectedDataModel):
    objects = ProtectedManager.from_queryset(MergeTargetQuerySet)()

    class Meta(ProtectedDataModel.Meta):
        app_label = "iam"
        rebac_resource_type = "probe/merge"


class TrashTarget(ProtectedTrashMixin, ProtectedDataModel):
    objects = ProtectedManager.from_queryset(TrashTargetQuerySet)()

    class Meta(ProtectedDataModel.Meta):
        app_label = "iam"
        rebac_resource_type = "probe/trash"


class APIEntry(ProtectedDataModel):
    name = models.CharField(max_length=80)
    secret = models.CharField(max_length=80, blank=True)

    class Meta(ProtectedDataModel.Meta):
        app_label = "iam"
        rebac_resource_type = "probe/api"


class APILine(ProtectedDataModel):
    entry = models.ForeignKey(APIEntry, on_delete=models.CASCADE, related_name="lines")
    text = models.CharField(max_length=80)

    class Meta(ProtectedDataModel.Meta):
        app_label = "iam"
        rebac_resource_type = "probe/api_line"


class NativeAPIEntry(RebacMixin, OwnedChildrenMixin):
    name = models.CharField(max_length=80)
    secret = models.CharField(max_length=80, blank=True)
    slug = models.SlugField(blank=True, default="")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")

    class Meta:
        app_label = "iam"
        rebac_resource_type = "probe/native_api"


class NativeAPILine(models.Model):
    entry = models.ForeignKey(NativeAPIEntry, on_delete=models.CASCADE, related_name="lines")
    text = models.CharField(max_length=80)
    position = models.IntegerField(default=0)

    class Meta:
        app_label = "iam"


class NeutralAPIRoot(models.Model):
    label = models.CharField(max_length=80)
    target = models.ForeignKey(NativeAPIEntry, on_delete=models.CASCADE)

    class Meta:
        app_label = "iam"


class MixedDefaultSource(models.Model):
    plain = models.ForeignKey("permissionless.PlainDefaultTarget", on_delete=models.CASCADE)
    protected = models.ForeignKey(NativeAPIEntry, on_delete=models.CASCADE)

    class Meta:
        app_label = "iam"


class ProtectedMembership(ProtectedPersistenceMixin):
    """Field-free IAM persistence for an aggregate-owned reference row."""

    entry = models.ForeignKey(APIEntry, on_delete=models.CASCADE)
    label = models.CharField(max_length=80)
    objects = ProtectedManager()
    _angee_base = ProtectedUnscopedManager()

    class Meta:
        app_label = "iam"
        base_manager_name = "_angee_base"
