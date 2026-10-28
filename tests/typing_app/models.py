"""Native model declarations used by positive and negative typing fixtures."""

from __future__ import annotations

from typing import Self, cast

from django.db import models

from angee.base.managers import AngeeManager
from angee.base.querysets import AngeeQuerySet, SystemQuerysetMixin
from tests.typing_app.factory import OtherFactory

factory_result = OtherFactory.from_queryset(1)


class AbstractTree(models.Model):
    parent = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
    count = models.IntegerField()

    class Meta:
        abstract = True

    @classmethod
    def parent_ids(cls) -> list[object]:
        return list(cls._base_manager.values_list("parent_id", flat=True))


class Tree(AbstractTree):
    name = models.CharField(max_length=64)


class Other(models.Model):
    name = models.CharField(max_length=64)


class FrameworkRecord(models.Model):
    """Ordinary source model preserves its fields through Angee's manager."""

    title = models.CharField(max_length=64)
    count = models.IntegerField()

    objects = AngeeManager()


class TypingChangeset(models.Model):
    """The swappable-model hook retains this configured owner's native shape."""

    note = models.CharField(max_length=64)
    actor_ref = models.TextField()
    change_reason = models.TextField()
    touched = models.JSONField(default=list)
    counts = models.JSONField(default=dict)

    @property
    def created_row(self) -> models.Model | None:
        return None

    def close(self, outcome: str) -> None:
        pass

    def record_created(self, row: models.Model) -> None:
        pass


class SystemRecordQuerySet[RecordT: SystemRecord](AngeeQuerySet[RecordT]):
    def selected(self) -> Self:
        return self.filter(count__gt=0)


_SystemRecordManagerBase = AngeeManager.from_queryset(SystemRecordQuerySet)


class SystemRecordManager[RecordT: SystemRecord](_SystemRecordManagerBase[RecordT]):
    def get_queryset(self) -> SystemRecordQuerySet[RecordT]:
        return cast(SystemRecordQuerySet[RecordT], super().get_queryset())


class SystemRecord(SystemQuerysetMixin):
    count = models.IntegerField()
    objects = SystemRecordManager()


class PlainSystemRecord(SystemQuerysetMixin):
    count = models.IntegerField()
