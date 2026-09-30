"""Trigger authoring, activation and ledger reads use the framework resource owners."""

import pytest
import yaml
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.scoping import system_queryset
from angee.integrate.schema import ConsoleImplChoicesQuery
from angee.resources.testing.models import Resource
from angee.workflows import schema as workflow_schema
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import Trigger, TriggerEvent
from angee.workflows.triggers import TriggerGrantTarget, TriggerSource
from angee.workflows_messaging.sources import MessageIngested
from tests.conftest import Vault, addon_schema, create_user, execute_schema, make_addon, result_data, vault_for
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.workflow_steps import document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


class FixedVaultSource(TriggerSource):
    """Exercise a fixed model authored with Django's lowercase label spelling."""

    key = "record_changed"
    label = "Vault event"
    model_label = "knowledge.vault"


@pytest.fixture
def trigger_surface(execution, trigger_resource_schema):
    """Use a real resource model, workflow grants and a model-owned source opt-in."""
    admin, _sent = execution
    workflow = load_workflow(document("entry"), key="trigger-surface", actor=admin)
    editor, viewer, starter = (create_user(name) for name in ("trigger-editor", "trigger-viewer", "trigger-starter"))
    for role, actor in (("editor", editor), ("viewer", viewer), ("starter", starter)):
        workflow.with_actor(admin).grant_record_access(role, actor)
    return addon_schema(workflow_schema.schemas, "console"), workflow, editor, viewer, starter


def test_native_trigger_crud_and_actions_keep_activation_server_owned(trigger_surface):
    """A workflow editor can author and enable; readers cannot modify activation."""
    schema, workflow, editor, viewer, starter = trigger_surface
    create = """mutation($workflow: ID!) {
      insert_trigger_one(object: {
        workflow: $workflow, source: "record_changed", model_label: "knowledge.vault", condition: {}
      }) { id enabled disabled_reason source_model can_edit }
    }"""
    variables = {"workflow": workflow.sqid}
    for reader in (viewer, starter):
        assert execute_schema(schema, create, variables, user=reader).errors
    inserted = result_data(execute_schema(schema, create, variables, user=editor))["insert_trigger_one"]
    assert inserted["enabled"] is False
    assert inserted["disabled_reason"] == "" and inserted["source_model"] == "knowledge.Vault"
    assert inserted["can_edit"] is True
    trigger = system_queryset(Trigger).get(sqid=inserted["id"])
    read = "query { trigger { id can_edit } }"
    for reader in (viewer, starter):
        assert result_data(execute_schema(schema, read, user=reader))["trigger"] == [
            {"id": trigger.sqid, "can_edit": False},
        ]
    enable = "mutation($id: ID!) { enable_workflow_trigger(id: $id) { ok } }"
    for reader in (viewer, starter):
        assert not result_data(execute_schema(schema, enable, {"id": trigger.sqid}, user=reader))[
            "enable_workflow_trigger"
        ]["ok"]
    assert result_data(execute_schema(schema, enable, {"id": trigger.sqid}, user=editor))[
        "enable_workflow_trigger"
    ]["ok"]
    trigger = system_queryset(Trigger).get(pk=trigger.pk)
    assert trigger.enabled and trigger.workflow.user_id != editor.pk
    for name in ("trigger_insert_input", "trigger_set_input"):
        fields = schema._schema.get_type(name).fields
        assert {"enabled", "disabled_reason"}.isdisjoint(fields)
    disable = "mutation($id: ID!) { disable_workflow_trigger(id: $id) { ok } }"
    assert result_data(execute_schema(schema, disable, {"id": trigger.sqid}, user=editor))[
        "disable_workflow_trigger"
    ]["ok"]
    trigger = system_queryset(Trigger).get(pk=trigger.pk)
    assert not trigger.enabled
    result_data(execute_schema(schema, """mutation($id: String!) {
      update_trigger_by_pk(pk_columns: {id: $id}, _set: {condition: {name: {_eq: "Ready"}}}) { id }
    }""", {"id": trigger.sqid}, user=editor))
    assert system_queryset(Trigger).get(pk=trigger.pk).condition == {"name": {"_eq": "Ready"}}
    result_data(execute_schema(schema, """mutation($id: String!) {
      delete_trigger_by_pk(id: $id) { id }
    }""", {"id": trigger.sqid}, user=editor))
    assert not system_queryset(Trigger).filter(pk=trigger.pk).exists()


