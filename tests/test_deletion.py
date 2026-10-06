"""Tests for GraphQL deletion preview objects and the public-id delete helper."""

from __future__ import annotations

from typing import Any, Self, cast

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.deletion import Collector
from django.db.models.signals import pre_delete
from django.test import override_settings
from django.test.utils import isolate_apps
from graphql import GraphQLError
from rebac import PermissionDenied, RebacMixin, SubjectRef, actor_context, system_context, to_object_ref
from rebac.models import active_relationship_model

import angee.graphql.deletion as deletion_module
from angee.base.models import AngeeModel
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.deletion import (
    DeletePreview,
    DeletePreviewGroup,
    DeletePreviewNode,
    delete_by_public_id,
)
from angee.graphql.ids import RECORD_NOT_FOUND_MESSAGE
from tests.tables import model_tables


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
@pytest.mark.parametrize("outcome", ("preview", "denied", "vanished", "scope_changed", "allowed"))
def test_confirm_delete_composes_model_locks_after_permission_with_caller_scope(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    """Confirmation retains authorization, availability and surface membership at lock time."""

    locked: list[int] = []

    class ScopedDeleteTarget(AngeeModel):
        """Target whose eligibility can change while a domain lock is acquired."""

        eligible = models.BooleanField(default=True)

        class Meta:
            """Register the isolated lock target."""

            app_label = "auth"

        def lock_for_delete(self, *, queryset: models.QuerySet[Self] | None = None) -> Self | None:
            """Simulate a target disappearing or leaving the caller's scope."""

            locked.append(self.pk)
            if outcome == "vanished":
                return None
            if outcome == "scope_changed":
                type(self).system_queryset().filter(pk=self.pk).update(eligible=False)
            return super().lock_for_delete(queryset=queryset)

    monkeypatch.setattr(ScopedDeleteTarget, "has_access", lambda self, action: outcome != "denied")
    with model_tables((ScopedDeleteTarget,)), system_context(reason="tests.deletion.lock_contract"):
        target = ScopedDeleteTarget.objects.create()
        targets = ScopedDeleteTarget.objects.filter(eligible=True)
        if outcome == "denied":
            with pytest.raises(PermissionDenied, match="not allowed to delete"):
                delete_by_public_id(ScopedDeleteTarget, str(target.pk), confirm=True, queryset=targets)
        elif outcome in ("vanished", "scope_changed"):
            with pytest.raises(ValidationError) as caught:
                delete_by_public_id(ScopedDeleteTarget, str(target.pk), confirm=True, queryset=targets)
            assert caught.value.messages == [RECORD_NOT_FOUND_MESSAGE]
            assert caught.value.code == "not_found"
        else:
            preview = delete_by_public_id(
                ScopedDeleteTarget, str(target.pk), confirm=outcome == "allowed", queryset=targets,
            )
            assert not preview.has_blockers
            assert (preview.deleted_instance is not None) is (outcome == "allowed")

        assert locked == ([] if outcome in ("preview", "denied") else [target.pk])
        assert ScopedDeleteTarget.objects.filter(pk=target.pk).exists() is (outcome != "allowed")


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_counts_deleted_rows() -> None:
    """A standalone row previews as one deleted object."""

    class PreviewItem(models.Model):
        """Concrete model used for deletion preview tests."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((PreviewItem,)):
        item = PreviewItem.objects.create(name="draft")

        preview = DeletePreview.from_instance(item)

        assert preview.total_deleted_count == 1
        assert preview.deleted[0].count == 1
        assert not preview.has_blockers


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
@pytest.mark.parametrize("blocked_target", ["root", "collected"])
def test_deletion_preview_reports_model_blockers_and_refuses_confirm(
    blocked_target: str,
) -> None:
    """Model refusals are distinct messages, including collected cascade children."""

    class PolicyParent(AngeeModel):
        """Deletion target with an optional model-owned refusal."""

        blocked = models.BooleanField(default=False)

        class Meta:
            """Django model options for the test target."""

            app_label = "auth"

        def delete_blocker(self) -> str | None:
            """Return the target's public-safe deletion refusal."""

            return "Release the parent first." if self.blocked else None

    class PolicyChild(AngeeModel):
        """Cascade row with an optional model-owned refusal."""

        parent = models.ForeignKey(PolicyParent, on_delete=models.CASCADE)
        blocked = models.BooleanField(default=False)

        class Meta:
            """Django model options for the test child."""

            app_label = "auth"

        def delete_blocker(self) -> str | None:
            """Return the child's public-safe deletion refusal."""

            return "Release the children first." if self.blocked else None

    def refuse_delete(sender: Any, instance: AngeeModel, **kwargs: Any) -> None:
        """Enforce each test model's refusal on every deletion path."""

        if message := instance.delete_blocker():
            raise ValidationError(message)

    for model in (PolicyParent, PolicyChild):
        pre_delete.connect(refuse_delete, sender=model)
    try:
        with model_tables((PolicyParent, PolicyChild)), system_context(reason="tests.deletion.policy"):
            parent = PolicyParent.objects.create(blocked=blocked_target == "root")
            for _ in range(2):
                PolicyChild.objects.create(parent=parent, blocked=blocked_target != "root")

            preview = delete_by_public_id(PolicyParent, str(parent.pk), confirm=True)

            message = "Release the parent first." if blocked_target == "root" else "Release the children first."
            assert preview.has_blockers
            assert preview.refusals == [message]
            assert preview.blocked == []
            with pytest.raises(GraphQLError) as caught:
                preview.require_no_blockers()
            assert caught.value.message == message
            assert caught.value.extensions == {"code": "BAD_USER_INPUT"}
            assert PolicyParent.objects.filter(pk=parent.pk).exists()
            assert PolicyChild.objects.filter(parent=parent).count() == 2
    finally:
        for model in (PolicyParent, PolicyChild):
            pre_delete.disconnect(refuse_delete, sender=model)


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
def test_deletion_preview_error_includes_model_refusals_and_fk_blockers() -> None:
    """Neither a model refusal nor a protected relation hides the other in errors."""

    class PolicyParent(AngeeModel):
        """Deletion target refused independently of its protected child."""

        class Meta:
            """Register the isolated target."""

            app_label = "auth"

        def delete_blocker(self) -> str | None:
            """Return the model's readable refusal."""

            return "Release the parent first."

    class PolicyChild(models.Model):
        """Protected relation reported alongside the target's refusal."""

        parent = models.ForeignKey(PolicyParent, on_delete=models.PROTECT)

        class Meta:
            """Register the isolated child."""

            app_label = "auth"

    def refuse_delete(sender: Any, instance: PolicyParent, **kwargs: Any) -> None:
        """Enforce the target's rule through Django's deletion lifecycle."""

        raise ValidationError(instance.delete_blocker())

    pre_delete.connect(refuse_delete, sender=PolicyParent)
    try:
        with model_tables((PolicyParent, PolicyChild)), system_context(reason="tests.deletion.mixed"):
            parent = PolicyParent.objects.create()
            PolicyChild.objects.create(parent=parent)
            preview = DeletePreview.from_instance(parent)
            assert preview.refusals == ["Release the parent first."]
            assert [(group.label, group.count) for group in preview.blocked] == [("policy childs", 1)]
            with pytest.raises(GraphQLError) as caught:
                AngeeHasuraWriteBackend(PolicyParent).delete(cast(Any, None), str(parent.pk))
            assert caught.value.message == (
                "Release the parent first. Deletion is blocked by related records: policy childs (1)."
            )
            assert caught.value.extensions == {"code": "BAD_USER_INPUT"}
            assert PolicyParent.objects.filter(pk=parent.pk).exists()
    finally:
        pre_delete.disconnect(refuse_delete, sender=PolicyParent)


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

    with model_tables((PreviewParent, PreviewChild)):
        parent = PreviewParent.objects.create(name="parent")
        PreviewChild.objects.create(parent=parent)

        preview = DeletePreview.from_instance(parent)

        assert preview.has_blockers
        assert preview.blocked[0].count == 1
        with pytest.raises(GraphQLError) as caught:
            AngeeHasuraWriteBackend(PreviewParent).delete(cast(Any, None), str(parent.pk))
        assert str(PreviewChild._meta.verbose_name_plural) in caught.value.message
        assert caught.value.extensions == {"code": "BAD_USER_INPUT"}
        assert PreviewParent.objects.filter(pk=parent.pk).exists()


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

    with model_tables((PreviewNullableParent, PreviewNullableChild)):
        parent = PreviewNullableParent.objects.create(name="parent")
        PreviewNullableChild.objects.create(parent=parent)
        PreviewNullableChild.objects.create(parent=parent)

        preview = DeletePreview.from_instance(parent)

        assert preview.updated[0].count == 2
        assert not preview.has_blockers


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

    with model_tables((PreviewRestrictedParent, PreviewRestrictedChild)):
        parent = PreviewRestrictedParent.objects.create(name="parent")
        PreviewRestrictedChild.objects.create(parent=parent)

        preview = DeletePreview.from_instance(parent)

        assert preview.has_blockers
        assert preview.blocked[0].count == 1
        with pytest.raises(GraphQLError) as caught:
            AngeeHasuraWriteBackend(PreviewRestrictedParent).delete(cast(Any, None), str(parent.pk))
        assert str(PreviewRestrictedChild._meta.verbose_name_plural) in caught.value.message
        assert caught.value.extensions == {"code": "BAD_USER_INPUT"}
        assert PreviewRestrictedParent.objects.filter(pk=parent.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_deletion_preview_counts_fast_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
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

    with model_tables((PreviewCascadeParent, PreviewCascadeChild)):
        parent = PreviewCascadeParent.objects.create(name="parent")
        PreviewCascadeChild.objects.create(parent=parent)

        def collect_with_fast_delete(collector: Collector, rows: list[models.Model], **_: object) -> None:
            collector.data[PreviewCascadeParent].update(rows)
            collector.fast_deletes.append(PreviewCascadeChild.objects.filter(parent__in=rows))

        # A fully composed environment has global lifecycle receivers, so Django
        # deliberately avoids its fast-delete optimization. Supply the collector
        # shape directly: this test owns GraphQL's interpretation of that shape,
        # not Django's signal-dependent optimization decision.
        monkeypatch.setattr(Collector, "collect", collect_with_fast_delete)

        preview = DeletePreview.from_instance(parent)

        deleted = {group.label: group.count for group in preview.deleted}
        parent_label = str(PreviewCascadeParent._meta.verbose_name_plural)
        child_label = str(PreviewCascadeChild._meta.verbose_name_plural)
        assert deleted[parent_label] == 1
        assert deleted[child_label] == 1


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
def test_deletion_preview_hides_rebac_child_leaves_without_read_access(composed_tables: None) -> None:
    """Actor-scoped previews do not expose related resource row labels or ids."""

    del composed_tables

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
            rebac_resource_type = "auth/group"

        def __str__(self) -> str:
            """Return the child name for preview display labels."""

            return self.name

    with model_tables((PreviewScopedParent, PreviewScopedChild)):
        with system_context(reason="test-setup"):
            parent = PreviewScopedParent.objects.create(name="parent")
            child = PreviewScopedChild.objects.create(parent=parent, name="Hidden child")

        with actor_context(SubjectRef.of("auth/user", "reader")):
            preview = DeletePreview.from_instance(parent)

        child_group = next(group for group in preview.root.children if group.label == "preview scoped childs")
        assert child_group.object_label == "1 preview scoped childs"
        assert child_group.children[0].object_label == "1 more records"
        assert child_group.children[0].object_id is None

        assert "Hidden child" not in _tree_object_labels(preview.root)
        assert str(child.pk) not in _tree_object_ids(child_group)


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
def test_delete_user_removes_denormalized_subject_relationships() -> None:
    """Deleting an ``auth/user`` row removes tuples where it is the subject."""

    class DeletedUserSubject(RebacMixin):
        """Concrete ``auth/user`` row for delete-GC coverage."""

        label = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test user row."""

            app_label = "auth"
            rebac_resource_type = "auth/user"

    with model_tables((DeletedUserSubject,)):
        with override_settings(REBAC_LOCAL_BACKEND_STORAGE="denormalized"):
            with system_context(reason="test.subject-relationship-gc.setup"):
                user = DeletedUserSubject.objects.create(label="subject")
            subject = to_object_ref(user)
            active_relationship_model().objects.create(
                resource_type="angee/role",
                resource_id="auditor",
                relation="member",
                subject_type=subject.resource_type,
                subject_id=subject.resource_id,
                caveat_context={},
            )

            with system_context(reason="test.subject-relationship-gc"):
                user.delete()

            assert not active_relationship_model().objects.filter(
                subject_type=subject.resource_type,
                subject_id=subject.resource_id,
            ).exists()


def _tree_object_labels(node: DeletePreviewNode) -> tuple[str, ...]:
    """Return every object label in a preview tree."""

    return (node.object_label, *(label for child in node.children for label in _tree_object_labels(child)))


def _tree_object_ids(node: DeletePreviewNode) -> tuple[str, ...]:
    """Return every concrete object id in a preview tree."""

    own = () if node.object_id is None else (node.object_id,)
    return (*own, *(object_id for child in node.children for object_id in _tree_object_ids(child)))


@pytest.mark.django_db
def test_delete_by_public_id_preserves_blocked_and_removes_unblocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked deletes leave rows present; unblocked deletes remove them."""

    blocked = Group.objects.create(name="blocked")
    removable = Group.objects.create(name="removable")
    previews = iter(
        (
            DeletePreview(
                total_deleted_count=1,
                deleted=[],
                updated=[],
                blocked=[DeletePreviewGroup(label="groups", count=1)],
                has_blockers=True,
            ),
            DeletePreview(
                total_deleted_count=1,
                deleted=[DeletePreviewGroup(label="groups", count=1)],
                updated=[],
                blocked=[],
                has_blockers=False,
            ),
        )
    )

    def preview_for(cls: type[DeletePreview], instance: Group) -> DeletePreview:
        del cls, instance
        return next(previews)

    monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_for))

    blocked_preview = delete_by_public_id(Group, str(blocked.pk), confirm=True)
    removable_preview = delete_by_public_id(Group, str(removable.pk), confirm=True)

    assert blocked_preview.has_blockers
    assert Group.objects.filter(pk=blocked.pk).exists()
    assert not removable_preview.has_blockers
    assert not Group.objects.filter(pk=removable.pk).exists()


