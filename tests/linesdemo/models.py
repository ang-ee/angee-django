"""Demo document + lines models for the F6 editable-lines Hasura tests.

A ``Document`` owns ordered ``DocumentLine`` children.
The parent is a REBAC resource (``linesdemo/document``, owner-gated write); the
child carries no row policy of its own — its rows are created, updated, and
deleted under the parent's authorization (the §3.4 elevation the write backend
applies after the parent write preflight). Both are concrete rows in a real
installed app so pytest-django builds the tables and ``rebac sync`` loads the
adjacent ``permissions.zed``.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from angee.base.fields import StateField
from angee.base.mixins import RowLockMixin
from angee.base.models import AngeeDataModel


class InputStampMixin(models.Model):
    """Consume a test-only ``stamp`` input extension through the cooperative hook.

    Each consumed stamp is recorded as a :class:`Tag` named
    ``<model>:<stamp>@<row pk>``, so a test observes that the hook ran after the
    row write and inside its transaction. The stamp ``reject`` raises after
    recording, so a test observes the rollback of both writes.
    """

    class Meta:
        """Abstract consumer composed onto the demo document and its lines."""

        abstract = True

    def apply_input_extensions(self, *, stamp: str | None = None, **values: Any) -> None:
        """Record ``stamp`` for this saved row, then delegate the remaining values."""

        if stamp is not None:
            Tag.objects.create(name=f"{self._meta.model_name}:{stamp}@{self.pk}")
            if stamp == "reject":
                raise ValidationError({"stamp": "This stamp is rejected."})
        super().apply_input_extensions(**values)


class Document(InputStampMixin, AngeeDataModel):
    """An owner-gated document whose lines are edited transactionally."""

    sqid_prefix = "doc_"

    title = models.CharField(max_length=200)
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Concrete REBAC document model for the editable-lines tests."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_document"
        rebac_resource_type = "linesdemo/document"


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


class Tag(AngeeDataModel):
    """A free vocabulary row a line references through an M2M (no row policy).

    The child's ``tags`` decodes/persists as public sqids, and the F6 lines metadata
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


class DocumentLine(InputStampMixin, AngeeDataModel):
    """One ordered child line of a :class:`Document` (no row policy of its own)."""

    sqid_prefix = "dln_"

    class Kind(models.TextChoices):
        """The line's product/service classification — the F6 enum child field."""

        GOODS = "goods", "Goods"
        SERVICE = "service", "Service"

    document = models.ForeignKey(
        Document,
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


class LineReceipt(AngeeDataModel):
    """A record that keeps a document line in use: removing that line is refused."""

    sqid_prefix = "lrc_"

    line = models.ForeignKey(DocumentLine, on_delete=models.PROTECT, related_name="+")

    class Meta(AngeeDataModel.Meta):
        """Concrete protecting model; no ``rebac_resource_type`` by design."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_line_receipt"


class PinnedLine(RowLockMixin, AngeeDataModel):
    """A document part whose pinned rows are system rows (the row-lock handle).

    A pinned row's ``label`` is locked: editable lines may change its quantity
    and order, but not its label, and may neither remove a pinned row nor create
    one. ``pinned`` itself is never a writable line column; only system writers
    (the tests' ``system_context`` seeding) set it.
    """

    sqid_prefix = "pln_"
    locked_rows_label = "Pinned lines"

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="pinned_lines",
    )
    label = models.CharField(max_length=200)
    quantity = models.IntegerField(default=1)
    position = models.IntegerField(default=0)
    pinned = models.BooleanField(default=False)

    class Meta(AngeeDataModel.Meta):
        """Concrete row-locked child-line model; authorized through its document."""

        abstract = False
        app_label = "linesdemo"
        db_table = "test_linesdemo_pinned_line"
        ordering = ("position", "pk")

    def locked_fields(self) -> tuple[str, ...]:
        """Lock a pinned row's label."""

        return ("label",) if self.pinned else ()
