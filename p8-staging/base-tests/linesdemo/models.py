"""Demo document + lines models for the F6 editable-lines Hasura tests.

A ``SaleDoc`` document owns ordered ``SaleLine`` children — the framework
stand-in for the arpee SalesOrder/Invoice document-with-lines shape (§3.14).
The parent is a REBAC resource (``linesdemo/document``, owner-gated write); the
child carries no row policy of its own — its rows are created, updated, and
deleted under the parent's authorization (the §3.4 elevation the write backend
applies after the parent write preflight). Both are concrete rows in a real
installed app so pytest-django builds the tables and ``rebac sync`` loads the
adjacent ``permissions.zed``.
"""

from __future__ import annotations

from django.db import models

from angee.base.fields import StateField
from angee.base.models import AngeeDataModel


class SaleDoc(AngeeDataModel):
    """An owner-gated document whose lines are edited transactionally."""

    sqid_prefix = "sdc_"

    title = models.CharField(max_length=200)
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Concrete REBAC document model for the editable-lines tests."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_document"
        rebac_resource_type = "linesdemo/document"
        rebac_id_attr = "sqid"


class Product(AngeeDataModel):
    """An owner-gated catalogue row a line may reference (visibility target).

    Its own REBAC policy is what a line's ``product`` public-id decode is scoped
    to: a line may only reference a product the caller can read, so a decode that
    ran under the §3.4 child elevation (sudo) instead of the caller's actor would
    leak invisible rows — the hole the two-phase diff closes.
    """

    sqid_prefix = "prd_"

    name = models.CharField(max_length=200)

    class Meta(AngeeDataModel.Meta):
        """Concrete owner-gated product model for line-relation visibility tests."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_product"
        rebac_resource_type = "linesdemo/product"
        rebac_id_attr = "sqid"


class Tag(AngeeDataModel):
    """A free vocabulary row a line references through an M2M (no row policy).

    Stands in for the arpee ``accounting.Tax`` M2M a document line carries: the
    child's ``tags`` decodes/persists as public sqids, and the F6 lines metadata
    projects it as a ``kind="list"`` relation the frontend renders as a
    multi-select. Non-REBAC (read-all) so the M2M decode is not the concern under
    test — the relation round-trip is.
    """

    sqid_prefix = "tag_"

    name = models.CharField(max_length=200)

    class Meta(AngeeDataModel.Meta):
        """Concrete read-all vocabulary model for line-M2M tests."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_tag"


class SaleLine(AngeeDataModel):
    """One ordered child line of a :class:`SaleDoc` (no row policy of its own)."""

    sqid_prefix = "sln_"

    class Kind(models.TextChoices):
        """The line's product/service classification — the F6 enum child field."""

        GOODS = "goods", "Goods"
        SERVICE = "service", "Service"

    document = models.ForeignKey(
        SaleDoc,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # An enum child field (a ``StateField`` choices column): reads as the UPPERCASE
    # wire member on the node, writes the lowercase model value through the String
    # line input (the F6 enum normalization the frontend cell applies).
    kind = StateField(choices_enum=Kind, default=Kind.GOODS)
    # An M2M child field: reads/writes public sqids (the F6 list normalization).
    tags = models.ManyToManyField(Tag, related_name="+", blank=True)
    label = models.CharField(max_length=200)
    quantity = models.IntegerField(default=1)
    position = models.IntegerField(default=0)

    class Meta(AngeeDataModel.Meta):
        """Concrete child-line model; no ``rebac_resource_type`` by design."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_line"
        ordering = ("position", "pk")
