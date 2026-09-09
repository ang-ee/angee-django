"""Immutable, ordered extraction evidence models."""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet
from angee.base.refs import RecordRefMixin
from angee.workflows_ocr.engines import OcrEngine


class ImmutableEvidenceQuerySet(AngeeQuerySet[Any]):
    """Prevent post-insert mutation and deletion through bulk ORM paths."""

    def update(self, **kwargs: Any) -> int:
        raise ValueError("Extraction evidence is immutable.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction evidence is retained and cannot be deleted through the ORM.")


class ImmutableEvidenceManager(AngeeManager.from_queryset(ImmutableEvidenceQuerySet)):  # type: ignore[misc]
    """Manager exposing immutable evidence reads plus initial bulk insertion."""


class Extraction(SqidMixin, AuditMixin, RecordRefMixin, AngeeModel):
    """One immutable schema-validated claim over an ordered file set."""

    runtime = True
    sqid_prefix = "ext_"
    rebac_grantable = {"viewer": "read"}

    revision = models.PositiveIntegerField(default=1, editable=False)
    lineage_key = models.CharField(max_length=64, db_index=True, editable=False)
    reuse_key = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=16, editable=False)
    error_code = models.CharField(max_length=100, blank=True, editable=False)
    schema_id = models.CharField(max_length=255, editable=False)
    schema_digest = models.CharField(max_length=64, editable=False)
    schema = models.JSONField(editable=False)
    engine = ImplClassField(base_class=OcrEngine, registry_setting="ANGEE_OCR_ENGINE_CLASSES", editable=False)
    model = models.ForeignKey(
        "agents.InferenceModel", null=True, blank=True, on_delete=models.PROTECT, related_name="ocr_extractions"
    )
    recognition_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocr_recognition_extractions",
    )
    engine_config = models.JSONField(default=dict, blank=True, editable=False)
    result = models.JSONField(editable=False)
    provenance = models.JSONField(default=dict, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=255)
    target = GenericForeignKey("content_type", "object_id")
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("lineage_key", "-revision")
        rebac_resource_type = "workflows_ocr/extraction"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("lineage_key", "revision"), name="uniq_ocr_extraction_revision"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse mutation after the evidence row has been inserted."""

        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction evidence is immutable; create a new revision.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction evidence is retained and cannot be deleted.")


class ExtractionSource(SqidMixin, AngeeModel):
    """One file's stable position and source identity in an extraction."""

    runtime = True
    sqid_prefix = "exs_"
    extraction = models.ForeignKey("workflows_ocr.Extraction", on_delete=models.CASCADE, related_name="sources")
    file = models.ForeignKey(
        "storage.File", null=True, blank=True, on_delete=models.PROTECT, related_name="ocr_sources"
    )
    message_part = models.ForeignKey(
        "messaging.Part", null=True, blank=True, on_delete=models.PROTECT, related_name="ocr_sources"
    )
    position = models.PositiveIntegerField(editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_ocr/extraction_source"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_ocr_source_position"),
            models.CheckConstraint(
                condition=(
                    models.Q(file__isnull=False, message_part__isnull=True)
                    | models.Q(file__isnull=True, message_part__isnull=False)
                ),
                name="ocr_source_exactly_one_input",
            ),
            models.UniqueConstraint(
                fields=("extraction", "file"), condition=models.Q(file__isnull=False), name="uniq_ocr_source_file"
            ),
            models.UniqueConstraint(
                fields=("extraction", "message_part"),
                condition=models.Q(message_part__isnull=False),
                name="uniq_ocr_source_message_part",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction source evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction source evidence is retained and cannot be deleted.")


class ExtractionPage(SqidMixin, AngeeModel):
    """One ordered page response retained as extraction evidence."""

    runtime = True
    sqid_prefix = "exp_"
    extraction = models.ForeignKey("workflows_ocr.Extraction", on_delete=models.CASCADE, related_name="pages")
    source = models.ForeignKey("workflows_ocr.ExtractionSource", on_delete=models.CASCADE, related_name="pages")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    dpi = models.PositiveIntegerField(editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    result = models.JSONField(editable=False)
    engine_metadata = models.JSONField(default=dict, blank=True, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_ocr/extraction_page"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_ocr_page_position"),
            models.UniqueConstraint(fields=("source", "source_page"), name="uniq_ocr_source_page"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction page evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction page evidence is retained and cannot be deleted.")


class ExtractionPart(SqidMixin, AngeeModel):
    """One ordered raw text or structured artifact behind a final claim."""

    runtime = True
    sqid_prefix = "exr_"
    extraction = models.ForeignKey("workflows_ocr.Extraction", on_delete=models.CASCADE, related_name="parts")
    source = models.ForeignKey("workflows_ocr.ExtractionSource", on_delete=models.CASCADE, related_name="parts")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(null=True, blank=True, editable=False)
    mime_type = models.CharField(max_length=128, editable=False)
    kind = models.CharField(max_length=32, editable=False)
    method = models.CharField(max_length=128, editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    dpi = models.PositiveIntegerField(null=True, blank=True, editable=False)
    value = models.JSONField(editable=False)
    claims = models.JSONField(default=dict, blank=True, editable=False)
    metadata = models.JSONField(default=dict, blank=True, editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_ocr/extraction_part"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("extraction", "position"), name="uniq_ocr_part_position"),)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction part evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction part evidence is retained and cannot be deleted.")
