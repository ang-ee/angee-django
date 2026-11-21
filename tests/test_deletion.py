"""Tests for GraphQL deletion preview objects and the public-id delete helper."""

from __future__ import annotations

from typing import Any, Self, cast

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, models, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.db.models.signals import pre_delete
from django.test.utils import CaptureQueriesContext, isolate_apps
from graphql import GraphQLError
from rebac import PermissionDenied, RebacMixin, SubjectRef, actor_context, system_context

import angee.graphql.deletion as deletion_module
from angee.base.models.base import AngeeModel
from angee.base.models.refs import delete_record_refs
from angee.base.signals import starts_delete
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.deletion import (
    DeletePreview,
    DeletePreviewGroup,
    DeletePreviewNode,
    delete_by_public_id,
)
from angee.graphql.ids import RECORD_NOT_FOUND_MESSAGE
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, result_data
from angee.iam import signals as iam_signals
from angee.integrate.states import StreamDirection, StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Channel
from angee.projects.testing.models import Milestone, Project, Task
from tests.conftest import addon_schema, create_platform_admin, current_stream, make_integration
from tests.mtidemo.models import MtiChild, MtiParent
from tests.scopedemo.models import ProjectionDoc
from tests.tables import model_tables


@strawberry_django.type(Group)
class DeleteGroupType:
    """Plain Django rows exercise the generated resource deletion contract."""

    id: strawberry.ID
    name: strawberry.auto


@strawberry_django.type(Project)
class DeleteProjectType(AngeeNode):
    """An existing typed record whose milestones exercise real cascades."""

    title: strawberry.auto


@strawberry_django.type(MtiChild)
class DeleteChildType(AngeeNode):
    """A multi-table row whose record links use its canonical parent identity."""

    title: strawberry.auto


@strawberry_django.type(Task)
class DeleteTaskType(AngeeNode):
    """A trashable record exercising permanent deletion with a retained link."""

    title: strawberry.auto


LINKED_DELETE = """mutation($id: ID!, $confirm: Boolean!) {
  linked_projects_delete(id: $id, confirm: $confirm) {
    has_blockers blocked { label count } refusals
  }
}"""


@pytest.fixture
def linked_deletion(db):
    """Generate a real resource root alongside the existing record-link fixtures."""

    admin = create_platform_admin("linked-delete-admin", password=None)
    with system_context(reason="tests.deletion.links"):
        bridge = make_integration("linked-delete", model=Channel)
        stream = current_stream(bridge, "projects", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PULL)
    resources = [
        hasura_model_resource(
            row_type, model=model, name=name,
            filterable=["title"], sortable=["title"], aggregatable=["title"], insert=False, update=False,
        )
        for row_type, model, name in (
            (DeleteProjectType, Project, "linked_projects"),
            (DeleteChildType, MtiChild, "linked_children"),
            (DeleteTaskType, Task, "linked_tasks"),
        )
    ]
    schema = addon_schema({"public": {
        "query": [resource.query for resource in resources],
        "mutation": [resource.mutation for resource in resources],
        "types": [row_type for resource in resources for row_type in resource.types],
    }}, "public")
    return schema, admin, stream


