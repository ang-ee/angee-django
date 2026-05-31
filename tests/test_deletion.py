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


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_counts_set_null_updates() -> None:
    """Set-null related rows are reported as updates."""

    class PreviewNullableParent(models.Model):
        """Parent model targeted by nullable children."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class PreviewNullableChild(models.Model):
        """Child model updated when its parent is deleted."""

        parent = models.ForeignKey(
            PreviewNullableParent,
            null=True,
            on_delete=models.SET_NULL,
        )

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewNullableParent)
        schema_editor.create_model(PreviewNullableChild)
    try:
        parent = PreviewNullableParent.objects.create(name="parent")
        PreviewNullableChild.objects.create(parent=parent)

        preview = DeletionPreview.from_instance(parent)

        assert preview.updated[0].count == 1
        assert not preview.has_blockers
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewNullableChild)
            schema_editor.delete_model(PreviewNullableParent)


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_reports_restricted_blockers() -> None:
    """Restricted related rows are reported as blockers."""

    class PreviewRestrictedParent(models.Model):
        """Parent model targeted by a restricted child."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class PreviewRestrictedChild(models.Model):
        """Child model that restricts parent deletion."""

        parent = models.ForeignKey(
            PreviewRestrictedParent,
            on_delete=models.RESTRICT,
        )

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewRestrictedParent)
        schema_editor.create_model(PreviewRestrictedChild)
    try:
        parent = PreviewRestrictedParent.objects.create(name="parent")
        PreviewRestrictedChild.objects.create(parent=parent)

        preview = DeletionPreview.from_instance(parent)

        assert preview.has_blockers
        assert preview.blocked[0].count == 1
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewRestrictedChild)
            schema_editor.delete_model(PreviewRestrictedParent)


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_counts_fast_deletes() -> None:
    """Fast-delete related rows are included in deleted counts."""

    class PreviewCascadeParent(models.Model):
        """Parent model targeted by fast-delete children."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class PreviewCascadeChild(models.Model):
        """Child model that can be fast-deleted."""

        parent = models.ForeignKey(
            PreviewCascadeParent,
            on_delete=models.CASCADE,
        )

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewCascadeParent)
        schema_editor.create_model(PreviewCascadeChild)
    try:
        parent = PreviewCascadeParent.objects.create(name="parent")
        PreviewCascadeChild.objects.create(parent=parent)

        preview = DeletionPreview.from_instance(parent)

        deleted = {group.label: group.count for group in preview.deleted}
        parent_label = str(PreviewCascadeParent._meta.verbose_name_plural)
        child_label = str(PreviewCascadeChild._meta.verbose_name_plural)
        assert deleted[parent_label] == 1
        assert deleted[child_label] == 1
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewCascadeChild)
            schema_editor.delete_model(PreviewCascadeParent)
