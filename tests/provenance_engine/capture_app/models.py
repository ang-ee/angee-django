from django.db import models

from angee.provenance.engine.base import ChangesetAttribution
from angee.provenance.history import CaptureManager, ModelHistory
from angee.provenance.history import ReplayHistoryMixin as HistoryMixin


class Note(HistoryMixin, models.Model):
    title = models.CharField(max_length=100)
    number = models.IntegerField(default=0)


class Child(HistoryMixin, models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)


class DetailedNote(Note):
    detail = models.CharField(max_length=100, default="")


class NoteProxy(Note):
    class Meta:
        proxy = True


class Explicit(models.Model):
    title = models.CharField(max_length=100)
    counter = models.IntegerField(default=0)
    history = ModelHistory(excluded_fields=["counter"])
    objects = CaptureManager()

    class Meta:
        base_manager_name = "objects"


class LocalNote(HistoryMixin, models.Model):
    title = models.CharField(max_length=100)
    history = ModelHistory(inherit=True, use_base_model_db=True)


class IndependentNote(Note):
    child_key = models.IntegerField(primary_key=True)
    detail = models.CharField(max_length=100, default="")


class AttributedNote(HistoryMixin, ChangesetAttribution):
    title = models.CharField(max_length=100)


class DomainChangesetNote(HistoryMixin, models.Model):
    changeset = models.CharField(max_length=100)
    title = models.CharField(max_length=100)
