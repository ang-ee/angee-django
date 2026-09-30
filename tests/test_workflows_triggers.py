"""Level-triggered admission through native source, filter and permission owners."""

from datetime import timedelta

import pytest
import strawberry_django
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.db.models.functions import Now, Upper
from django.db.models.signals import post_save
from rebac import actor_context, current_actor, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.impl import impl_choices_enum
from angee.base.scoping import system_queryset
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.knowledge import schema as knowledge_schema
from angee.workflows import schema as workflow_schema
from angee.workflows import triggers
from angee.workflows.testing.drivers import load_workflow, run_until, trigger_source
from angee.workflows.testing.models import StepAttempt, StepWatch, Trigger, TriggerEvent, WorkflowRun
from tests.conftest import Page, Vault, addon_schema, create_user, execute_schema, make_addon, result_data, vault_for
from tests.workflow_steps import document

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
    assert not trigger.enabled and trigger.run_as_id is None


def test_enable_projects_native_subject_refs_to_the_user_foreign_key(trigger_setup):
    actor, _, _, trigger = trigger_setup
    enabled = Trigger.objects.enable(trigger, actor=to_subject_ref(actor))
    assert enabled.enabled and enabled.run_as_id == actor.pk


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
    run = system_queryset(WorkflowRun).get(pk=event.run_id)
    assert run.request_key == f"trigger:{trigger.sqid}:{record.sqid}"
    assert run.trigger_event_id == event.pk
    run_until(run)
    system_queryset(WorkflowRun).filter(pk=run.pk).update(finished_at=Now() - timedelta(days=91))
    assert WorkflowRun.objects.prune() == 1
    event.refresh_from_db()
    assert event.run_id is None and event.admitted_at
    capture(record)
    assert Trigger.objects.drain() == 0
    assert not system_queryset(WorkflowRun).exists()


def test_actor_losing_record_read_rejects_under_system_drain(trigger_setup):
    admin, workflow, _, trigger = trigger_setup
    editor = create_user("trigger-actor")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    record = vault_for(editor, name="Ready")
    Trigger.objects.enable(trigger, actor=editor)
    capture(record)
    system_queryset(Vault).filter(pk=record.pk).update(owner=create_user("replacement-owner"))
    with system_context(reason="test.workflow trigger actor pin"):
        assert Trigger.objects.drain() == 0
    event = system_queryset(TriggerEvent).get(record_object_id=record.pk)
    assert "inaccessible" in event.rejection
    assert not system_queryset(WorkflowRun).exists()


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
    assert not trigger.enabled and trigger.run_as_id is None and "removed_field" in trigger.disabled_reason


def test_invalid_persisted_public_id_is_configuration_failure(trigger_setup):
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    system_queryset(Trigger).filter(pk=trigger.pk).update(condition={"id": {"_eq": "bad-public-id"}})
    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    event = system_queryset(TriggerEvent).get()
    assert not trigger.enabled and trigger.run_as_id is None and trigger.disabled_reason
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
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCES, "record_changed": source_path,
    }
    assert Trigger.objects.drain() == 0
    trigger.refresh_from_db()
    event = system_queryset(TriggerEvent).get()
    assert not trigger.enabled and trigger.run_as_id is None and "cannot be loaded" in trigger.disabled_reason
    assert event.rejection == trigger.disabled_reason and event.evaluated_at and not event.admitted_at
    data = result_data(execute_schema(schema, """query($id: String!) {
      trigger(where: {id: {_eq: $id}}) { id source source_model display_name enabled disabled_reason run_as { id } }
    }""", {"id": trigger.sqid}, user=actor))["trigger"]
    assert len(data) == 1 and data[0]["source_model"] == "knowledge.vault"
    assert data[0]["display_name"] == "record_changed: knowledge.vault"
    assert data[0]["enabled"] is False and data[0]["run_as"] is None
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
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCES,
        "other_changed": "tests.test_workflows_triggers.OtherChanged",
    }
    source_field = Trigger._meta.get_field("source")
    monkeypatch.setattr(source_field, "choices_enum", impl_choices_enum(source_field.registry_setting))
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
        assert not trigger.enabled and trigger.run_as_id is None
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
    assert trigger.condition == original and trigger.enabled and trigger.run_as_id == admin.pk


def test_bulk_creation_cannot_borrow_another_users_trigger_authority(trigger_setup):
    """A workflow editor must use the enable owner to acquire an acting identity."""
    admin, workflow, _, trigger = trigger_setup
    editor = create_user("trigger-bulk-creator")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    forged = Trigger(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
        enabled=True, run_as=admin,
    )
    with pytest.raises(ValidationError, match="save"):
        Trigger.objects.with_actor(editor).bulk_create([forged])
    assert list(system_queryset(Trigger).values_list("pk", flat=True)) == [trigger.pk]
    installed = Trigger(workflow=workflow, source="record_changed", model_label="knowledge.vault")
    assert system_queryset(Trigger).bulk_create([installed]) == [installed]
    assert not installed.enabled and installed.run_as_id is None


@pytest.mark.parametrize("boundary", ["enable", "check_access", "check_admission", "trigger_input"])
def test_trigger_actor_boundaries_drop_ambient_system_privileges(trigger_setup, monkeypatch, boundary):
    """Source and domain hooks observe only the non-admin enabling user's rows."""
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
    assert seen == [(False, to_subject_ref(actor), False)]


@pytest.mark.parametrize("active_at_enable", [False, True])
def test_inactive_trigger_actor_cannot_enable_or_admit(trigger_setup, active_at_enable):
    """Revocation of the pinned account disables admission without starting a run."""
    admin, workflow, record, trigger = trigger_setup
    actor = create_user("trigger-deactivated-actor")
    workflow.with_actor(admin).grant_record_access("editor", actor)
    if active_at_enable:
        Trigger.objects.enable(trigger, actor=actor)
        capture(record)
    system_queryset(type(actor)).filter(pk=actor.pk).update(is_active=False)
    if active_at_enable:
        assert Trigger.objects.drain() == 0
    else:
        with pytest.raises(PermissionDenied, match="active"):
            Trigger.objects.enable(trigger, actor=to_subject_ref(actor))
    trigger.refresh_from_db()
    assert not trigger.enabled and trigger.run_as_id is None
    if active_at_enable:
        assert "active" in trigger.disabled_reason
        event = system_queryset(TriggerEvent).get()
        assert event.rejection == trigger.disabled_reason and event.evaluated_at
    assert not system_queryset(WorkflowRun).exists()


def test_domain_hooks_own_input_and_rejections_without_disabling(trigger_setup, monkeypatch):
    """Domain policy runs as run_as and is rolled back when admission rejects."""
    admin, workflow, record, trigger = trigger_setup
    actor = create_user("trigger-domain-actor")
    workflow.with_actor(admin).grant_record_access("editor", actor)
    record = vault_for(actor, name="Ready")
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    seen = []

    def reject(self, record, *, actor):
        seen.append(actor.pk)
        record.name = "Rolled back"
        record.save()
        raise ValidationError("Domain admission declined.")

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", reject)
    assert Trigger.objects.drain() == 0
    record.refresh_from_db()
    assert record.name == "Ready" and seen == [actor.pk]
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
