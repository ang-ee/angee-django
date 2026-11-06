"""Source fields exercise native nullable copies and generated relation access."""

from typing import Self, cast

from django.db import models
from django_compose.models import Composable
from simple_history.manager import HistoricalQuerySet
from simple_history.models import HistoricalChanges

from angee.provenance.contrib.angee.history import ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.provenance.history import ModelHistoryManager
from angee.provenance.statements import Aspect, PairAspect, StatementRecords

from .querysets import FixtureStatementQuerySet


class FixtureHistoricalQuerySet[T: HistoricalChanges](HistoricalQuerySet[T]):
    def tagged(self) -> Self:
        return self

    def latest_of_each(self) -> Self:
        return cast(Self, super().latest_of_each())

    def label(self) -> int:
        return 1


class FixtureHistoryManager[T: HistoricalChanges](ModelHistoryManager[T]):
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
    statements = StatementRecords(
        aspects={"identity": Aspect(fields=("title", "count"))},
        historical_queryset=FixtureStatementQuerySet,
    )
    same_as = PairAspect()

    class Meta:
        abstract = True
        base_manager_name = "objects"


class SubjectDonor(Composable, extends="typing_history.Subject"):
    donation = models.IntegerField(default=0)
    statements = StatementRecords(aspects={"donation": Aspect(fields=("donation",))})

    def statement_handle_count(self) -> int:
        return self.statements.handles().count()

    class Meta:
        abstract = True


class Link(Composable, concrete=True):
    external_key = models.CharField(max_length=40)

    class Meta:
        abstract = True


class Child(Composable, inherits="typing_history.Subject"):
    child_value = models.CharField(max_length=40)
    statements = StatementRecords(aspects={"child": Aspect(fields=("child_value",))}, link="typing_history.Link")

    def linked_count(self) -> int:
        return self.statements.current().exclude(link__external_key="remote").count()

    class Meta:
        abstract = True
        base_manager_name = "objects"


class Grandchild(Composable, inherits="typing_history.Child"):
    class Meta:
        abstract = True
        base_manager_name = "objects"


class UnresolvedSubject(models.Model):
    objects = AngeeHistoryUnscopedManager()
    title = models.CharField(max_length=40)
    count = models.IntegerField()
    history = ModelHistory(
        inherit=True, history_manager=FixtureHistoryManager, historical_queryset=FixtureHistoricalQuerySet
    )
    statements = StatementRecords(aspects={"identity": Aspect(fields=("title", "count"))})
    same_as = PairAspect()

    class Meta:
        abstract = True


class SharedSubject(Composable):
    title = models.CharField(max_length=40)
    statements = StatementRecords(aspects={"identity": Aspect(fields=("title",))})

    class Meta:
        abstract = True


class FirstShared(SharedSubject, concrete=True):
    class Meta:
        abstract = True


class SecondShared(SharedSubject, concrete=True):
    class Meta:
        abstract = True


class AmbiguousDonor(Composable, extends="typing_history.SharedSubject"):
    statements = StatementRecords(aspects={"donation": Aspect(fields=("title",))})

    class Meta:
        abstract = True
