"""Source fields exercise native nullable copies and generated relation access."""

from django.db import models

from angee.models.history import ModelHistory
from angee.models.statements import Aspect, StatementRecords


class Changeset(models.Model):
    runtime = True

    class Meta:
        abstract = True


class Subject(models.Model):
    runtime = True

    title = models.CharField(max_length=40)
    count = models.IntegerField()
    history = ModelHistory(inherit=True)
    statements = StatementRecords(aspects={"identity": Aspect(fields=("title", "count"))})

    class Meta:
        abstract = True