@pytest.mark.parametrize("cascaded", (False, True))
def test_generated_delete_reports_live_record_links_and_deletes_after_release(linked_deletion, cascaded):
    """Preview and confirm judge both the root and a held cascade through the edge owner."""

    schema, admin, stream = linked_deletion
    with system_context(reason="tests.deletion.linked_target"):
        project = Project.objects.create(title="Held project")
        milestone = Milestone.objects.create(project=project, name="Held milestone")
        target = milestone if cascaded else project
        link = RecordLink.objects.observe(stream, "held-target", target=target)
    variables = {"id": project.public_id, "confirm": False}
    preview = result_data(execute_schema(schema, LINKED_DELETE, variables, user=admin))["linked_projects_delete"]
    assert preview["has_blockers"]
    assert preview["blocked"] == [{"label": str(RecordLink._meta.verbose_name_plural), "count": 1}]
    confirmed = result_data(execute_schema(schema, LINKED_DELETE, {**variables, "confirm": True}, user=admin))
    assert confirmed["linked_projects_delete"] == preview
    assert Project._base_manager.filter(pk=project.pk).exists()
    assert Milestone._base_manager.filter(pk=milestone.pk).exists()
    assert RecordLink._base_manager.get(pk=link.pk).target_object_id == str(target.pk)

    RecordLink.objects.observe(stream, link.external_key, target=None)
    released = result_data(execute_schema(schema, LINKED_DELETE, {**variables, "confirm": True}, user=admin))
    assert not released["linked_projects_delete"]["has_blockers"]
    assert not Project._base_manager.filter(pk=project.pk).exists()
    assert not Milestone._base_manager.filter(pk=milestone.pk).exists()


def test_generated_delete_sequence_keeps_held_id_and_deletes_released_id(linked_deletion):
    """The selection runner can continue independently after a per-row refusal."""

    schema, admin, stream = linked_deletion
    with system_context(reason="tests.deletion.linked_selection"):
        held = Project.objects.create(title="Held")
        released = Project.objects.create(title="Released")
        RecordLink.objects.observe(stream, "held", target=held)
        RecordLink.objects.observe(stream, "released", target=released)
        RecordLink.objects.observe(stream, "released", target=None)
    for target, blocked in ((held, True), (released, False)):
        for confirm in (False, True):
            preview = result_data(execute_schema(
                schema, LINKED_DELETE, {"id": target.public_id, "confirm": confirm}, user=admin,
            ))["linked_projects_delete"]
            assert preview["has_blockers"] is blocked
    assert Project._base_manager.filter(pk=held.pk).exists()
    assert not Project._base_manager.filter(pk=released.pk).exists()


def test_generated_delete_preview_never_releases_push_links(linked_deletion):
    """A non-blocking edge is admitted by its live filter, but teardown waits for confirm."""

    schema, admin, pull = linked_deletion
    with system_context(reason="tests.deletion.push_link"):
        stream = current_stream(pull.integration.concrete_capability(), "pushed-projects",
                                kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PUSH)
        project = Project.objects.create(title="Published project")
        link = RecordLink.objects.observe(stream, "pushed", target=project)
    variables = {"id": project.public_id, "confirm": False}
    preview = result_data(execute_schema(schema, LINKED_DELETE, variables, user=admin))["linked_projects_delete"]
    assert not preview["has_blockers"]
    assert RecordLink._base_manager.get(pk=link.pk).target_object_id == str(project.pk)
    confirmed = result_data(execute_schema(schema, LINKED_DELETE, {**variables, "confirm": True}, user=admin))
    assert not confirmed["linked_projects_delete"]["has_blockers"]
    assert not Project._base_manager.filter(pk=project.pk).exists()
    assert RecordLink._base_manager.get(pk=link.pk).target_object_id is None


def test_generated_delete_returns_a_late_reference_refusal_after_rollback(linked_deletion):
    """A receiver-time refusal remains readable even if its link rolls back with the delete."""

    schema, admin, stream = linked_deletion
    with system_context(reason="tests.deletion.late_reference"):
        project = Project.objects.create(title="Late hold")

    def hold_during_delete(sender, instance, using, **kwargs):
        RecordLink.objects.observe(stream, "late", target=instance)
        delete_record_refs(instance, using)

    pre_delete.connect(hold_during_delete, sender=Project)
    try:
        preview = result_data(execute_schema(
            schema, LINKED_DELETE, {"id": project.public_id, "confirm": True}, user=admin,
        ))["linked_projects_delete"]
    finally:
        pre_delete.disconnect(hold_during_delete, sender=Project)
    assert preview["has_blockers"]
    assert preview["blocked"] == [{"label": str(RecordLink._meta.verbose_name_plural), "count": 1}]
    assert Project._base_manager.filter(pk=project.pk).exists()
    assert not RecordLink._base_manager.filter(stream=stream, external_key="late").exists()


