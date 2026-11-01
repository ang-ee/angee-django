"""Minimal concrete IAM source fixtures without optional capability imports."""

from angee.base.models.trash import TrashQuerySet
from angee.base.querysets import MergeableQuerySet
from angee.iam.managers import ProtectedManager
from angee.iam.models import Group as AbstractGroup
from angee.iam.models import IAMKind as AbstractKind
from angee.iam.models import IAMProtected as AbstractProtected
from angee.iam.models import User as AbstractUser
from angee.iam.models.base import ProtectedDataModel
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
