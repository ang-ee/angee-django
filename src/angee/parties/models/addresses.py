"""Native addresses model owners for the contacts domain."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.db import models, transaction

from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.trash import TrashMixin
from angee.parties.fields import CountryCodeField
from angee.parties.managers import (
    AddressManager,
)
from angee.parties.mixins import PartyAspectMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.statements import (
    Aspect,
    SourceKind,
    StatementRecords,
)

if TYPE_CHECKING:
    pass


class Address(TrashMixin, PartyAspectMixin, HistoryMixin, AuditMixin, AngeeDataModel, concrete=True):
    """A physical or postal address of a party (the vCard ``ADR`` property).

    There is intentionally no ``(party, label)`` uniqueness — a party may carry
    two same-labelled addresses — so a CardDAV mapper keys idempotency on the
    address content, not the label.
    """

    sqid_prefix = "adr_"
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "address": Aspect(
                fields=(
                    "label",
                    "po_box",
                    "extended",
                    "street",
                    "city",
                    "region",
                    "postal_code",
                    "country",
                ),
                precedence=cast(
                    tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY)
                ),
            )
        },
        sqid_prefix="ads_",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(max_length=64, blank=True, default="")
    po_box = models.CharField(max_length=128, blank=True, default="")
    extended = models.TextField(blank=True, default="")
    street = models.TextField(blank=True, default="")
    city = models.TextField(blank=True, default="")
    region = models.TextField(blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")
    country = CountryCodeField(blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    objects = AddressManager()

    class Meta:
        """Django model options for the address source model."""

        abstract = True
        ordering = ("party", "label", "sqid")
        verbose_name_plural = "addresses"
        rebac_resource_type = "parties/address"
        constraints = (
            models.UniqueConstraint(
                fields=("party",),
                condition=models.Q(is_primary=True),
                name="parties_address_one_primary_per_party",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Make the selected primary address authoritative for this party."""

        update_fields = kwargs.get("update_fields")
        if not self.is_primary or (update_fields is not None and "is_primary" not in update_fields):
            super().save(*args, **kwargs)
            return
        with transaction.atomic():
            type(self).objects.demote_primaries(self)
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a one-line address for Django displays."""

        return ", ".join(part for part in (self.street, self.city, self.country) if part)
