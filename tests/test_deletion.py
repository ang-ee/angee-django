"""Tests for deletion preview domain objects."""

from __future__ import annotations

import pytest
from django.db import connection, models
from django.db.models.deletion import Collector
from django.db.models.signals import post_delete, pre_delete
from rebac import RebacMixin, SubjectRef, actor_context, system_context
from rebac.signals import _rebac_cascade_resource, _rebac_pre_delete

from angee.base.deletion import DeletionPreview, DeletionPreviewNode


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
        PreviewNullableChild.objects.create(parent=parent)

        preview = DeletionPreview.from_instance(parent)

        assert preview.updated[0].count == 2
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
        pre_delete.disconnect(_rebac_pre_delete)
        post_delete.disconnect(_rebac_cascade_resource)
        parent = PreviewCascadeParent.objects.create(name="parent")
        PreviewCascadeChild.objects.create(parent=parent)
        collector = Collector(using=parent._state.db or "default")
        collector.collect([parent])

        assert any(queryset.model is PreviewCascadeChild for queryset in collector.fast_deletes)

        preview = DeletionPreview.from_instance(parent)

        deleted = {group.label: group.count for group in preview.deleted}
        parent_label = str(PreviewCascadeParent._meta.verbose_name_plural)
        child_label = str(PreviewCascadeChild._meta.verbose_name_plural)
        assert deleted[parent_label] == 1
        assert deleted[child_label] == 1
    finally:
        pre_delete.connect(_rebac_pre_delete)
        post_delete.connect(_rebac_cascade_resource)
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewCascadeChild)
            schema_editor.delete_model(PreviewCascadeParent)


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_hides_rebac_child_leaves_without_read_access() -> None:
    """Actor-scoped previews do not expose related resource row labels or ids."""

    class PreviewScopedParent(models.Model):
        """Parent model targeted by a scoped cascade child."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class PreviewScopedChild(RebacMixin):
        """REBAC resource hidden from the preview actor."""

        parent = models.ForeignKey(
            PreviewScopedParent,
            on_delete=models.CASCADE,
        )
        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            rebac_resource_type = "auth/user"

        def __str__(self) -> str:
            """Return the child name for preview display labels."""

            return self.name

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PreviewScopedParent)
        schema_editor.create_model(PreviewScopedChild)
    try:
        with system_context(reason="test-setup"):
            parent = PreviewScopedParent.objects.create(name="parent")
            child = PreviewScopedChild.objects.create(parent=parent, name="Hidden child")

        with actor_context(SubjectRef.of("auth/user", "reader")):
            preview = DeletionPreview.from_instance(parent)

        child_group = next(group for group in preview.root.children if group.label == "preview scoped childs")
        assert child_group.object_label == "1 preview scoped childs"
        assert child_group.children[0].object_label == "1 more records"
        assert child_group.children[0].object_id is None

        assert "Hidden child" not in _tree_object_labels(preview.root)
        assert str(child.pk) not in _tree_object_ids(child_group)
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PreviewScopedChild)
            schema_editor.delete_model(PreviewScopedParent)


def _tree_object_labels(node: DeletionPreviewNode) -> tuple[str, ...]:
    """Return every object label in a preview tree."""

    return (node.object_label, *(label for child in node.children for label in _tree_object_labels(child)))


def _tree_object_ids(node: DeletionPreviewNode) -> tuple[str, ...]:
    """Return every concrete object id in a preview tree."""

    own = () if node.object_id is None else (node.object_id,)
    return (*own, *(object_id for child in node.children for object_id in _tree_object_ids(child)))
