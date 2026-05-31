"""Tests for deletion preview domain objects."""

from __future__ import annotations

import pytest
from django.db import connection, models

from angee.base.deletion import DeletionPreview


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_counts_deleted_rows() -> None:
    """A standalone row previews as one deleted object."""

    class PreviewItem(models.Model):
        """Concrete model used for deletion preview tests."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewItem)
    try:
        item = PreviewItem.objects.create(name="draft")

        preview = DeletionPreview.from_instance(item)

        assert preview.total_deleted_count == 1
        assert preview.deleted[0].count == 1
        assert not preview.has_blockers
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewItem)


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_reports_protected_blockers() -> None:
    """Protected related rows are reported as blockers."""

    class PreviewParent(models.Model):
        """Parent model targeted by a protected child."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class PreviewChild(models.Model):
        """Child model that blocks parent deletion."""

        parent = models.ForeignKey(PreviewParent, on_delete=models.PROTECT)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewParent)
        schema_editor.create_model(PreviewChild)
    try:
        parent = PreviewParent.objects.create(name="parent")
        PreviewChild.objects.create(parent=parent)

        preview = DeletionPreview.from_instance(parent)

        assert preview.has_blockers
        assert preview.blocked[0].count == 1
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewChild)
            schema_editor.delete_model(PreviewParent)
