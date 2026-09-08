"""Atomic workflow definition command regressions."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from rebac import actor_context, app_settings, system_context
from rebac.roles import grant

from angee.workflows.definitions import (
    DefinitionEdit,
    DefinitionEditError,
    DefinitionReadinessError,
    EdgeCreate,
    EdgePatch,
    EndpointRef,
    NodeCreate,
    NodeDelete,
    NodePatch,
    StaleDefinitionError,
)
from tests.workflows import Edge, Step, Workflow

pytest_plugins = ("tests.workflows",)
User = get_user_model()


def _draft() -> tuple[Workflow, Step, Step, Edge]:
    with system_context(reason="test definition command setup"):
        workflow = Workflow.objects.create(name="Draft")
        entry = Step.objects.create(
            workflow=workflow,
            key="entry",
            name="Entry",
            step_class="agent_session",
            is_entry=True,
        )
        tail = Step.objects.create(
            workflow=workflow,
            key="tail",
            name="Tail",
            step_class="agent_session",
        )
        edge = Edge.objects.create(workflow=workflow, source=entry, target=tail)
        workflow.refresh_from_db()
    return workflow, entry, tail, edge


def test_command_creates_correlated_rows_and_returns_committed_revision_and_readiness(
    workflow_tables: None,
) -> None:
    del workflow_tables
    workflow, _entry, tail, _edge = _draft()
    revision = workflow.draft_revision
    edit = DefinitionEdit(
        node_creates=(
            NodeCreate(
                "new-wait",
                {"key": "wait", "name": "Wait", "step_class": "wait", "config": {}, "is_entry": False},
            ),
        ),
        edge_creates=(
            EdgeCreate(
                "new-edge",
                EndpointRef(existing_id=tail.pk),
                EndpointRef(client_key="new-wait"),
            ),
        ),
        edge_patches=(EdgePatch(_edge.pk, target=EndpointRef(client_key="new-wait")),),
    )

    with system_context(reason="test definition command"):
        result = Workflow.objects.apply_definition(workflow, expected_revision=revision, edit=edit)

    assert result.revision == revision + 1
    assert result.nodes[0].client_key == "new-wait"
    assert result.edges[0].client_key == "new-edge"
    with system_context(reason="test correlated definition rows"):
        assert Step.objects.filter(pk=result.nodes[0].identity, workflow=workflow).exists()
        assert Edge.objects.filter(pk=result.edges[0].identity, workflow=workflow).exists()
    assert any(diagnostic.location.field == "config.until" for diagnostic in result.readiness)


def test_command_preserves_ids_and_deletes_incident_edges_once(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, edge = _draft()

    with system_context(reason="test definition patch"):
        result = Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(
                workflow={"description": "Edited"},
                node_patches=(NodePatch(entry.pk, {"name": "Renamed"}),),
                node_deletes=(NodeDelete(tail.pk),),
            ),
        )

    entry.refresh_from_db()
    workflow.refresh_from_db()
    assert entry.name == "Renamed"
    assert entry.pk is not None
    assert workflow.description == "Edited"
    assert result.revision == workflow.draft_revision
    with system_context(reason="test deleted definition rows"):
        assert not Step.objects.filter(pk=tail.pk).exists()
        assert not Edge.objects.filter(pk=edge.pk).exists()


def test_stale_or_structurally_invalid_command_rolls_back_without_revision_change(
    workflow_tables: None,
) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()
    revision = workflow.draft_revision

    with system_context(reason="test definition conflict"):
        with pytest.raises(StaleDefinitionError) as stale:
            Workflow.objects.apply_definition(
                workflow,
                expected_revision=revision - 1,
                edit=DefinitionEdit(node_patches=(NodePatch(entry.pk, {"name": "Lost"}),)),
            )
        with pytest.raises(DefinitionEditError) as invalid:
            Workflow.objects.apply_definition(
                workflow,
                expected_revision=revision,
                edit=DefinitionEdit(
                    node_creates=(
                        NodeCreate(
                            "duplicate",
                            {"key": "entry", "name": "Duplicate", "step_class": "agent_session"},
                        ),
                    )
                ),
            )

    workflow.refresh_from_db()
    entry.refresh_from_db()
    assert stale.value.current == revision
    assert {item.code for item in invalid.value.diagnostics} >= {"node_key_duplicate"}
    assert workflow.draft_revision == revision
    assert entry.name == "Entry"
    with system_context(reason="test definition rollback read"):
        assert not Step.objects.filter(workflow=workflow, name="Duplicate").exists()


def test_invalid_client_references_and_conflicting_edits_are_non_oracular(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, edge = _draft()
    edit = DefinitionEdit(
        node_patches=(NodePatch(entry.pk, {"name": "One"}), NodePatch(entry.pk, {"name": "Two"})),
        node_deletes=(NodeDelete(tail.pk),),
        edge_patches=(),
        edge_creates=(
            EdgeCreate(
                "edge",
                EndpointRef(existing_id=999999),
                EndpointRef(existing_id=tail.pk, client_key="both"),
            ),
        ),
    )

    with system_context(reason="test invalid definition references"), pytest.raises(DefinitionEditError) as caught:
        Workflow.objects.apply_definition(workflow, expected_revision=workflow.draft_revision, edit=edit)

    diagnostics = caught.value.diagnostics
    assert sum(item.code == "reference_invalid" for item in diagnostics) == 2
    assert any(item.code == "identity_duplicate" for item in diagnostics)
    with system_context(reason="test invalid definition reference read"):
        assert Edge.objects.filter(pk=edge.pk).exists()


def test_noop_and_snapshot_share_one_revision_owner(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, edge = _draft()

    with system_context(reason="test definition snapshot"):
        result = Workflow.objects.apply_definition(
            workflow, expected_revision=workflow.draft_revision, edit=DefinitionEdit()
        )
        snapshot = Workflow.objects.definition_snapshot(workflow)

    assert result.revision == workflow.draft_revision
    assert snapshot.revision == result.revision
    assert [row.pk for row in snapshot.nodes] == [entry.pk, tail.pk]
    assert [row.pk for row in snapshot.edges] == [edge.pk]
    assert snapshot.readiness == ()


def test_source_preview_rejects_an_ambiguous_target_reference(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()

    with system_context(reason="test ambiguous preview target"):
        with pytest.raises(DefinitionEditError, match="Target is missing or unavailable"):
            Workflow.objects.definition_input_sources(
                workflow,
                expected_revision=workflow.draft_revision,
                edit=DefinitionEdit(),
                target=EndpointRef(existing_id=entry.pk, client_key="also-new"),
            )


@pytest.mark.parametrize(
    "binding",
    [
        {"kind": {}},
        {"kind": []},
        {"kind": "missing"},
        {},
        {
            "kind": "object",
            "fields": {"a.b[]/kind": {"kind": "array", "items": [{"kind": "constant", "value": None}, {}]}},
        },
    ],
)
def test_manager_saves_malformed_binding_discriminators_as_readiness_issues(
    workflow_tables: None,
    binding: dict[str, object],
) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()

    with system_context(reason="test malformed binding draft"):
        result = Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(node_patches=(NodePatch(entry.pk, {"input_binding": binding}),)),
        )
        entry.refresh_from_db()

    assert entry.input_binding == binding
    assert any(
        diagnostic.code == "binding_invalid" and diagnostic.location.field == "input_binding"
        for diagnostic in result.readiness
    )
    if "fields" in binding:
        diagnostic = next(item for item in result.readiness if item.code == "binding_invalid")
        assert diagnostic.message == "Choose a value type."
        assert diagnostic.location.detail_path == ("fields", "a.b[]/kind", "items", 1)


def test_key_swap_is_explicit_and_map_config_is_never_rewritten(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, _edge = _draft()
    opaque_config = {"target_step": "entry", "items": "input.rows", "extension": {"kept": True}}
    with system_context(reason="test opaque Map setup"):
        map_row = Step.objects.create(
            workflow=workflow,
            key="map",
            name="Map",
            step_class="map",
            config=opaque_config,
        )
        workflow.refresh_from_db()
        with pytest.raises(DefinitionEditError) as caught:
            Workflow.objects.apply_definition(
                workflow,
                expected_revision=workflow.draft_revision,
                edit=DefinitionEdit(
                    node_patches=(
                        NodePatch(entry.pk, {"key": "tail"}),
                        NodePatch(tail.pk, {"key": "entry"}),
                    )
                ),
            )
        result = Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(
                node_patches=(NodePatch(entry.pk, {"key": "start"}),),
                node_creates=(
                    NodeCreate(
                        "replacement-entry",
                        {"key": "entry", "name": "Replacement", "step_class": "agent_session"},
                    ),
                ),
            ),
        )
        map_row.refresh_from_db()

    assert "key_swap_unsupported" in {item.code for item in caught.value.diagnostics}
    assert map_row.config == opaque_config
    assert result.nodes[0].client_key == "replacement-entry"
    assert not any(item.code == "map_target_missing" for item in result.readiness)


def test_command_honors_actor_scoping_and_snapshot_reads_immutable_versions(workflow_tables: None) -> None:
    del workflow_tables
    admin = User.objects.create_superuser(username="definition-admin", email="definition@example.com")
    outsider = User.objects.create_user(username="definition-outsider")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    workflow, entry, _tail, _edge = _draft()

    with actor_context(outsider), pytest.raises(ObjectDoesNotExist):
        Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(workflow={"description": "Denied"}),
        )

    with actor_context(admin):
        bound = Workflow.objects.with_action("write").get(pk=workflow.pk)
    result = Workflow.objects.apply_definition(
        bound,
        expected_revision=bound.draft_revision,
        edit=DefinitionEdit(
            workflow={"description": "Allowed"},
            node_creates=(NodeCreate("audit-node", {"key": "audit", "name": "Audit", "step_class": "agent_session"}),),
            edge_creates=(
                EdgeCreate("audit-edge", EndpointRef(existing_id=entry.pk), EndpointRef(client_key="audit-node")),
            ),
        ),
    )
    with actor_context(admin):
        created_node = Step.objects.get(pk=result.nodes[0].identity)
        created_edge = Edge.objects.get(pk=result.edges[0].identity)
        published = workflow.publish()
        snapshot = Workflow.objects.definition_snapshot(published)

    assert result.revision == workflow.draft_revision + 1
    assert snapshot.workflow.pk == published.pk
    assert snapshot.revision == published.draft_revision
    assert (created_node.created_by_id, created_node.updated_by_id) == (admin.pk, admin.pk)
    assert (created_edge.created_by_id, created_edge.updated_by_id) == (admin.pk, admin.pk)


def test_publish_command_uses_exact_revision_readiness_and_idempotent_snapshot(workflow_tables: None) -> None:
    del workflow_tables
    workflow, _entry, _tail, _edge = _draft()
    with system_context(reason="test exact definition publication"):
        with pytest.raises(StaleDefinitionError):
            Workflow.objects.publish_definition(workflow, expected_revision=workflow.draft_revision - 1)
        first = Workflow.objects.publish_definition(workflow, expected_revision=workflow.draft_revision)
        second = Workflow.objects.publish_definition(workflow, expected_revision=workflow.draft_revision)

        incomplete = Workflow.objects.create(name="Incomplete")
        with pytest.raises(DefinitionReadinessError) as caught:
            Workflow.objects.publish_definition(incomplete, expected_revision=incomplete.draft_revision)

    assert first.created is True
    assert second.created is False
    assert second.publication.pk == first.publication.pk
    assert first.publication.draft_revision == first.revision
    assert {item.code for item in caught.value.diagnostics} == {"entry_count"}


@pytest.mark.parametrize("revision", [-1, True, 2_147_483_648])
def test_command_rejects_invalid_revision_tokens(workflow_tables: None, revision: object) -> None:
    del workflow_tables
    workflow, _entry, _tail, _edge = _draft()
    with system_context(reason="test invalid revision"), pytest.raises(DefinitionEditError) as caught:
        Workflow.objects.apply_definition(workflow, expected_revision=revision, edit=DefinitionEdit())  # type: ignore[arg-type]
    with system_context(reason="test invalid publish revision"), pytest.raises(DefinitionEditError) as publish:
        Workflow.objects.publish_definition(workflow, expected_revision=revision)  # type: ignore[arg-type]
    assert {item.code for item in caught.value.diagnostics} == {"revision_invalid"}
    assert {item.code for item in publish.value.diagnostics} == {"revision_invalid"}


def test_explicit_null_model_fields_are_structural_and_atomic(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, _tail, edge = _draft()
    with system_context(reason="test definition null fields"), pytest.raises(DefinitionEditError) as caught:
        Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(
                workflow={"description": None, "error_workflow": Workflow(name="Unavailable")},
                node_patches=(NodePatch(entry.pk, {"name": None}),),
                edge_patches=(EdgePatch(edge.pk, {"condition": None}),),
            ),
        )
    assert {(item.location.kind, item.location.field) for item in caught.value.diagnostics} >= {
        ("workflow", "description"),
        ("workflow", "error_workflow"),
        ("node", "name"),
        ("edge", "condition"),
    }
    workflow.refresh_from_db()
    entry.refresh_from_db()
    edge.refresh_from_db()
    assert (workflow.description, entry.name, edge.condition) == ("", "Entry", "")


def test_snapshot_orders_multiple_disconnected_persisted_nodes(workflow_tables: None) -> None:
    del workflow_tables
    workflow, _entry, _tail, _edge = _draft()
    with system_context(reason="test disconnected definition snapshot"):
        first = Step.objects.create(workflow=workflow, key="zeta", name="Zeta", step_class="agent_session")
        second = Step.objects.create(workflow=workflow, key="alpha", name="Alpha", step_class="agent_session")
        workflow.refresh_from_db()
        snapshot = Workflow.objects.definition_snapshot(workflow)
    unreachable = [item for item in snapshot.readiness if item.code == "unreachable"]
    assert [item.location.key.existing_id for item in unreachable] == sorted([first.pk, second.pk])


def test_existing_edge_signature_swap_is_rejected_before_persistence(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, first = _draft()
    with system_context(reason="test edge signature swap setup"):
        second = Edge.objects.create(workflow=workflow, source=entry, target=tail, condition="other")
        workflow.refresh_from_db()
        with pytest.raises(DefinitionEditError) as caught:
            Workflow.objects.apply_definition(
                workflow,
                expected_revision=workflow.draft_revision,
                edit=DefinitionEdit(
                    edge_patches=(
                        EdgePatch(first.pk, {"condition": "other"}),
                        EdgePatch(second.pk, {"condition": ""}),
                    )
                ),
            )
        first.refresh_from_db()
        second.refresh_from_db()
    assert {item.code for item in caught.value.diagnostics} == {"edge_swap_unsupported"}
    assert (first.condition, second.condition) == ("", "other")


def test_command_rolls_back_after_a_late_persistence_failure(
    workflow_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()
    revision = workflow.draft_revision
    original_save = Edge.save

    def fail_edge_save(instance: Edge, *args: object, **kwargs: object) -> None:
        if instance.pk is None:
            raise RuntimeError("late edge failure")
        original_save(instance, *args, **kwargs)

    monkeypatch.setattr(Edge, "save", fail_edge_save)
    with system_context(reason="test late definition rollback"), pytest.raises(RuntimeError):
        Workflow.objects.apply_definition(
            workflow,
            expected_revision=revision,
            edit=DefinitionEdit(
                workflow={"description": "must roll back"},
                node_creates=(
                    NodeCreate("created", {"key": "created", "name": "Created", "step_class": "agent_session"}),
                ),
                edge_creates=(
                    EdgeCreate("created-edge", EndpointRef(existing_id=entry.pk), EndpointRef(client_key="created")),
                ),
            ),
        )
    workflow.refresh_from_db()
    assert workflow.description == ""
    assert workflow.draft_revision == revision
    with system_context(reason="test late definition rollback read"):
        assert not Step.objects.filter(workflow=workflow, key="created").exists()


def test_command_tags_client_identity_separately_and_reports_malformed_key(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()
    with system_context(reason="test command identity tags"):
        result = Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(
                node_creates=(
                    NodeCreate(
                        str(entry.pk), {"key": "numeric-client", "name": "Client", "step_class": "agent_session"}
                    ),
                ),
            ),
        )
        with pytest.raises(DefinitionEditError) as caught:
            Workflow.objects.apply_definition(
                workflow,
                expected_revision=result.revision,
                edit=DefinitionEdit(node_patches=(NodePatch(entry.pk, {"key": []}),)),
            )

    assert result.nodes[0].client_key == str(entry.pk)
    assert any(item.code == "field_invalid" and item.location.field == "key" for item in caught.value.diagnostics)