@pytest.mark.django_db
def test_delete_by_public_id_defaults_to_preview_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``confirm`` previews the cascade but leaves the row intact."""

    group = Group.objects.create(name="preview-only")

    def preview_for(cls: type[DeletePreview], instance: Group) -> DeletePreview:
        del cls, instance
        return DeletePreview(
            total_deleted_count=1,
            deleted=[DeletePreviewGroup(label="groups", count=1)],
            updated=[],
            blocked=[],
            has_blockers=False,
        )

    monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_for))

    preview = delete_by_public_id(Group, str(group.pk))

    assert not preview.has_blockers
    assert Group.objects.filter(pk=group.pk).exists()


@pytest.mark.django_db
def test_delete_by_public_id_previews_and_deletes_inside_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview and delete run inside one database transaction."""

    group = Group.objects.create(name="transactional")
    active = False
    entered = False

    class Atomic:
        """Small transaction context used to observe resolver boundaries."""

        def __enter__(self) -> None:
            nonlocal active, entered
            active = True
            entered = True

        def __exit__(self, *exc: object) -> None:
            nonlocal active
            active = False

    def atomic(*args: object, **kwargs: object) -> Atomic:
        """Return a transaction context that records entry."""

        del args, kwargs
        return Atomic()

    def preview_for(cls: type[DeletePreview], instance: Group) -> DeletePreview:
        del cls, instance
        assert active
        return DeletePreview(
            total_deleted_count=1,
            deleted=[DeletePreviewGroup(label="groups", count=1)],
            updated=[],
            blocked=[],
            has_blockers=False,
        )

    monkeypatch.setattr(transaction, "atomic", atomic)
    monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_for))

    delete_by_public_id(Group, str(group.pk), confirm=True)

    assert entered
    assert not active
    assert not Group.objects.filter(pk=group.pk).exists()


