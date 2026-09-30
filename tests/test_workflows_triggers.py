"""Level-triggered admission through native source, filter and permission owners."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
import strawberry_django
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.db.models.functions import Now, Upper
from django.db.models.signals import post_save
from rebac import RelationshipTuple, actor_context, current_actor, system_context, to_object_ref, to_subject_ref
from rebac.actors import is_sudo
from rebac.models import active_relationship_model
from rebac.relationships import delete_relationship

from angee.base.impl import impl_choices_enum
from angee.base.scoping import system_queryset
from angee.graphql.data import hasura_model_resource
from angee.graphql.deletion import delete_by_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.knowledge import schema as knowledge_schema
from angee.workflows import schema as workflow_schema
from angee.workflows import triggers
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, trigger_source
from angee.workflows.testing.models import (
    StepAttempt,
    StepWatch,
    Trigger,
    TriggerEvent,
    WorkflowRun,
    WorkflowRunEvidence,
)
from tests.conftest import Page, Vault, addon_schema, create_user, execute_schema, make_addon, result_data, vault_for
from tests.mtidemo.models import MtiParent
from tests.workflow_steps import Value, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def trigger_resource_schema(monkeypatch):
    """Bare tests install just their real, composed resource owner, restoring it afterward."""
    owner = GraphQLSchemas([make_addon(schemas=knowledge_schema.schemas)])
    monkeypatch.setattr(GraphQLSchemas, "_discovered", owner)
    with trigger_source(Vault):
        yield owner


@pytest.fixture
def trigger_setup(execution, trigger_resource_schema):
    """Use one public workflow and a real actor-owned record for admission."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="record-trigger", actor=actor, subject_model="knowledge.vault")
    record = vault_for(actor, name="Ready")
    with actor_context(actor):
        trigger = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label="knowledge.vault",
            condition={"name": {"_eq": "Ready"}},
        )
    return actor, workflow, record, trigger


def capture(record):
    """Bulk and signal writers share the same durable owner."""
    triggers.RecordChanged.dispatch(type(record), record)


def restrict_source_grant_to_record(monkeypatch, record):
    """Let the record owner, but not an unrelated workflow editor, delegate it."""
    def targets(cls, trigger):
        return (triggers.TriggerGrantTarget(
            triggers.ObjectRef("knowledge/role", "vault_viewer"), "member", "write", to_object_ref(record),
        ),)

    monkeypatch.setattr(Vault, "record_changed_grant_targets", classmethod(targets))


def test_record_changed_system_check_requires_grant_targets(monkeypatch):
    """The mixin alone opts in, and Django checks its required declaration."""
    missing = type("MissingGrantTargets", (triggers.RecordChangedOptIn,), {
        "_meta": SimpleNamespace(label="example.MissingGrantTargets"),
    })
    monkeypatch.setattr(triggers.apps, "get_models", lambda: [missing])
    errors = triggers.check_record_changed_models()
    assert len(errors) == 1 and errors[0].id == "workflows.E001"
    assert "record_changed_grant_targets" in errors[0].msg


def test_record_changed_requires_the_mixin_for_watch_eligibility():
    with pytest.raises(ValidationError, match="no registered workflow event source"):
        triggers.RecordChanged.check_watch_model(MtiParent)


def test_disabled_triggers_write_nothing_and_native_save_skips_raw(trigger_setup):
    actor, _, record, trigger = trigger_setup
    capture(record)
    assert not system_queryset(TriggerEvent).exists()
    Trigger.objects.enable(trigger, actor=actor)
    with trigger_source(Vault, connect=True):
        post_save.send(sender=Vault, instance=record, raw=True, created=False)
        assert not system_queryset(TriggerEvent).exists()
        with actor_context(actor):
            record.save()
        assert system_queryset(TriggerEvent).count() == 1


