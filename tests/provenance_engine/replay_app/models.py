from django.db import models

from angee.provenance.engine.history import HistoryMixin


class Folder(HistoryMixin):
    name = models.CharField(max_length=100)
    history_relations = ("entries",)


class Entry(HistoryMixin):
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name="entries")
    value = models.DecimalField(max_digits=12, decimal_places=3)


class SpecialFolder(Folder):
    tag = models.CharField(max_length=100)


class OptionalEntry(HistoryMixin):
    folder = models.ForeignKey(Folder, null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=100)