@pytest.mark.parametrize("error_type", (ProtectedError, RestrictedError))
def test_generated_delete_reports_a_late_refusal_without_retaining_rows(linked_deletion, error_type):
    schema, admin, _stream = linked_deletion
    with system_context(reason="tests.deletion.empty_refusal"):
        project = Project.objects.create(title="Empty late refusal")

    def refuse(sender, instance, **kwargs):
        raise error_type("Internal receiver detail must not be exposed.", [])

    pre_delete.connect(refuse, sender=Project)
    try:
        preview = result_data(execute_schema(
            schema, LINKED_DELETE, {"id": project.public_id, "confirm": True}, user=admin,
        ))["linked_projects_delete"]
    finally:
        pre_delete.disconnect(refuse, sender=Project)
    assert preview == {
        "has_blockers": True, "blocked": [],
        "refusals": ["Deletion was refused by a related record; nothing was deleted."],
    }
    assert Project._base_manager.filter(pk=project.pk).exists()


def test_delete_preview_batches_holds_across_a_large_cascade(linked_deletion):
    """Growing one collected type changes its IN list, not its query count."""

    _schema, admin, stream = linked_deletion
    counts = []
    for size in (2, 250):
        with system_context(reason="tests.deletion.large_cascade"):
            root = ProjectionDoc.objects.create(title=f"Cascade {size}")
            children = ProjectionDoc.objects.bulk_create([
                ProjectionDoc(parent=root, title=f"Child {index}") for index in range(size)
            ])
            RecordLink.objects.observe(stream, f"held-cascade-{size}", target=children[-1])
        with actor_context(admin), CaptureQueriesContext(connection) as queries:
            preview = DeletePreview.from_instance(root, actor=admin)
        assert preview.has_blockers and preview.blocked[0].count == 1
        hold_reads = [query for query in queries if "_angee_holds_deleted_target" in query["sql"]]
        assert len(hold_reads) == 1  # The root and every child share one target type.
        assert len(queries) <= 30
        counts.append(len(queries))
        assert ProjectionDoc._base_manager.filter(parent=root).count() == size
    assert counts[1] <= counts[0]


def test_generated_delete_reports_retained_provenance_statements(linked_deletion):
    """Source statements hold their record even without a live integration link."""

    schema, admin, _stream = linked_deletion
    with system_context(reason="tests.deletion.statements"):
        project = Project.objects.create(title="Retained project")
        statement = project.statements_for("identity").set(
            aspect="identity", values={"title": "Retained project", "body": ""}, asserted_by=admin,
        )
    for confirm in (False, True):
        preview = result_data(execute_schema(
            schema, LINKED_DELETE, {"id": project.public_id, "confirm": confirm}, user=admin,
        ))["linked_projects_delete"]
        assert preview["has_blockers"]
        assert any("source statements" in message for message in preview["refusals"])
        assert {"label": str(type(statement)._meta.verbose_name_plural), "count": 1} in preview["blocked"]
        assert Project._base_manager.filter(pk=project.pk).exists()
        assert type(statement)._base_manager.filter(pk=statement.pk).exists()


def test_generated_delete_preserves_history_without_turning_snapshots_into_holds(linked_deletion):
    """Provenance explicitly exempts historical snapshots from blocks_target_delete."""

    schema, admin, _stream = linked_deletion
    with system_context(reason="tests.deletion.history"):
        project = Project.objects.create(title="Retained snapshot")
    pk = project.pk
    history = Project.history.model
    assert history._base_manager.filter(id=pk).exists()
    assert not history.blocks_target_delete(Project)
    for confirm in (False, True):
        preview = result_data(execute_schema(
            schema, LINKED_DELETE, {"id": project.public_id, "confirm": confirm}, user=admin,
        ))["linked_projects_delete"]
        assert not preview["has_blockers"]
    assert not Project._base_manager.filter(pk=pk).exists()
    assert history._base_manager.filter(id=pk).exists()