@pytest.mark.django_db
def test_delete_by_public_id_elevates_lookup_and_runs_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin deletes share one elevated preview/delete helper with before-delete hooks."""

    group = Group.objects.create(name="admin-delete")
    reasons: list[str | None] = []
    hooked: list[int] = []

    class Context:
        """Small context manager that records the elevated reason."""

        def __init__(self, reason: str | None) -> None:
            """Store the reason passed to ``system_context``."""

            self.reason = reason

        def __enter__(self) -> None:
            reasons.append(self.reason)

        def __exit__(self, *exc: object) -> None:
            return None

    def recording_system_context(*, reason: str | None = None) -> Context:
        """Return a recording system context."""

        return Context(reason)

    def preview_for(cls: type[DeletePreview], instance: Group) -> DeletePreview:
        del cls
        assert instance == group
        return DeletePreview(
            total_deleted_count=1,
            deleted=[DeletePreviewGroup(label="groups", count=1)],
            updated=[],
            blocked=[],
            has_blockers=False,
        )

    monkeypatch.setattr(deletion_module, "system_context", recording_system_context)
    monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_for))

    preview = delete_by_public_id(
        Group,
        str(group.pk),
        confirm=True,
        reason="iam.graphql.user.delete",
        before_delete=lambda row: hooked.append(cast(Group, row).pk),
    )

    assert not preview.has_blockers
    assert reasons == ["iam.graphql.user.delete"]
    assert hooked == [group.pk]
    assert not Group.objects.filter(pk=group.pk).exists()


@pytest.mark.django_db
def test_delete_by_public_id_skips_hook_when_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked delete previews do not run destructive hooks."""

    group = Group.objects.create(name="blocked-admin-delete")

    def preview_for(cls: type[DeletePreview], instance: Group) -> DeletePreview:
        del cls, instance
        return DeletePreview(
            total_deleted_count=1,
            deleted=[],
            updated=[],
            blocked=[DeletePreviewGroup(label="groups", count=1)],
            has_blockers=True,
        )

    monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_for))

    preview = delete_by_public_id(
        Group,
        str(group.pk),
        confirm=True,
        before_delete=lambda row: row.delete(),
    )

    assert preview.has_blockers
    assert Group.objects.filter(pk=group.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_by_public_id_returns_blocked_preview_for_late_protected_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocker appearing after preview returns the same blocked-preview shape."""

    class DeleteRaceParent(models.Model):
        """Parent model targeted by a late protected child."""

        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class DeleteRaceChild(models.Model):
        """Child model that blocks parent deletion."""

        parent = models.ForeignKey(DeleteRaceParent, on_delete=models.PROTECT)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((DeleteRaceParent, DeleteRaceChild)):
        parent = DeleteRaceParent.objects.create(name="race")
        from_instance = DeletePreview.from_instance
        first_preview = True

        def preview_then_insert(cls: type[DeletePreview], instance: models.Model) -> DeletePreview:
            """Simulate a concurrent committed FK insertion outside the delete savepoint."""

            nonlocal first_preview
            preview = from_instance(instance)
            if first_preview:
                first_preview = False
                DeleteRaceChild.objects.create(parent=cast(DeleteRaceParent, instance))
            return preview

        monkeypatch.setattr(DeletePreview, "from_instance", classmethod(preview_then_insert))

        preview = delete_by_public_id(
            DeleteRaceParent,
            str(parent.pk),
            confirm=True,
        )

        assert preview.has_blockers
        assert preview.blocked[0].count == 1
        assert DeleteRaceParent.objects.filter(pk=parent.pk).exists()
        assert DeleteRaceChild.objects.filter(parent=parent).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("refused", (False, True))
def test_delete_receiver_refusal_rolls_back_before_delete_writes_and_on_commit(refused: bool) -> None:
    """Confirm hooks commit only when Django's authoritative receiver allows deletion."""

    group = Group.objects.create(name="hook-target")
    committed: list[str] = []

    def before_delete(instance: models.Model) -> None:
        Group.objects.create(name="hook-side-effect")
        transaction.on_commit(lambda: committed.append("committed"))

    def refuse_delete(sender: Any, instance: Group, **kwargs: Any) -> None:
        if refused:
            raise ValidationError("Release the group first.")

    pre_delete.connect(refuse_delete, sender=Group)
    try:
        preview = delete_by_public_id(Group, str(group.pk), confirm=True, before_delete=before_delete)
    finally:
        pre_delete.disconnect(refuse_delete, sender=Group)

    assert preview.has_blockers is refused
    assert preview.refusals == (["Release the group first."] if refused else [])
    assert Group.objects.filter(pk=group.pk).exists() is refused
    assert Group.objects.filter(name="hook-side-effect").exists() is not refused
    assert committed == ([] if refused else ["committed"])


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
@pytest.mark.parametrize("retaining_rows", (False, True))
def test_delete_preview_from_counts_reports_target_refusal_with_or_without_fk_blockers(
    retaining_rows: bool,
) -> None:
    """Count-based purges project the target's rule alongside retaining-row groups."""

    class PurgeTarget(AngeeModel):
        """Counted purge root with a model-owned refusal."""

        class Meta:
            """Register the isolated target."""

            app_label = "auth"

        def delete_blocker(self) -> str | None:
            """Expose the purge root's public-safe refusal."""

            return "Disconnect the target first."

    def refuse_delete(sender: Any, instance: PurgeTarget, **kwargs: Any) -> None:
        raise ValidationError(instance.delete_blocker())

    pre_delete.connect(refuse_delete, sender=PurgeTarget)
    try:
        with model_tables((PurgeTarget,)), system_context(reason="tests.deletion.purge"):
            target = PurgeTarget.objects.create()
            retaining_group = Group.objects.create(name="retaining-group")
            preview = DeletePreview.from_counts(
                target, {Group: 3}, blockers=[retaining_group] if retaining_rows else [],
            )
    finally:
        pre_delete.disconnect(refuse_delete, sender=PurgeTarget)

    assert preview.has_blockers
    assert preview.refusals == ["Disconnect the target first."]
    assert [(group.label, group.count) for group in preview.blocked] == ([("groups", 1)] if retaining_rows else [])
    with pytest.raises(GraphQLError, match="Disconnect the target first"):
        preview.require_no_blockers()