def test_enable_cannot_persist_an_actorless_system_trigger(trigger_setup):
    _, _, _, trigger = trigger_setup
    with system_context(reason="test.workflow actorless trigger"), pytest.raises(PermissionDenied, match="acting user"):
        Trigger.objects.enable(trigger, actor=None)
    trigger.refresh_from_db()
    assert not trigger.enabled


def test_enable_projects_native_subject_refs_to_the_user_foreign_key(trigger_setup):
    actor, _, _, trigger = trigger_setup
    enabled = Trigger.objects.enable(trigger, actor=to_subject_ref(actor))
    assert enabled.enabled and enabled.workflow.user_id != actor.pk


def test_workflow_principal_grants_are_listable_and_repaired_on_reenable(trigger_setup):
    """The explicit source grant belongs to the workflow user, not the enabler."""
    actor, workflow, _, trigger = trigger_setup
    enabled = Trigger.objects.enable(trigger, actor=actor)
    workflow.refresh_from_db()
    assert workflow.user.kind == "service"
    assert workflow.user.username == f"workflow-{workflow.sqid}"
    assert workflow.user_id != actor.pk
    grants = enabled.granted_relationships(actor=actor)
    assert len(grants) == 1
    grant = grants[0]
    assert (grant.resource_type, grant.resource_id, grant.relation) == (
        "knowledge/role", "vault_viewer", "member",
    )
    assert grant.subject_id == str(workflow.user_id)
    assert enabled.granted_targets == [{
        "resource_type": "knowledge/role", "resource_id": "vault_viewer", "relation": "member",
        "grant_permission": "write", "grant_resource_type": "workflows/workflow",
        "grant_resource_id": str(workflow.pk),
    }]

    delete_relationship(RelationshipTuple(
        resource=triggers.TriggerGrantTarget.from_stored(enabled.granted_targets[0]).resource,
        relation="member", subject=to_subject_ref(workflow.user),
    ))
    assert not enabled.granted_relationships(actor=actor)
    enabled = Trigger.objects.enable(enabled, actor=actor)
    assert len(enabled.granted_relationships(actor=actor)) == 1
    Trigger.objects.disable(enabled, actor=actor)
    assert not active_relationship_model().objects.filter(
        resource_type="knowledge/role", resource_id="vault_viewer", relation="member",
        subject_id=str(workflow.user_id),
    ).exists()


def test_shared_target_survives_until_last_trigger_disables(trigger_setup):
    """Two triggers on one workflow contribute one native tuple without stealing it."""
    actor, workflow, _, first = trigger_setup
    with actor_context(actor):
        second = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label="knowledge.vault",
        )
    first = Trigger.objects.enable(first, actor=actor)
    second = Trigger.objects.enable(second, actor=actor)
    assert len(second.granted_relationships(actor=actor)) == 1
    first = Trigger.objects.revoke_grant(
        first, actor=actor, resource_type="knowledge/role", resource_id="vault_viewer", relation="member",
    )
    assert not first.enabled and "workflow principal" in first.disabled_reason
    assert len(second.granted_relationships(actor=actor)) == 1
    Trigger.objects.disable(second, actor=actor)
    assert not second.granted_relationships(actor=actor)


def test_revoked_required_grant_disables_admission_with_a_reason(trigger_setup):
    actor, workflow, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    trigger = Trigger.objects.revoke_grant(
        trigger, actor=actor, resource_type="knowledge/role", resource_id="vault_viewer", relation="member",
    )
    assert not trigger.enabled and "workflow principal" in trigger.disabled_reason
    assert not trigger.granted_relationships(actor=actor)
    capture(record)
    assert Trigger.objects.drain() == 0
    assert not system_queryset(WorkflowRun).exists()
    assert workflow.user_id is not None


