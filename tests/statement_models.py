"""Neutral statement declarations loaded by the disposable composed host."""

from django.db import models

from angee.models import (
    AngeeDataModel,
)
from angee.money.fields import MoneyField
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.statements import (
    Aspect,
    PairAspect,
    StatementRecords,
    StatementsMixin,
)


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
        link=None,
    )
    same_as = PairAspect()

    class Meta(AngeeDataModel.Meta):
        app_label = "base"
        db_table = "test_statement_subject"
        base_manager_name = "_angee_base"


class Ledger(StatementsMixin, HistoryMixin, AngeeDataModel):
    """A subject whose ``<Subject>Statement`` name is taken and whose aspect copies a money column."""

    amount = MoneyField(max_digits=12, decimal_places=2, default=0, currency_field="currency")
    currency = models.ForeignKey("money.Currency", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    statements = StatementRecords(aspects={"amount": Aspect(fields=("amount",))}, link=None, model_name="LedgerStance")

    class Meta(AngeeDataModel.Meta):
        app_label = "base"
        db_table = "test_statement_ledger"
        base_manager_name = "_angee_base"


class LedgerStatement(AngeeDataModel):
    """The domain model that owns the ``LedgerStatement`` name."""

    class Meta(AngeeDataModel.Meta):
        app_label = "base"
        db_table = "test_statement_ledger_statement"
