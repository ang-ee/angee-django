"""Example owners of roster authority, record-derived decision access and donor aspects."""

from typing import cast

from django.db import models

from angee.base.fields.relations import RetainedGenericRelation
from angee.base.models.base import AngeeDataModel
from angee.base.models.roles import role_anchor
from angee.provenance.statements import (
    Aspect,
    SourceKind,
    StatementQuerySet,
    StatementRecords,
    StatementsMixin,
)

Role = role_anchor("extcontrib/role", name="Role")


class Record(AngeeDataModel):
    """A concerned record exposing retained links through a typed query path."""

    sqid_prefix = "xrc_"
    name = models.CharField(max_length=100)
    decision_records = RetainedGenericRelation("decisions.DecisionRecord", related_query_name="extcontrib_record")

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_extcontrib_record"
        rebac_resource_type = "extcontrib/record"


class ClaimedStatementQuerySet(StatementQuerySet):
    """The subject source's own statement queryset, which donors must not displace."""


class ClaimedSource(StatementsMixin, AngeeDataModel):
    """The subject source: it declares the statement table's options and its own aspect."""

    sqid_prefix = "xcl_"
    name = models.CharField(max_length=100)
    statements = StatementRecords(
        aspects={"identity": Aspect(fields=("name",))},
        sqid_prefix="xcs_",
        historical_queryset=ClaimedStatementQuerySet,
    )

    class Meta(AngeeDataModel.Meta):
        abstract = True


class ClaimedBilling(models.Model):
    """A consumer donor contributing a tax aspect to the claimed record, as arp does for parties.

    Composed by hand below in the composer's base order (donors before the
    source); a declared ``extends`` would make the composer discover it.
    """

    vat = models.CharField(max_length=32, blank=True, default="")
    registry = models.CharField(max_length=32, blank=True, default="")
    statements = StatementRecords(
        aspects={
            "tax": Aspect(
                fields=("vat",),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD)),
            ),
            # People never state the registry entry: only a system of record or an import does.
            "registry": Aspect(
                fields=("registry",),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.SYSTEM_OF_RECORD, SourceKind.IMPORT)),
            ),
        },
    )

    class Meta:
        abstract = True


class Claimed(ClaimedBilling, ClaimedSource):
    """A statement subject composed from a source and a consumer donor."""

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_extcontrib_claimed"
        rebac_resource_type = "extcontrib/claimed"