def test_deleting_trigger_releases_its_grant(trigger_setup):
    """Queryset deletion runs the same tuple cleanup as explicit disable."""
    actor, workflow, _, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    with system_context(reason="test.trigger.delete_grants"):
        Trigger.objects.filter(pk=trigger.pk).delete()
    assert not active_relationship_model().objects.filter(
        resource_type="knowledge/role", resource_id="vault_viewer", relation="member",
        subject_id=str(workflow.user_id),
    ).exists()


def test_current_state_rejection_rearms_and_admission_survives_prune(trigger_setup):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    with actor_context(actor):
        record.name = "Later"
        record.save()
    assert Trigger.objects.drain() == 0
    event = system_queryset(TriggerEvent).get()
    assert event.evaluated_at and not event.admitted_at and "no longer matches" in event.rejection
    with actor_context(actor):
        record.name = "Ready"
        record.save()
    capture(record)
    assert Trigger.objects.drain() == 1
    event.refresh_from_db()
    assert event.admitted_at and not event.rejection
    run = event.started_run
    assert run.request_key == f"trigger:{trigger.sqid}:{record.sqid}"
    assert run.trigger_event_id == event.pk
    assert [row.record_public_id for row in system_queryset(WorkflowRunEvidence).filter(run=run)] == [record.sqid]
    run_until(run)
    system_queryset(WorkflowRun).filter(pk=run.pk).update(finished_at=Now() - timedelta(days=91))
    assert WorkflowRun.objects.prune() == 1
    event.refresh_from_db()
    assert event.admitted_at and not system_queryset(WorkflowRun).filter(trigger_event=event).exists()
    with actor_context(actor):
        assert delete_by_public_id(Trigger, trigger.sqid).has_blockers
    capture(record)
    assert Trigger.objects.drain() == 0
    assert not system_queryset(WorkflowRun).exists()


def test_admitted_event_blocks_trigger_purge_preview(trigger_setup):
    """The retained ledger and its started run appear as native deletion blockers."""
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    assert Trigger.objects.drain() == 1
    event = system_queryset(TriggerEvent).get(trigger=trigger)
    with actor_context(actor):
        preview = delete_by_public_id(Trigger, trigger.sqid)
    assert preview.has_blockers
    assert any(blocked.label == str(TriggerEvent._meta.verbose_name_plural) for blocked in preview.blocked)
    assert system_queryset(Trigger).filter(pk=trigger.pk).exists()
    assert event.started_run.trigger_event_id == event.pk


def test_enabler_losing_record_read_does_not_change_principal_admission(trigger_setup):
    admin, workflow, _, trigger = trigger_setup
    editor = create_user("trigger-actor")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    record = vault_for(editor, name="Ready")
    Trigger.objects.enable(trigger, actor=editor)
    capture(record)
    system_queryset(Vault).filter(pk=record.pk).update(owner=create_user("replacement-owner"))
    with system_context(reason="test.workflow principal admission"):
        assert Trigger.objects.drain() == 1
    event = system_queryset(TriggerEvent).get(record_object_id=record.pk)
    assert event.admitted_at and not event.rejection
    assert system_queryset(WorkflowRun).get().run_as_id == workflow.user_id


def test_editor_publication_cannot_use_principal_grants(trigger_setup, monkeypatch):
    admin, workflow, record, trigger = trigger_setup
    editor = create_user("trigger-publisher")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    restrict_source_grant_to_record(monkeypatch, record)
    Trigger.objects.enable(trigger, actor=admin)
    load_workflow(document("entry", "finish"), key=workflow.key, actor=editor,
                  subject_model="knowledge.Vault")
    capture(record)

    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    event = system_queryset(TriggerEvent).get(trigger=trigger)
    assert not trigger.enabled
    assert "Version 2" in trigger.disabled_reason
    assert "trigger-publisher" in trigger.disabled_reason
    assert "member" in trigger.disabled_reason
    assert "vault_viewer" not in trigger.disabled_reason
    assert event.rejection == trigger.disabled_reason
    assert not system_queryset(WorkflowRun).exists()


