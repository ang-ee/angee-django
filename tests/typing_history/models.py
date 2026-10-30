"""Source fields exercise native nullable copies and generated relation access."""

from typing import Self, cast

from django.db import models
from django_compose.models import Composable
from simple_history.manager import HistoricalQuerySet

from angee.provenance.contrib.angee.history import ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.provenance.history import ModelHistoryManager
from angee.provenance.statements import Aspect, StatementRecords


class FixtureHistoricalQuerySet[T: models.Model](HistoricalQuerySet[T]):
    def tagged(self) -> Self:
        return self

    def latest_of_each(self) -> Self:
        return cast(Self, super().latest_of_each())

    def label(self) -> int:
        return 1


class FixtureHistoryManager[T: models.Model](ModelHistoryManager[T]):
    def label(self) -> str:
        return "manager"


class Changeset(Composable, concrete=True):
    class Meta:
        abstract = True


class Subject(Composable, concrete=True):
    objects = AngeeHistoryUnscopedManager()

    title = models.CharField(max_length=40)
    count = models.IntegerField()
    history = ModelHistory(
        inherit=True,
        history_manager=FixtureHistoryManager,
        historical_queryset=FixtureHistoricalQuerySet,
    )
    statements = StatementRecords(aspects={"identity": Aspect(fields=("title", "count"))})

    class Meta:
        abstract = True
        base_manager_name = "objects"