@pytest.mark.parametrize(("model", "root"), [(MtiChild, "linked_children"), (Task, "linked_tasks")])
def test_generated_delete_keeps_a_held_child_or_trashed_record(linked_deletion, model, root):
    schema, admin, stream = linked_deletion
    with system_context(reason="tests.deletion.held_shapes"):
        target = model.objects.create(title="Held record")
        if isinstance(target, Task):
            target.trash(reason="Ready for permanent deletion")
            assert Task._base_manager.get(pk=target.pk).is_trashed
        link = RecordLink.objects.observe(stream, root, target=target)
    document = LINKED_DELETE.replace("linked_projects", root)
    for confirm in (False, True):
        preview = result_data(execute_schema(
            schema, document, {"id": target.public_id, "confirm": confirm}, user=admin,
        ))[f"{root}_delete"]
        assert preview["has_blockers"]
        assert preview["blocked"] == [{"label": str(RecordLink._meta.verbose_name_plural), "count": 1}]
        assert model._base_manager.filter(pk=target.pk).exists()
        if model is MtiChild:
            assert MtiParent._base_manager.filter(pk=target.pk).exists()
    RecordLink.objects.observe(stream, link.external_key, target=None)
    released = result_data(execute_schema(
        schema, document, {"id": target.public_id, "confirm": True}, user=admin,
    ))[f"{root}_delete"]
    assert not released["has_blockers"]
    assert not model._base_manager.filter(pk=target.pk).exists()
    if model is MtiChild:
        assert not MtiParent._base_manager.filter(pk=target.pk).exists()


@pytest.mark.parametrize("delete", (False, True))
def test_resource_delete_flag_controls_both_roots_and_preview_metadata(delete: bool) -> None:
    """Opting out of deletion emits neither delete root nor preview capability."""

    resource = hasura_model_resource(
        DeleteGroupType, model=Group, name="delete_groups",
        filterable=["id", "name"], sortable=["name"], aggregatable=["id"],
        insert=False, update=True, delete=delete, public_id_field="id",
    )
    schema = addon_schema({"public": {
        "query": [resource.query], "mutation": [resource.mutation], "types": resource.types,
    }}, "public")
    fields = schema._schema.mutation_type.fields if schema._schema.mutation_type else {}
    assert "update_delete_groups_by_pk" in fields
    assert ("delete_groups_delete" in fields) is delete
    assert ("delete_delete_groups_by_pk" in fields) is delete
    metadata = schema.angee_resources[0]
    assert metadata.roots.delete_name == ("delete_delete_groups_by_pk" if delete else None)
    assert metadata.roots.delete_preview_name == ("delete_groups_delete" if delete else None)
    assert metadata.type_names.delete_payload == ("DeletePreview" if delete else None)
    assert ("deletePreview" in metadata.capabilities) is delete


@pytest.mark.parametrize("mutation_schemas", (("public",), ("public", "console")))
def test_generated_preview_follows_delete_root_in_named_schemas(mutation_schemas: tuple[str, ...]) -> None:
    """A public resource exposes both delete roots only in its mutation buckets."""

    resource = hasura_model_resource(
        DeleteGroupType, model=Group, name="named_groups",
        filterable=["id", "name"], sortable=["name"], aggregatable=["id"],
        insert=False, update=False, public_id_field="id",
    )
    buckets = {
        name: {
            "query": [resource.query],
            "mutation": [resource.mutation] if name in mutation_schemas else [],
            "types": resource.types,
        }
        for name in ("public", "console")
    }
    for name in buckets:
        schema = addon_schema(buckets, name)
        fields = schema._schema.mutation_type.fields if schema._schema.mutation_type else {}
        exposed = name in mutation_schemas
        assert ("delete_named_groups_by_pk" in fields) is exposed
        assert ("named_groups_delete" in fields) is exposed
        metadata = schema.angee_resources[0]
        assert metadata.roots.delete_name == ("delete_named_groups_by_pk" if exposed else None)
        assert metadata.roots.delete_preview_name == ("named_groups_delete" if exposed else None)
        assert ("delete" in metadata.capabilities) is exposed
        assert ("deletePreview" in metadata.capabilities) is exposed
        wire = metadata.as_wire(schema_name=name)
        assert wire["schemaName"] == name
        assert wire["roots"]["delete"] == metadata.roots.delete_name
        assert wire["roots"]["deletePreview"] == metadata.roots.delete_preview_name


