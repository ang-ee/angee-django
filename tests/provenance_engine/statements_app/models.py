from django.db import models

from angee.provenance.engine.history import HistoryMixin
from angee.provenance.engine.statements import (
    Aspect,
    PairAspect,
    SourceKind,
    StatementLinkMixin,
    StatementRecords,
    StatementsMixin,
    StatementSourceMixin,
)


class Principal(StatementSourceMixin):
    name = models.CharField(max_length=50)
    kind = models.CharField(max_length=32, default=SourceKind.DIRECTORY)
    admitted = models.BooleanField(default=True)

    @property
    def source_kind(self) -> str:
        return self.kind


class Link(StatementLinkMixin):
    is_authoritative = models.BooleanField(default=False)

    @property
    def authoritative(self) -> bool:
        return self.is_authoritative


class Member(models.Model):
    name = models.CharField(max_length=100)


class Subject(StatementsMixin, HistoryMixin):
    label = models.CharField(max_length=100, blank=True)
    detail = models.TextField(blank=True)
    statements = StatementRecords(
        aspects={
            "identity": Aspect(
                fields=("label",),
                precedence=(SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY),
            ),
            "detail": Aspect(fields=("detail",), precedence=(SourceKind.DIRECTORY, SourceKind.PERSON)),
            "membership": Aspect(member={"member": models.ForeignKey(Member, on_delete=models.PROTECT)}),
        },
        link="statements_test.Link",
    )
    same_as = PairAspect()


class RetainedSubject(StatementsMixin, HistoryMixin):
    label = models.CharField(max_length=100, blank=True)
    retired = models.BooleanField(default=False)
    statements = StatementRecords(aspects={"identity": Aspect(fields=("label",))})

    def revert_removal_collector(self) -> None:
        return None

    def revert_removal(self, *, reason: str = "") -> None:
        self.retired = True
        self.save(update_fields=["retired"])


class StatementsOnly(StatementsMixin):
    label = models.CharField(max_length=100, blank=True)
    statements = StatementRecords(aspects={"identity": Aspect(fields=("label",))})


class Code(models.Model):
    code = models.CharField(max_length=50, unique=True)


class TypedReference(StatementsMixin, HistoryMixin):
    code = models.ForeignKey(Code, to_field="code", on_delete=models.PROTECT, null=True)
    count = models.IntegerField(default=7)
    statements = StatementRecords(
        aspects={
            "reference": Aspect(fields=("code",)),
            "count": Aspect(fields=("count",)),
            "member": Aspect(member={"member": models.ForeignKey(Code, to_field="code", on_delete=models.PROTECT)}),
        }
    )