def test_admin_publication_keeps_principal_admission(trigger_setup):
    admin, workflow, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=admin)
    capture(record)
    assert Trigger.objects.drain() == 1
    assert system_queryset(WorkflowRun).get().version.published_by_id == admin.pk


def test_system_installed_publication_keeps_principal_admission(trigger_setup):
    admin, workflow, record, trigger = trigger_setup
    with system_context(reason="test.workflow system installation"):
        installed = load_workflow(document("entry", "finish"), key=workflow.key,
                                  subject_model="knowledge.Vault")
    assert installed.published.published_by_id is None
    Trigger.objects.enable(trigger, actor=admin)
    capture(record)
    assert Trigger.objects.drain() == 1
    assert system_queryset(WorkflowRun).get().version_id == installed.published_id


def test_publisher_losing_delegation_stops_later_admission(trigger_setup):
    admin, workflow, record, trigger = trigger_setup
    editor = create_user("revoked-publisher")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    load_workflow(document("entry", "finish"), key=workflow.key, actor=editor,
                  subject_model="knowledge.Vault")
    Trigger.objects.enable(trigger, actor=admin)
    workflow.with_actor(admin).revoke_record_access("editor", editor)
    capture(record)

    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    assert not trigger.enabled and "revoked-publisher" in trigger.disabled_reason
    assert not system_queryset(WorkflowRun).exists()


def test_missing_stored_grant_provenance_requires_reenable(trigger_setup):
    admin, workflow, record, trigger = trigger_setup
    trigger = Trigger.objects.enable(trigger, actor=admin)
    stored = [{key: value for key, value in target.items() if not key.startswith("grant_")}
              for target in trigger.granted_targets]
    system_queryset(Trigger).filter(pk=trigger.pk).update(granted_targets=stored)
    capture(record)

    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    assert not trigger.enabled
    assert "grant provenance is missing" in trigger.disabled_reason
    assert "enable the trigger again" in trigger.disabled_reason


def test_child_start_refuses_version_published_without_principal_delegation(trigger_setup, monkeypatch, register_step):
    admin, workflow, record, trigger = trigger_setup
    editor = create_user("child-publisher")
    child = load_workflow(document("entry"), key="untrusted-child", actor=admin)
    child.with_actor(admin).grant_record_access("editor", editor)
    load_workflow(document("entry", "finish"), key=child.key, actor=editor)

    class StartChild(Step[Value, Value, None]):
        key = "start_untrusted_child"

        def run(self, ctx):
            ctx.start_run(child)
            return ctx.done(ctx.input)

    register_step(StartChild)
    load_workflow(document("entry", step=StartChild.key), key=workflow.key, actor=admin,
                  subject_model="knowledge.Vault")
    restrict_source_grant_to_record(monkeypatch, record)
    Trigger.objects.enable(trigger, actor=admin)
    workflow.refresh_from_db()
    child.with_actor(admin).grant_record_access("starter", workflow.user)
    capture(record)
    assert Trigger.objects.drain() == 1
    parent = system_queryset(WorkflowRun).get(trigger_event__trigger=trigger)
    run_until(parent)
    assert parent.status == "failed"
    attempt = system_queryset(StepAttempt).get(step_run__run=parent)
    assert "child-publisher" in attempt.error and "member" in attempt.error
    assert not system_queryset(WorkflowRun).filter(parent_step__run=parent).exists()


def test_deleted_record_is_rejected_instead_of_remaining_pending(trigger_setup):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    with actor_context(actor):
        record.delete()
    assert Trigger.objects.drain() == 0
    event = system_queryset(TriggerEvent).get()
    assert event.evaluated_at and event.rejection and event.admitted_at is None