@pytest.mark.parametrize("backend_kind", ("missing", "override", "inherited_override"))
def test_resource_rejects_backends_without_a_matching_delete_preview(backend_kind: str) -> None:
    """A custom delete may never silently inherit the unrelated default preview."""

    class NativeBackend:
        """A native delete-only backend lacks the Angee preview extension."""

        def delete(self, info: strawberry.Info, pk: str) -> None:
            return None

    class OverrideBackend(AngeeHasuraWriteBackend):
        """A custom delete has not yet migrated into delete_preview."""

        def delete(self, info: strawberry.Info, pk: str) -> None:
            return None

    class InheritedOverrideBackend(OverrideBackend):
        """The incomplete override remains incomplete through inheritance."""

    backend = {
        "missing": NativeBackend(),
        "override": OverrideBackend(Group),
        "inherited_override": InheritedOverrideBackend(Group),
    }[backend_kind]
    with pytest.raises(ImproperlyConfigured, match=r"auth.Group.*implement delete_preview"):
        hasura_model_resource(
            DeleteGroupType, model=Group, name="invalid_delete_groups",
            filterable=["id", "name"], sortable=["name"], aggregatable=["id"],
            insert=False, update=False, write_backend=cast(Any, backend), public_id_field="id",
        )


@pytest.mark.django_db
@pytest.mark.parametrize("native_delete", (False, True))
def test_generated_deletes_share_custom_preview_and_write_scope(native_delete: bool) -> None:
    """Both roots honor custom preview behavior and the backend's target restriction."""

    class ScopedBackend(AngeeHasuraWriteBackend):
        """Restrict the surface and expose its own refusal in both envelopes."""

        def write_target_queryset(self) -> models.QuerySet[Any]:
            return super().write_target_queryset().exclude(name="hidden")

        def delete_preview(self, info: strawberry.Info, pk: str, *, confirm: bool) -> DeletePreview:
            preview = super().delete_preview(info, pk, confirm=False)
            if self.write_target_queryset().filter(pk=pk, name="retained").exists():
                preview.has_blockers = True
                preview.refusals = ["Retained by this surface."]
                return preview
            return super().delete_preview(info, pk, confirm=confirm)

    resource = hasura_model_resource(
        DeleteGroupType, model=Group, name="scoped_groups",
        filterable=["id", "name"], sortable=["name"], aggregatable=["id"],
        insert=False, update=False, write_backend=ScopedBackend(Group), public_id_field="id",
    )
    schema = addon_schema({"public": {
        "query": [resource.query], "mutation": [resource.mutation], "types": resource.types,
    }}, "public")
    root = "delete_scoped_groups_by_pk" if native_delete else "scoped_groups_delete"
    document = (
        "mutation($id: String!) { delete_scoped_groups_by_pk(id: $id) { id } }"
        if native_delete else
        "mutation($id: ID!) { scoped_groups_delete(id: $id, confirm: true) { has_blockers refusals } }"
    )
    hidden, retained, removable = (Group.objects.create(name=name) for name in ("hidden", "retained", "removable"))
    denied = execute_schema(schema, document, {"id": str(hidden.pk)})
    assert denied.errors is not None
    assert Group.objects.filter(pk=hidden.pk).exists()
    refused = execute_schema(schema, document, {"id": str(retained.pk)})
    if native_delete:
        assert refused.errors is not None
        assert refused.errors[0].message == "Retained by this surface."
    else:
        assert result_data(refused)[root] == {"has_blockers": True, "refusals": ["Retained by this surface."]}
    assert Group.objects.filter(pk=retained.pk).exists()
    deleted = result_data(execute_schema(schema, document, {"id": str(removable.pk)}))[root]
    if native_delete:
        assert deleted["id"] == str(removable.pk)
    else:
        assert deleted["has_blockers"] is False
    assert not Group.objects.filter(pk=removable.pk).exists()


