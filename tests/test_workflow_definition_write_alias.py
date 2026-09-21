"""Definition edits preserve the selected writer through mutation and publication.

Native FK construction can request a database hint, but every query must use
the selected writer, including native model validation.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, models, router
from rebac import system_context

from angee.workflows.definitions import (
    DefinitionEdit,
    EdgeCreate,
    EdgeDelete,
    EndpointRef,
    NodeCreate,
    NodeDelete,
    NodePatch,
)
from angee.workflows.states import TriggerKind
from tests.test_transitions import TransitionRouter
from tests.workflows import Edge, Step, Trigger, Workflow


class DefinitionRouter(TransitionRouter):
    """Allow relations between two aliases of the same physical test database."""

    def allow_relation(self, obj1: models.Model, obj2: models.Model, **hints: Any) -> bool:
        del obj1, obj2, hints
        return True


@pytest.fixture
def definition_writer(workflow_tables: None) -> Iterator[str]:
    """Expose the existing workflow tables through a separate native alias."""

    del workflow_tables
    alias = "workflow_definition_writer"
    connections[alias] = connection.copy(alias=alias)
    try:
        yield alias
    finally:
        connections[alias].close()
        del connections[alias]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection", ["anchor", "manager"])
def test_definition_edit_publish_and_restore_keep_the_selected_alias(
    definition_writer: str, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Preflight, saves, deletes, copied rows and revision writes share one alias."""

    with system_context(reason="definition alias setup"):
        draft = Workflow.objects.create(name="Writer definition")
        entry = Step.objects.create(
            workflow=draft, key="entry", name="Entry", step_class="fixture", is_entry=True
        )
        tail = Step.objects.create(workflow=draft, key="tail", name="Tail", step_class="fixture")
        edge = Edge.objects.create(workflow=draft, source=entry, target=tail)
        draft.refresh_from_db()
    routing = DefinitionRouter("default")
    monkeypatch.setattr(router, "routers", [routing])
    manager = Workflow.objects
    draft._state.db = definition_writer
    if selection == "manager":
        draft._state.db = "unavailable-instance"
        manager = manager.db_manager(definition_writer)

    def reject_default_query(*args: Any) -> None:
        raise AssertionError("Definition mutation must not query the router's conflicting default alias.")

    with connection.execute_wrapper(reject_default_query), system_context(reason="definition alias mutation"):
        changed = manager.apply_definition(
            draft,
            expected_revision=draft.draft_revision,
            edit=DefinitionEdit(
                workflow={"description": "On the writer"},
                node_patches=(NodePatch(entry.pk, {"name": "Renamed"}),),
                node_creates=(NodeCreate("new", {"key": "new", "name": "New", "step_class": "fixture"}),),
                edge_creates=(EdgeCreate("route", EndpointRef(existing_id=entry.pk), EndpointRef(client_key="new")),),
                edge_deletes=(EdgeDelete(edge.pk),),
                node_deletes=(NodeDelete(tail.pk),),
            ),
        )
        published = manager.publish_definition(draft, expected_revision=changed.revision)
        restored = manager.restore_definition(draft, published.publication, expected_revision=changed.revision)
        snapshot = manager.definition_snapshot(draft)
        assert snapshot.workflow.description == "On the writer"
        assert {node.key for node in snapshot.nodes} == {"entry", "new"}
        assert len(snapshot.edges) == 1
        assert restored.snapshot.revision == snapshot.revision
        assert published.publication._state.db == definition_writer
        assert all(row._state.db == definition_writer for row in (*snapshot.nodes, *snapshot.edges))
        assert not Step.objects.using(definition_writer).filter(pk=tail.pk).exists()
        assert not Edge.objects.using(definition_writer).filter(pk=edge.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_model_clean_relation_queries_and_trigger_writes_keep_explicit_alias(
    definition_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncached definition FKs and cached conflicting aliases cannot redirect clean()."""

    with system_context(reason="definition validation setup"):
        draft = Workflow.objects.create(name="Validation writer")
        entry = Step.objects.create(workflow=draft, key="entry", name="Entry", step_class="fixture", is_entry=True)
        tail = Step.objects.create(workflow=draft, key="tail", name="Tail", step_class="fixture")
        draft.publish()
        trigger = Trigger.objects.create(workflow=draft, kind=TriggerKind.MANUAL)
    monkeypatch.setattr(router, "routers", [DefinitionRouter("default")])

    def reject_default_query(*args: Any) -> None:
        raise AssertionError("A clean() relation query lost the explicitly selected alias.")

    with connection.execute_wrapper(reject_default_query), system_context(reason="definition validation mutation"):
        linked = Workflow(name="Linked", published_from_id=draft.pk, error_workflow_id=draft.pk, key=draft.key)
        linked.full_clean_for_write(using=definition_writer, validate_unique=False, validate_constraints=False)
        edge = Edge(workflow_id=draft.pk, source_id=entry.pk, target_id=tail.pk)
        edge.save(using=definition_writer)
        created = Trigger.objects.using(definition_writer).bulk_create([
            Trigger(workflow_id=draft.pk, kind=TriggerKind.MANUAL),
            Trigger(workflow=draft, kind=TriggerKind.MANUAL),
        ])
        enabled = Trigger.objects.db_manager(definition_writer).set_enabled(trigger, enabled=True)
        assert edge._state.db == enabled._state.db == definition_writer
        assert all(row._state.db == definition_writer for row in created)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection", ["explicit", "pinned"])
def test_step_config_hook_reads_deferred_fields_on_its_selected_alias(
    definition_writer: str, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    """The override honors both direct using and dispatcher-pinned context."""

    with system_context(reason="step deferred config alias setup"):
        workflow = Workflow.objects.create(name="Deferred config")
        step = Step.objects.create(
            workflow=workflow, key="entry", name="Entry", step_class="fixture", config={"retained": 3},
        )
        deferred = Step.objects.only("pk").get(pk=step.pk)
    assert {"config", "step_class"} <= deferred.get_deferred_fields()
    deferred._state.db = "default" if selection == "explicit" else definition_writer
    monkeypatch.setattr(router, "routers", [DefinitionRouter("default")])

    def reject_default_query(*args: Any) -> None:
        raise AssertionError("Step config validation lost its selected alias.")

    with connection.execute_wrapper(reject_default_query), system_context(reason="step deferred config validation"):
        deferred.validate_impl_configs(**({"using": definition_writer} if selection == "explicit" else {}))
        assert deferred.config == {"retained": 3}


@pytest.mark.django_db(transaction=True)
def test_edge_save_rejects_stale_cached_endpoint_ancestry_on_default(workflow_tables: None) -> None:
    """Moving a previously cached endpoint cannot bypass the persisted ancestry proof."""

    del workflow_tables
    with system_context(reason="definition stale endpoint validation"):
        original = Workflow.objects.create(name="Original")
        replacement = Workflow.objects.create(name="Replacement")
        source = Step.objects.create(workflow=original, key="source", name="Source", step_class="fixture")
        target = Step.objects.create(workflow=original, key="target", name="Target", step_class="fixture")
        edge = Edge(workflow=original, source=source, target=target)
        moved = Step.objects.get(pk=source.pk)
        moved.workflow = replacement
        moved.save()
        with pytest.raises(ValidationError, match="source must belong"):
            edge.save()