def test_drain_bound_counts_candidates_including_rejections(trigger_setup, monkeypatch):
    actor, _, _, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    monkeypatch.setattr(triggers, "TRIGGER_DRAIN_LIMIT", 3)
    for index in range(5):
        capture(vault_for(actor, name=f"Pending {index}"))
    assert Trigger.objects.drain() == 0
    assert system_queryset(TriggerEvent).filter(evaluated_at__isnull=False).count() == 3
    assert Trigger.objects.drain() == 0
    assert system_queryset(TriggerEvent).filter(evaluated_at__isnull=False).count() == 5


@pytest.mark.parametrize("condition", [
    {"not_a_field": {"_eq": "value"}}, {"name": {"_unknown": "value"}}, {"id": {"_eq": "bad-public-id"}},
])
def test_conditions_use_actual_resource_fields_and_operators(trigger_setup, condition):
    actor, _, _, trigger = trigger_setup
    trigger.condition = condition
    with actor_context(actor), pytest.raises(ValidationError):
        trigger.save()


def test_condition_depth_is_bounded_and_stale_configuration_disables(trigger_setup):
    actor, _, record, trigger = trigger_setup
    trigger.condition = {}
    for _ in range(13):
        trigger.condition = {"_not": trigger.condition}
    with actor_context(actor), pytest.raises(ValidationError, match="nesting depth"):
        trigger.save()
    trigger.refresh_from_db()
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    system_queryset(Trigger).filter(pk=trigger.pk).update(condition={"removed_field": {"_eq": "x"}})
    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    assert not trigger.enabled and "removed_field" in trigger.disabled_reason


def test_invalid_persisted_public_id_is_configuration_failure(trigger_setup):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    system_queryset(Trigger).filter(pk=trigger.pk).update(condition={"id": {"_eq": "bad-public-id"}})
    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    event = system_queryset(TriggerEvent).get()
    assert not trigger.enabled and trigger.disabled_reason
    assert event.rejection == trigger.disabled_reason and event.evaluated_at and not event.admitted_at


@pytest.mark.parametrize("source_path", [
    "tests.missing_trigger_source.RecordChanged", "tests.workflow_steps.MissingTriggerSource",
])
def test_unimportable_source_disables_and_keeps_the_row_readable(trigger_setup, settings, source_path):
    """Runtime source disappearance is configuration failure, preserving repair diagnostics."""
    actor, _, record, trigger = trigger_setup
    schema = addon_schema(workflow_schema.schemas, "console")
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES, "record_changed": source_path,
    }
    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    event = system_queryset(TriggerEvent).get()
    assert not trigger.enabled and "cannot be loaded" in trigger.disabled_reason
    assert event.rejection == trigger.disabled_reason and event.evaluated_at and not event.admitted_at
    data = result_data(execute_schema(schema, """query($id: String!) {
      trigger(where: {id: {_eq: $id}}) { id source source_model display_name enabled disabled_reason }
    }""", {"id": trigger.sqid}, user=actor))["trigger"]
    assert len(data) == 1 and data[0]["source_model"] == "knowledge.Vault"
    assert data[0]["display_name"] == "record_changed: knowledge.Vault"
    assert data[0]["enabled"] is False
    assert data[0]["disabled_reason"] == trigger.disabled_reason


def test_capture_failure_preserves_the_enclosing_write(trigger_setup, monkeypatch, caplog):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)

    def fail(*args, **kwargs):
        raise IntegrityError("capture failed")

    monkeypatch.setattr(triggers.TriggerEventQuerySet, "update_or_create", fail)
    with transaction.atomic(), actor_context(actor):
        record.name = "Retained"
        record.save()
        capture(record)
        assert Vault.objects.filter(pk=record.pk, name="Retained").exists()
    assert "Workflow trigger capture failed" in caplog.text


def test_watch_capture_failure_preserves_the_native_save(trigger_setup, monkeypatch, caplog):
    """The shared capture savepoint rolls back its ledger, retaining the source write."""
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)

    def fail(*args, **kwargs):
        raise IntegrityError("watch capture failed")

    monkeypatch.setattr(type(StepWatch.objects), "record_change", fail)
    with trigger_source(Vault, connect=True):
        with transaction.atomic(), actor_context(actor):
            record.name = "Retained despite capture failure"
            record.save()
            assert Vault.objects.filter(pk=record.pk, name=record.name).exists()
    assert not system_queryset(TriggerEvent).exists()
    assert "Workflow source capture failed" in caplog.text