@pytest.fixture(autouse=True)
def restore_sign_in_receivers():
    """Temporary auth registries run ready hooks against Django's shared signal."""

    yield
    iam_signals.connect()


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

        def delete_blocker(self, *, cascaded: bool = False) -> str | None:
            """Return the target's public-safe deletion refusal."""

            del cascaded
            return "Release the parent first." if self.blocked else None

    class PolicyChild(AngeeModel):
        """Cascade row with an optional model-owned refusal."""

        parent = models.ForeignKey(PolicyParent, on_delete=models.CASCADE)
        blocked = models.BooleanField(default=False)

        class Meta:
            """Django model options for the test child."""

            app_label = "auth"

        def delete_blocker(self, *, cascaded: bool = False) -> str | None:
            """Return the child's public-safe deletion refusal, wherever the delete started."""

            del cascaded
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
def test_deletion_preview_judges_its_target_as_the_start_and_every_collected_row_as_cascaded() -> None:
    """A refusal for deletes started at a row is not one a cascade to it shows (``delete_blocker(cascaded=)``)."""

    class StartedParent(AngeeModel):
        """Deletion target without a refusal of its own."""

        class Meta:
            """Register the isolated target."""

            app_label = "auth"

    class StartedChild(AngeeModel):
        """A row refusing only deletes started at it, as a mirrored calendar does."""

        parent = models.ForeignKey(StartedParent, on_delete=models.CASCADE)

        class Meta:
            """Register the isolated child."""

            app_label = "auth"

        def delete_blocker(self, *, cascaded: bool = False) -> str | None:
            """Refuse a delete started here; go with the row a cascade started from."""

            return None if cascaded else "Delete the parent instead."

    with model_tables((StartedParent, StartedChild)), system_context(reason="tests.deletion.started"):
        parent = StartedParent.objects.create()
        child = StartedChild.objects.create(parent=parent)

        assert DeletePreview.from_instance(child).refusals == ["Delete the parent instead."]
        cascade = DeletePreview.from_instance(parent)
        assert (cascade.refusals, cascade.has_blockers, cascade.total_deleted_count) == ([], False, 2)


def test_a_delete_starts_at_its_origin_row_at_its_querysets_model_or_without_an_origin_at_every_row() -> None:
    """``starts_delete`` is where a row sits in its delete, for ``pre_delete`` receivers and the preview alike."""

    child = MtiChild(pk=7)
    parent, other = MtiParent(pk=7), MtiChild(pk=8)
    assert starts_delete(child, child) and starts_delete(MtiChild(pk=7), child)
    # An instance delete's multi-table parent row, and any other row, only cascade from its start.
    assert not starts_delete(parent, child) and not starts_delete(other, child)
    # A queryset delete starts at every row of its model: the collector does not say which it selected.
    rows = MtiChild._base_manager.none()
    assert starts_delete(other, rows) and not starts_delete(parent, rows)
    assert starts_delete(parent, None)


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth")
def test_deletion_preview_error_includes_model_refusals_and_fk_blockers() -> None:
    """Neither a model refusal nor a protected relation hides the other in errors."""

    class PolicyParent(AngeeModel):
        """Deletion target refused independently of its protected child."""

        class Meta:
            """Register the isolated target."""

            app_label = "auth"

        def delete_blocker(self, *, cascaded: bool = False) -> str | None:
            """Return the model's readable refusal."""

            del cascaded
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

        def delete_blocker(self, *, cascaded: bool = False) -> str | None:
            """Expose the purge root's public-safe refusal."""

            del cascaded
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
