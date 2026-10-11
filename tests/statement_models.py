"""Neutral statement declarations loaded by the disposable composed host."""

from django.db import models

from angee.base.mixins import Aspect, HistoryMixin, PairAspect, StatementRecords, StatementsMixin
from angee.base.models import AngeeDataModel


class StatementSubject(StatementsMixin, HistoryMixin, AngeeDataModel):
    """Exercise scalar, typed-member and ordered-pair generation during boot."""

    label = models.CharField(max_length=100, blank=True)
    detail = models.TextField(blank=True)
    statements = StatementRecords(
        aspects={
            "identity": Aspect(fields=("label",)),
            "membership": Aspect(
                fields=("detail",),
                member={"member": models.ForeignKey("base.StatementSubject", on_delete=models.PROTECT)},
            ),
        },
        link=False,
    )
    same_as = PairAspect()

    class Meta(AngeeDataModel.Meta):
        app_label = "base"
        db_table = "test_statement_subject"
        base_manager_name = "_angee_base"