def test_enable_preview_discloses_grants_and_run_readers_only_to_enablers(trigger_surface):
    schema, workflow, editor, viewer, starter = trigger_surface
    admin = workflow.created_by
    group = apps.get_model("iam", "Group")
    with system_context(reason="test.workflow reader group"):
        reviewers = group.objects.create(name="Reviewers")
    workflow.with_actor(admin).grant_record_access("viewer", reviewers)
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    query = """query($id: String!) {
      trigger_by_pk(id: $id) { enable_preview { grants run_readers } }
    }"""
    variables = {"id": trigger.sqid}
    preview = result_data(execute_schema(schema, query, variables, user=editor))[
        "trigger_by_pk"
    ]["enable_preview"]
    assert preview["grants"] == ["member on vault viewer (knowledge role)"]
    assert f"User: {editor.username}" in preview["run_readers"]
    assert f"User: {viewer.username}" in preview["run_readers"]
    assert "Group: Reviewers" in preview["run_readers"]
    assert f"User: {starter.username}" not in preview["run_readers"]
    for actor in (viewer, starter):
        assert result_data(execute_schema(schema, query, variables, user=actor))[
            "trigger_by_pk"
        ]["enable_preview"] is None


def test_enable_preview_hides_reader_list_from_editor_without_delegation(trigger_surface, monkeypatch):
    schema, workflow, editor, _viewer, _starter = trigger_surface
    owner = workflow.created_by
    target = vault_for(owner, name="Private delegation target")

    def grants(cls, trigger):
        return (TriggerGrantTarget(to_object_ref(target), "reader", "write"),)

    monkeypatch.setattr(Vault, "record_changed_grant_targets", classmethod(grants))
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    query = "query($id: String!) { trigger_by_pk(id: $id) { can_edit enable_preview { run_readers } } }"
    result = result_data(execute_schema(schema, query, {"id": trigger.sqid}, user=editor))["trigger_by_pk"]
    assert result == {"can_edit": True, "enable_preview": None}


def test_trigger_grants_are_visible_and_revocable_through_the_authoring_surface(trigger_surface):
    schema, workflow, editor, viewer, _starter = trigger_surface
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    Trigger.objects.enable(trigger, actor=editor)
    query = """query($id: String!) {
      trigger_by_pk(id: $id) {
        id can_edit enabled disabled_reason
        grants { resource_type resource_id relation target_kind target_label }
      }
    }"""
    for actor, can_edit in ((editor, True), (viewer, False)):
        data = result_data(execute_schema(schema, query, {"id": trigger.sqid}, user=actor))["trigger_by_pk"]
        assert data["can_edit"] is can_edit
        assert data["grants"] == [{
            "resource_type": "knowledge/role", "resource_id": "vault_viewer",
            "relation": "member", "target_kind": "knowledge role", "target_label": None,
        }]
    revoke = """mutation($id: ID!) {
      revoke_workflow_trigger_grant(
        id: $id, resource_type: "knowledge/role", resource_id: "vault_viewer", relation: "member"
      ) { ok message }
    }"""
    variables = {"id": trigger.sqid}
    denied = result_data(execute_schema(schema, revoke, variables, user=viewer))["revoke_workflow_trigger_grant"]
    assert not denied["ok"]
    result = result_data(execute_schema(schema, revoke, variables, user=editor))["revoke_workflow_trigger_grant"]
    assert result["ok"] and "workflow principal" in result["message"]
    current = result_data(execute_schema(schema, query, variables, user=viewer))["trigger_by_pk"]
    assert not current["enabled"] and "workflow principal" in current["disabled_reason"]
    assert "knowledge/role:vault_viewer" not in current["disabled_reason"]
    assert current["grants"] == []


