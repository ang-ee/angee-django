"""Source fields exercise native nullable copies and generated relation access."""

from django.db import models

from angee.provenance.contrib.angee.history import ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.provenance.statements import Aspect, StatementRecords


class Changeset(models.Model):
    runtime = True

    class Meta:
        abstract = True


class Subject(models.Model):
    runtime = True
    objects = AngeeHistoryUnscopedManager()

    title = models.CharField(max_length=40)
    count = models.IntegerField()
    history = ModelHistory(inherit=True)
    statements = StatementRecords(aspects={"identity": Aspect(fields=("title", "count"))})

    class Meta:
        abstract = True
        base_manager_name = "objects"