@pytest.mark.parametrize("model_label", ["workflows.workflow", "decisions.decision", "auth.user"])
def test_recursive_and_non_opted_models_are_refused(trigger_setup, model_label):
    actor, _, _, trigger = trigger_setup
    trigger.model_label = model_label
    with actor_context(actor), pytest.raises(ValidationError):
        trigger.save()


def test_resource_condition_rejects_field_gated_reads(execution):
    """The compiler uses the resource catalogue, which excludes read__error fields."""
    owner = GraphQLSchemas([make_addon(schemas=workflow_schema.schemas)])
    with pytest.raises(ValidationError, match="error.*not defined"):
        owner.resource_filter(StepAttempt, {"error": {"_eq": "hidden"}})


def test_resource_condition_reuses_expression_extensions_and_public_id_decoders(trigger_setup):
    """Stored filters follow the same final input extension and SQL alias as requests."""
    actor, _, record, _ = trigger_setup

    @strawberry_django.type(Vault)
    class FilterVault(AngeeNode):
        name: str

    resource = hasura_model_resource(
        FilterVault, model=Vault, name="filter_vault", filterable=("id", "name", "upper_name"),
        filter_expressions={"upper_name": Upper("name", output_field=models.CharField())},
        sortable=("id",), aggregatable=(),
        insert=False, update=False, delete=False,
    )
    owner = GraphQLSchemas([make_addon(schemas={"console": {
        "query": [resource.query], "types": resource.types,
    }})])
    condition = owner.resource_filter(Vault, {"_and": [
        {"id": {"_eq": str(record.sqid)}}, {"upper_name": {"_eq": "READY"}},
    ]})
    assert list(condition(Vault.objects.with_actor(actor)).values_list("pk", flat=True)) == [record.pk]