def test_grant_target_label_requires_target_read_access(trigger_surface, monkeypatch):
    schema, workflow, editor, viewer, _starter = trigger_surface
    target = vault_for(editor, name="Readable grant target")

    def grants(cls, trigger):
        return (TriggerGrantTarget(to_object_ref(target), "viewer", "write"),)

    monkeypatch.setattr(Vault, "record_changed_grant_targets", classmethod(grants))
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    Trigger.objects.enable(trigger, actor=editor)
    query = "query($id: String!) { trigger_by_pk(id: $id) { grants { target_kind target_label } } }"
    variables = {"id": trigger.sqid}
    assert result_data(execute_schema(schema, query, variables, user=editor))["trigger_by_pk"]["grants"] == [
        {"target_kind": "vault", "target_label": "Readable grant target"},
    ]
    assert result_data(execute_schema(schema, query, variables, user=viewer))["trigger_by_pk"]["grants"] == [
        {"target_kind": "vault", "target_label": None},
    ]


@pytest.mark.parametrize("source_path,model_label,label", [
    ("angee.workflows.triggers.RecordChanged", "knowledge.vault", "Record changed"),
    ("tests.test_workflows_triggers_surface.FixedVaultSource", "", "Vault event"),
])
def test_resolved_model_and_title_share_source_owner_without_row_queries(
    trigger_surface, settings, source_path, model_label, label,
):
    """Stored lowercase and fixed source models use the metadata's canonical identity."""
    schema, workflow, editor, viewer, _starter = trigger_surface
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES,
        "record_changed": source_path,
    }
    assert FixedVaultSource.choice().defaults["source_model"] == "knowledge.Vault"
    query = "{ trigger { display_name source_model condition } }"
    expected = {
        "display_name": f"{label}: vault", "source_model": "knowledge.Vault",
        "condition": {"name": {"_eq": "Ready"}},
    }
    counts = []
    for count in (1, 5):
        for _ in range(count - system_queryset(Trigger).count()):
            Trigger.objects.with_actor(editor).create(
                workflow=workflow, source="record_changed", model_label=model_label, condition=expected["condition"],
            )
        with CaptureQueriesContext(connection) as captured:
            assert result_data(execute_schema(schema, query, user=viewer)) == {"trigger": [expected] * count}
        counts.append(len(captured))
    assert counts[0] == counts[1]


def test_message_source_defaults_and_record_model_use_the_same_canonical_identity(settings):
    """New message-source forms receive the same model as a retained trigger."""
    settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES = {
        **settings.ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES,
        "message_ingested": "angee.workflows_messaging.sources.MessageIngested",
    }
    trigger = Trigger(source="message_ingested")
    assert trigger.source_model == MessageIngested.choice().defaults["source_model"] == "messaging.Message"
    assert str(trigger) == "Message ingested: message"


def test_source_choices_follow_workflow_authority_without_widening_other_registries(trigger_surface):
    """A non-admin author can configure a source; ordinary readers cannot inspect its defaults."""
    _schema, _workflow, editor, viewer, starter = trigger_surface
    schema = addon_schema({"console": {"query": [ConsoleImplChoicesQuery]}}, "console")
    query = """query($model: String!, $field: String!) {
      impl_choices(model: $model, field: $field) { key defaults }
    }"""
    variables = {"model": "workflows.Trigger", "field": "source"}
    choices = result_data(execute_schema(schema, query, variables, user=editor))["impl_choices"]
    assert {"key": "record_changed", "defaults": {"source_model": ""}} in choices
    for reader in (viewer, starter, None):
        assert execute_schema(schema, query, variables, user=reader).errors
    assert execute_schema(schema, query, {
        "model": "integrate_vcs.VcsBridge", "field": "backendClass",
    }, user=editor).errors


def test_trigger_ledger_origin_and_filters_require_operator_visibility(trigger_surface):
    """Admission evidence is readable to editors, not ordinary workflow readers."""
    schema, workflow, editor, viewer, starter = trigger_surface
    record = vault_for(editor, name="Ready")
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    Trigger.objects.enable(trigger, actor=editor)
    TriggerEvent.objects.record_change(Vault, record)
    Trigger.objects.drain()
    event = system_queryset(TriggerEvent).get(trigger=trigger)
    assert event.admitted_at is not None, event.rejection
    run = event.started_run
    assert run.origin == "trigger" and run.trigger_event_id == event.pk and run.run_as_id == workflow.user_id
    query = """query(
      $workflow: String!, $trigger: String!, $model: String!, $record: String!, $event: String!, $run: String!
    ) {
      trigger(where: {workflow: {_eq: $workflow}}) { id enabled source }
      triggerevent(where: {trigger: {_eq: $trigger}, record_model: {_eq: $model}, record_id: {_eq: $record}}) {
        id record_model record_id changed_at evaluated_at admitted_at rejection started_run { id origin }
      }
      workflowrun(where: {trigger_event: {_eq: $event}}) { id origin trigger_event { id } }
      by_started_run: triggerevent(where: {started_run: {_eq: $run}}) { id }
    }"""
    variables = {
        "workflow": workflow.sqid, "trigger": trigger.sqid, "model": "knowledge.Vault",
        "record": record.sqid, "event": event.sqid, "run": run.sqid,
    }
    visible = result_data(execute_schema(schema, query, variables, user=editor))
    assert visible["trigger"][0]["id"] == trigger.sqid
    ledger = visible["triggerevent"]
    assert len(ledger) == 1 and ledger[0]["record_id"] == record.sqid
    assert ledger[0]["admitted_at"] and ledger[0]["evaluated_at"] and ledger[0]["rejection"] == ""
    assert ledger[0]["started_run"] == {"id": run.sqid, "origin": "TRIGGER"}
    assert visible["by_started_run"] == [{"id": event.sqid}]
    assert visible["workflowrun"] == [{"id": run.sqid, "origin": "TRIGGER", "trigger_event": {"id": event.sqid}}]
    for reader in (viewer, starter):
        restricted = result_data(execute_schema(schema, query, variables, user=reader))
        assert len(restricted["trigger"]) == 1
        assert restricted["triggerevent"] == []
        assert restricted["by_started_run"] == []
        if reader is viewer:
            assert restricted["workflowrun"] == [{"id": run.sqid, "origin": "TRIGGER", "trigger_event": None}]
        else:
            assert restricted["workflowrun"] == []
    operator = create_user("trigger-run-operator")
    run.with_actor(editor).grant_record_access("operator", operator)
    operated = result_data(execute_schema(schema, "{ triggerevent { id started_run { id origin } } }", user=operator))
    assert operated["triggerevent"] == [{
        "id": event.sqid, "started_run": {"id": run.sqid, "origin": "TRIGGER"},
    }]
    resources = {resource.model_label: resource for resource in schema.angee_resources}
    assert "condition" in {field.name for field in resources["workflows.Trigger"].fields}
    mutations = schema._schema.mutation_type.fields
    assert not any("triggerevent" in name for name in mutations)