@pytest.mark.parametrize("field", ["condition", "source", "model_label"])
def test_configuration_edits_disable_the_previous_enablers_authority(
    execution, trigger_resource_schema, monkeypatch, settings, field,
):
    """A co-editor's native partial save must require a new enabling actor."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="source-stability", actor=actor)
    editor = create_user("trigger-co-editor")
    workflow.with_actor(actor).grant_record_access("editor", editor)
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES,
        "other_changed": "tests.test_workflows_triggers.OtherChanged",
    }
    source_field = Trigger._meta.get_field("source")
    monkeypatch.setattr(source_field, "choices_enum", impl_choices_enum(triggers.TriggerSource))
    with trigger_source(Page):
        with actor_context(actor):
            trigger = Trigger.objects.create(workflow=workflow, source="record_changed", model_label="knowledge.vault")
        trigger = Trigger.objects.enable(trigger, actor=actor)
        trigger.with_actor(editor)
        replacement = {"condition": {"name": {"_eq": "Ready"}}, "source": "other_changed",
                       "model_label": "knowledge.page"}[field]
        setattr(trigger, field, replacement)
        with actor_context(editor):
            trigger.save(update_fields=(field,))
        trigger.refresh_from_db()
        assert not trigger.enabled
        assert "changed" in trigger.disabled_reason


class OtherChanged(triggers.RecordChanged):
    """An alternate native source used to verify activation invalidation."""

    key = "other_changed"


@pytest.mark.parametrize("bulk", [False, True])
def test_bulk_authoring_cannot_bypass_trigger_configuration_owner(trigger_setup, bulk):
    """Authored bulk writes cannot retain activation while bypassing model validation."""
    admin, workflow, _, trigger = trigger_setup
    editor = create_user("trigger-bulk-editor")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    trigger = Trigger.objects.enable(trigger, actor=admin)
    original = trigger.condition
    trigger.condition = {}
    queryset = Trigger.objects.with_actor(editor).filter(pk=trigger.pk)
    with pytest.raises(ValidationError, match="save"):
        if bulk:
            queryset.bulk_update([trigger], ["condition"])
        else:
            queryset.update(condition={})
    trigger.refresh_from_db()
    assert trigger.condition == original and trigger.enabled and trigger.workflow.user_id != admin.pk


def test_bulk_creation_cannot_borrow_another_users_trigger_authority(trigger_setup):
    """A workflow editor must use the enable owner to grant its principal."""
    admin, workflow, _, trigger = trigger_setup
    editor = create_user("trigger-bulk-creator")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    forged = Trigger(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
        enabled=True,
    )
    with pytest.raises(ValidationError, match="save"):
        Trigger.objects.with_actor(editor).bulk_create([forged])
    assert list(system_queryset(Trigger).values_list("pk", flat=True)) == [trigger.pk]
    installed = Trigger(workflow=workflow, source="record_changed", model_label="knowledge.vault")
    assert system_queryset(Trigger).bulk_create([installed]) == [installed]
    assert not installed.enabled


@pytest.mark.parametrize("boundary", ["enable", "check_access", "check_admission", "trigger_input"])
def test_trigger_actor_boundaries_drop_ambient_system_privileges(trigger_setup, monkeypatch, boundary):
    """Enable checks the author; admission hooks act as the workflow principal."""
    admin, workflow, record, trigger = trigger_setup
    actor = create_user("trigger-hook-actor")
    workflow.with_actor(admin).grant_record_access("editor", actor)
    record = vault_for(actor, name="Ready")
    hidden = vault_for(admin, name="Hidden")
    seen = []

    def observe(*args, **kwargs):
        seen.append((is_sudo(), current_actor(), Vault.objects.filter(pk=hidden.pk).exists()))
        return {}

    if boundary == "enable":
        monkeypatch.setattr(triggers.RecordChanged, "check_access", observe)
    with system_context(reason="test trigger enable actor boundary"):
        trigger = Trigger.objects.enable(trigger, actor=actor)
    if boundary != "enable":
        owner = triggers.RecordChanged if boundary == "check_access" else Trigger
        if owner is Trigger:
            monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
        monkeypatch.setattr(owner, boundary, observe)
        capture(record)
        with system_context(reason="test trigger admission actor boundary"):
            assert Trigger.objects.drain() == 1
    assert seen == ([(False, to_subject_ref(actor), False)] if boundary == "enable"
                    else [(False, to_subject_ref(workflow.user), True)])


def test_enabler_deactivation_does_not_disable_principal_admission(trigger_setup):
    """The enabling user's lifecycle does not change the standing principal."""
    admin, workflow, record, trigger = trigger_setup
    actor = create_user("trigger-deactivated-actor")
    workflow.with_actor(admin).grant_record_access("editor", actor)
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    system_queryset(type(actor)).filter(pk=actor.pk).update(is_active=False)
    assert Trigger.objects.drain() == 1
    trigger.refresh_from_db()
    assert trigger.enabled
    assert system_queryset(WorkflowRun).get().run_as_id == workflow.user_id


def test_domain_hooks_own_input_and_rejections_without_disabling(trigger_setup, monkeypatch):
    """Domain policy runs as the principal and rolls back when admission rejects."""
    admin, workflow, record, trigger = trigger_setup
    actor = create_user("trigger-domain-actor")
    workflow.with_actor(admin).grant_record_access("editor", actor)
    record = vault_for(actor, name="Ready")
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    seen = []

    def reject(self, record, *, actor):
        seen.append(actor.pk)
        raise ValidationError("Domain admission declined.")

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", reject)
    assert Trigger.objects.drain() == 0
    record.refresh_from_db()
    assert record.name == "Ready" and seen == [workflow.user_id]
    assert system_queryset(Trigger).get(pk=trigger.pk).enabled
    assert "Domain admission declined" in system_queryset(TriggerEvent).get().rejection
    monkeypatch.setattr(Trigger, "check_admission", lambda self, record, *, actor: None)
    monkeypatch.setattr(Trigger, "trigger_input", lambda self, record: {"value": record.pk})
    capture(record)
    assert Trigger.objects.drain() == 1
    assert system_queryset(WorkflowRun).get().input == {"value": record.pk}