def test_trigger_ledger_redacts_unreadable_source_identity_and_filter_oracles(trigger_surface):
    """An editor sees admission state but cannot discover a hidden source by identity filters."""
    schema, workflow, editor, _viewer, source_owner = trigger_surface
    assert not editor.is_superuser and not source_owner.is_superuser
    record = vault_for(source_owner, name="Private source")
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    Trigger.objects.enable(trigger, actor=editor)
    TriggerEvent.objects.record_change(Vault, record)
    event = system_queryset(TriggerEvent).get(trigger=trigger)
    query = """query($model: String!, $record: String!) {
      triggerevent { id record_model record_id evaluated_at admitted_at }
      by_model: triggerevent(where: {record_model: {_eq: $model}}) { id }
      by_record: triggerevent(where: {record_id: {_eq: $record}}) { id }
      model_count: triggerevent_aggregate(where: {record_model: {_eq: $model}}) { aggregate { count } }
      record_count: triggerevent_aggregate(where: {record_id: {_eq: $record}}) { aggregate { count } }
    }"""
    variables = {"model": record._meta.label, "record": record.sqid}
    hidden = result_data(execute_schema(schema, query, variables, user=editor))
    assert hidden == {
        "triggerevent": [{"id": event.sqid, "record_model": None, "record_id": None,
                          "evaluated_at": None, "admitted_at": None}],
        "by_model": [], "by_record": [],
        "model_count": {"aggregate": {"count": 0}}, "record_count": {"aggregate": {"count": 0}},
    }
    grant = RelationshipTuple(resource=to_object_ref(record), relation="viewer", subject=to_subject_ref(editor))
    write_relationships([grant])
    visible = result_data(execute_schema(schema, query, variables, user=editor))
    assert visible["triggerevent"][0]["record_model"] == record._meta.label
    assert visible["triggerevent"][0]["record_id"] == record.sqid
    assert visible["by_model"] == visible["by_record"] == [{"id": event.sqid}]
    assert visible["model_count"] == visible["record_count"] == {"aggregate": {"count": 1}}
    Trigger.objects.drain()
    event.refresh_from_db()
    assert event.admitted_at is not None, event.rejection
    nested = "{ workflowrun { subject_model subject_id trigger_event { id record_model record_id } } }"
    assert result_data(execute_schema(schema, nested, user=editor)) == {"workflowrun": [{"trigger_event": {
        "id": event.sqid, "record_model": record._meta.label, "record_id": record.sqid,
    }, "subject_model": record._meta.label, "subject_id": record.sqid}]}
    delete_relationship(grant)
    assert result_data(execute_schema(schema, nested, user=editor)) == {"workflowrun": [{"subject_model": None,
        "subject_id": None, "trigger_event": {
        "id": event.sqid, "record_model": None, "record_id": None,
    }}]}


def test_trigger_ledger_reference_guard_batches_source_reads(trigger_surface):
    """Reading five ledger references requires the same queries as reading one."""
    schema, workflow, editor, _viewer, source_owner = trigger_surface
    trigger = Trigger.objects.with_actor(editor).create(
        workflow=workflow, source="record_changed", model_label="knowledge.vault",
    )
    Trigger.objects.enable(trigger, actor=editor)
    query = "{ triggerevent { record_model record_id } }"
    counts = []
    for count in (1, 5):
        for index in range(system_queryset(TriggerEvent).count(), count):
            TriggerEvent.objects.record_change(Vault, vault_for(source_owner, name=f"Private source {index}"))
        result_data(execute_schema(schema, query, user=editor))
        with CaptureQueriesContext(connection) as queries:
            assert result_data(execute_schema(schema, query, user=editor)) == {
                "triggerevent": [{"record_model": None, "record_id": None}] * count,
            }
        counts.append(len(queries))
    assert counts[0] == counts[1]


def test_resource_installs_trigger_disabled(trigger_surface, tmp_path, execution):
    """A declaration cannot choose an actor or silently activate its policy."""
    _schema, workflow, editor, _viewer, _starter = trigger_surface
    source = "100_workflows.trigger.yaml"
    workflow_source = "000_workflows.workflow.yaml"
    owner = make_addon(path=tmp_path, resources={"demo": [{"path": workflow_source}, {"path": source}]})
    (tmp_path / workflow_source).write_text(yaml.safe_dump({"rows": [{"xref": "graph", "fields": {
        "key": "installed-trigger-workflow", "name": workflow.name, "draft": document("entry"),
    }}]}))
    fields = {
        "workflow": f"{owner.name}.graph", "source": "record_changed", "model_label": "knowledge.vault",
        "condition": {}, "enabled": True,
    }
    (tmp_path / source).write_text(yaml.safe_dump({"rows": [{"xref": "trigger", "fields": fields}]}))
    result = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert result.created == 2
    trigger = system_queryset(Trigger).get()
    assert not trigger.enabled and trigger.disabled_reason == ""
    Trigger.objects.enable(trigger, actor=execution[0])
    unchanged = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert unchanged.skipped == 2
    assert system_queryset(Trigger).get(pk=trigger.pk).enabled
    fields["condition"] = {"name": {"_eq": "Ready"}}
    (tmp_path / source).write_text(yaml.safe_dump({"rows": [{"xref": "trigger", "fields": fields}]}))
    replaced = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert replaced.updated == 1
    trigger = system_queryset(Trigger).get(pk=trigger.pk)
    assert not trigger.enabled