def test_transient_trigger_error_remains_pending_for_later_drain(trigger_setup, monkeypatch):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    original = Trigger.trigger_input
    calls = 0

    def transient(self, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("temporary connection failure")
        return original(self, record)

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "trigger_input", transient)
    assert Trigger.objects.drain() == 0
    event = system_queryset(TriggerEvent).get()
    assert event.evaluated_at is None and event.rejection == ""
    assert Trigger.objects.drain() == 1


def test_unexpected_trigger_configuration_error_isolates_candidate(trigger_setup, monkeypatch, caplog):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    second = vault_for(actor, name="Other")
    capture(record)
    capture(second)
    original = Trigger.validate_configuration

    def broken(self):
        if self.pk == trigger.pk and not getattr(self, "_retried", False):
            raise RuntimeError("unexpected configuration bug")
        return original(self)

    monkeypatch.setattr(Trigger, "validate_configuration", broken)
    # A second trigger owns an independent candidate in the same drain.
    with actor_context(actor):
        other = Trigger.objects.create(
            workflow=trigger.workflow, source="record_changed", model_label="knowledge.vault",
            condition={},
        )
    Trigger.objects.enable(other, actor=actor)
    capture(second)
    assert Trigger.objects.drain() >= 1
    assert "unexpected configuration bug" in caplog.text
    assert system_queryset(TriggerEvent).filter(trigger=trigger, evaluated_at__isnull=True).exists()


def test_domain_rejection_is_sanitized(trigger_setup, monkeypatch):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)

    def reject(self, record, *, actor):
        raise ValidationError("Rejected\x00 secret")

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", reject)
    assert Trigger.objects.drain() == 0
    event = system_queryset(TriggerEvent).get()
    assert "Rejected" in event.rejection and "\x00" not in event.rejection


def test_trigger_hook_contributions_are_source_scoped_and_conflicts_fail():
    seen = []

    class RecordHook:
        trigger_sources = ("record_changed",)

        def trigger_input(self, record):
            return {"record": record}

        def check_admission(self, record, *, actor):
            seen.append("record")

    class MessageHook:
        trigger_sources = ("message_ingested",)

        def trigger_input(self, record):
            return {"message": record}

        def check_admission(self, record, *, actor):
            seen.append("message")

    class Combined(RecordHook, MessageHook):
        source = "record_changed"
        _trigger_hooks = Trigger._trigger_hooks
        admission_input = Trigger.admission_input
        admit_record = Trigger.admit_record

    trigger = Combined()
    hooks = trigger._trigger_hooks("trigger_input")
    assert hooks == [RecordHook.trigger_input]
    assert trigger._trigger_hooks("check_admission") == [RecordHook.check_admission]
    assert trigger.admission_input("row") == {"record": "row"}
    trigger.admit_record("row", actor=None)
    assert seen == ["record"]
    trigger.source = "other"
    assert trigger.admission_input("row") == {}
    trigger.admit_record("row", actor=None)
    assert seen == ["record"]

    class AnotherRecordHook:
        trigger_sources = ("record_changed",)

        def trigger_input(self, record):
            return {"other": record}

    class Conflict(RecordHook, AnotherRecordHook):
        source = "record_changed"

    with pytest.raises(ImproperlyConfigured, match="Multiple trigger_input"):
        Trigger._trigger_hooks(Conflict(), "trigger_input")
