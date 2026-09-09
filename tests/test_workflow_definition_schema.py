"""Native GraphQL coverage for workflow definition commands."""

from __future__ import annotations

import pytest
from rebac import system_context

from tests.conftest import execute_schema, result_data
from tests.test_workflows import _console_schema, _platform_admin
from tests.workflows import Step, Workflow

SAVE = """
mutation SaveDefinition($workflow: ID!, $revision: Int!, $edit: WorkflowDefinitionEditInput!) {
  save_workflow_definition(workflow: $workflow, expected_revision: $revision, edit: $edit) {
    status revision current_revision
    nodes { client_key id }
    edges { client_key id }
    diagnostics { code message kind id client_key field }
  }
}
"""

SOURCES = """
query InputSources(
  $workflow: ID!
  $revision: Int!
  $edit: WorkflowDefinitionEditInput!
  $target: WorkflowEndpointInput!
) {
  workflow_input_sources(
    workflow: $workflow
    expected_revision: $revision
    edit: $edit
    target: $target
  ) {
    status revision current_revision
    sources {
      kind id client_key step_key label
      contract { root_node_id nodes { id kind } edges { parent_node_id child_node_id kind key } }
    }
    diagnostics { code field detail_path id client_key }
  }
}
"""

MAP_BODIES = """
query MapBodies(
  $workflow: ID!
  $revision: Int!
  $edit: WorkflowDefinitionEditInput!
  $owner: WorkflowEndpointInput!
) {
  workflow_map_body_candidates(
    workflow: $workflow
    expected_revision: $revision
    edit: $edit
    owner: $owner
  ) {
    status revision current_revision
    candidates { id client_key step_key label eligible reason }
    diagnostics { code field id client_key }
  }
}
"""

COMPARE = """
query CompareDefinition($workflow: ID!, $source: ID!) {
  workflow_definition_comparison(workflow: $workflow, source: $source) {
    source_id source_version draft_id draft_revision
    counts { steps_added steps_removed steps_changed connections_added connections_removed settings_changed }
    changes { kind change key field before after presentation_only }
  }
}
"""

RESTORE = """
mutation RestoreDefinition($workflow: ID!, $source: ID!, $revision: Int!) {
  restore_workflow_definition(workflow: $workflow, source: $source, expected_revision: $revision) {
    status current_revision
    snapshot { workflow { id status } revision nodes { key name } edges { source target condition } }
  }
}
"""


def _draft() -> tuple[Workflow, Step]:
    with system_context(reason="test definition GraphQL setup"):
        workflow = Workflow.objects.create(name="Definition API")
        entry = Step.objects.create(
            workflow=workflow,
            key="entry",
            name="Entry",
            step_class="agent_session",
            is_entry=True,
        )
        workflow.refresh_from_db()
    return workflow, entry


def test_version_comparison_and_restore_keep_one_coherent_saved_revision(workflow_tables: None) -> None:
    del workflow_tables
    admin = _platform_admin("definition-version-admin")
    workflow, entry = _draft()
    with system_context(reason="definition version GraphQL setup"):
        publication = workflow.publish()
        workflow.refresh_from_db()
        entry.name = "Later draft"
        entry.save(update_fields={"name"})
        workflow.refresh_from_db()

    variables = {"workflow": workflow.sqid, "source": publication.sqid}
    comparison = result_data(execute_schema(_console_schema(), COMPARE, variables, user=admin))[
        "workflow_definition_comparison"
    ]
    assert comparison["source_id"] == publication.sqid
    assert comparison["draft_revision"] == workflow.draft_revision
    assert comparison["counts"]["steps_changed"] == 1

    stale = result_data(
        execute_schema(_console_schema(), RESTORE, {**variables, "revision": workflow.draft_revision - 1}, user=admin)
    )["restore_workflow_definition"]
    assert stale["status"] == "STALE"
    restored = result_data(
        execute_schema(_console_schema(), RESTORE, {**variables, "revision": workflow.draft_revision}, user=admin)
    )["restore_workflow_definition"]
    assert restored["status"] == "SUCCESS"
    assert restored["snapshot"]["workflow"]["id"] == workflow.sqid
    assert restored["snapshot"]["nodes"][0]["name"] == "Entry"


def test_definition_mutation_preserves_omission_correlates_rows_and_reports_readiness(
    workflow_tables: None,
) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-schema-admin")
    workflow, entry = _draft()
    client_key = str(entry.pk)
    variables = {
        "workflow": workflow.sqid,
        "revision": workflow.draft_revision,
        "edit": {
            "workflow": {"description": "Saved"},
            "node_creates": [
                {
                    "client_key": client_key,
                    "fields": {
                        "key": "wait",
                        "name": "Wait",
                        "step_class": "wait",
                        "config": {},
                        "input_binding": {"kind": "step_output"},
                    },
                }
            ],
            "edge_creates": [
                {
                    "client_key": "edge-client",
                    "source": {"id": entry.sqid},
                    "target": {"client_key": client_key},
                }
            ],
        },
    }

    payload = result_data(execute_schema(schema, SAVE, variables, user=admin))["save_workflow_definition"]

    assert payload["status"] == "SUCCESS", payload
    assert payload["revision"] == workflow.draft_revision + 1
    assert payload["nodes"][0]["client_key"] == client_key
    assert payload["edges"][0]["client_key"] == "edge-client"
    assert any(item["field"] == "config.until" for item in payload["diagnostics"])
    assert any(item["code"] == "binding_invalid" for item in payload["diagnostics"])
    with system_context(reason="test definition GraphQL result"):
        workflow.refresh_from_db()
    assert workflow.name == "Definition API"
    assert workflow.description == "Saved"
    with system_context(reason="verify incomplete binding draft"):
        assert workflow.steps.get(key="wait").input_binding == {"kind": "step_output"}


def test_definition_mutation_rejects_null_max_steps_without_writes(
    workflow_tables: None,
) -> None:
    """An explicit null limit is a field diagnostic, never a transport failure."""

    del workflow_tables
    admin = _platform_admin("definition-null-max-steps")
    workflow, entry = _draft()
    original_revision = workflow.draft_revision
    with system_context(reason="capture workflow before rejected null limit"):
        original_nodes = list(workflow.steps.values_list("pk", "key", "config"))

    payload = result_data(
        execute_schema(
            _console_schema(),
            SAVE,
            {
                "workflow": workflow.sqid,
                "revision": original_revision,
                "edit": {"workflow": {"max_steps": None}},
            },
            user=admin,
        )
    )["save_workflow_definition"]

    assert payload["status"] == "STRUCTURAL"
    assert [(item["kind"], item["field"]) for item in payload["diagnostics"]] == [
        ("workflow", "max_steps")
    ]
    assert payload["diagnostics"][0]["code"] == "field_invalid"
    assert payload["diagnostics"][0]["message"] == "This field cannot be null."
    with system_context(reason="verify rejected null workflow limit"):
        workflow.refresh_from_db()
        assert workflow.draft_revision == original_revision
        assert list(workflow.steps.values_list("pk", "key", "config")) == original_nodes
        assert workflow.steps.get(pk=entry.pk).key == entry.key


def test_source_preview_uses_unsaved_topology_and_keeps_stale_baseline_separate(
    workflow_tables: None,
) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-source-admin")
    workflow, entry = _draft()
    edit = {
        "node_creates": [
            {"client_key": "middle", "fields": {"key": "middle", "name": "Middle", "step_class": "agent_session"}},
            {"client_key": "target", "fields": {"key": "target", "name": "Target", "step_class": "agent_session"}},
        ],
        "edge_creates": [
            {"client_key": "first", "source": {"id": entry.sqid}, "target": {"client_key": "middle"}},
            {"client_key": "second", "source": {"client_key": "middle"}, "target": {"client_key": "target"}},
        ],
    }
    variables = {
        "workflow": workflow.sqid,
        "revision": workflow.draft_revision,
        "edit": edit,
        "target": {"client_key": "target"},
    }

    preview = result_data(execute_schema(schema, SOURCES, variables, user=admin))["workflow_input_sources"]

    assert preview["status"] == "SUCCESS"
    assert preview["revision"] == workflow.draft_revision
    assert {(item["kind"], item["step_key"], item["client_key"]) for item in preview["sources"]} == {
        ("workflow_input", None, None),
        ("step_output", "entry", None),
        ("step_output", "middle", "middle"),
    }
    assert all(item["contract"]["root_node_id"] == 0 for item in preview["sources"])
    with system_context(reason="verify source preview is read only"):
        assert workflow.steps.count() == 1

    stale = result_data(
        execute_schema(schema, SOURCES, {**variables, "revision": workflow.draft_revision - 1}, user=admin)
    )["workflow_input_sources"]
    assert stale == {
        "status": "STALE",
        "revision": None,
        "current_revision": workflow.draft_revision,
        "sources": [],
        "diagnostics": [],
    }

    ambiguous = result_data(
        execute_schema(
            schema,
            SOURCES,
            {**variables, "target": {"id": entry.sqid, "client_key": "target"}},
            user=admin,
        )
    )["workflow_input_sources"]
    assert ambiguous["status"] == "STRUCTURAL"
    assert ambiguous["sources"] == []
    assert ambiguous["diagnostics"][0]["code"] == "reference_invalid"


def test_map_body_preview_uses_prospective_graph_without_persisting_membership(workflow_tables: None) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-map-body-admin")
    workflow, entry = _draft()
    edit = {
        "node_creates": [
            {
                "client_key": "map",
                "fields": {
                    "key": "map",
                    "name": "Map",
                    "step_class": "map",
                    "config": {"items": "input", "target_step": "body"},
                },
            },
            {
                "client_key": "body",
                "fields": {"key": "body", "name": "Body", "step_class": "agent_session"},
            },
            {
                "client_key": "free",
                "fields": {"key": "free", "name": "Free", "step_class": "agent_session"},
            },
        ],
        "edge_creates": [
            {"client_key": "to-map", "source": {"id": entry.sqid}, "target": {"client_key": "map"}},
        ],
    }
    variables = {
        "workflow": workflow.sqid,
        "revision": workflow.draft_revision,
        "edit": edit,
        "owner": {"client_key": "map"},
    }

    preview = result_data(execute_schema(schema, MAP_BODIES, variables, user=admin))["workflow_map_body_candidates"]

    assert preview["status"] == "SUCCESS"
    candidates = {item["step_key"]: item for item in preview["candidates"]}
    assert candidates["body"]["eligible"] is True
    assert candidates["free"]["eligible"] is True
    assert candidates["map"]["eligible"] is False
    with system_context(reason="verify Map body preview is read only"):
        assert workflow.steps.count() == 1


def test_definition_mutation_returns_structural_and_stale_without_losing_data(workflow_tables: None) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-errors-admin")
    workflow, _entry = _draft()

    invalid = result_data(
        execute_schema(
            schema,
            SAVE,
            {
                "workflow": workflow.sqid,
                "revision": workflow.draft_revision,
                "edit": {"workflow": {"description": None}, "node_creates": []},
            },
            user=admin,
        )
    )["save_workflow_definition"]
    stale = result_data(
        execute_schema(
            schema,
            SAVE,
            {"workflow": workflow.sqid, "revision": workflow.draft_revision - 1, "edit": {}},
            user=admin,
        )
    )["save_workflow_definition"]

    assert invalid["status"] == "STRUCTURAL"
    assert any(item["field"] == "description" for item in invalid["diagnostics"])
    assert stale == {
        "status": "STALE",
        "revision": None,
        "current_revision": workflow.draft_revision,
        "nodes": [],
        "edges": [],
        "diagnostics": [],
    }
    with system_context(reason="test rejected definition GraphQL result"):
        workflow.refresh_from_db()
    assert workflow.description == ""


def test_definition_snapshot_and_publish_payloads_are_typed(workflow_tables: None) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-publish-admin")
    workflow, entry = _draft()
    query = """
      query Definition($workflow: ID!) {
        workflow_definition(workflow: $workflow) {
          revision
          workflow {
            id draft_revision lineage_id current_published_id current_published_version publication_status
          }
          nodes { id key config config_errors input_binding }
          edges { id source target condition }
          readiness { code field id client_key }
        }
      }
    """
    mutation = """
      mutation Publish($workflow: ID!, $revision: Int!) {
        publish_workflow_definition(workflow: $workflow, expected_revision: $revision) {
          status revision publication_created publication {
            id version draft_revision lineage_id current_published_id current_published_version publication_status
          }
          diagnostics { code field }
        }
      }
    """

    snapshot = result_data(execute_schema(schema, query, {"workflow": workflow.sqid}, user=admin))[
        "workflow_definition"
    ]
    first = result_data(
        execute_schema(
            schema,
            mutation,
            {"workflow": workflow.sqid, "revision": workflow.draft_revision},
            user=admin,
        )
    )["publish_workflow_definition"]
    second = result_data(
        execute_schema(
            schema,
            mutation,
            {"workflow": workflow.sqid, "revision": workflow.draft_revision},
            user=admin,
        )
    )["publish_workflow_definition"]

    assert snapshot["revision"] == workflow.draft_revision
    assert snapshot["workflow"]["draft_revision"] == workflow.draft_revision
    assert snapshot["workflow"]["lineage_id"] == workflow.sqid
    assert snapshot["workflow"]["publication_status"] == "draft"
    assert snapshot["nodes"] == [
        {"id": entry.sqid, "key": "entry", "config": {}, "config_errors": {}, "input_binding": None}
    ]
    assert snapshot["readiness"] == []
    assert first["status"] == "SUCCESS" and first["publication_created"] is True
    assert second["status"] == "SUCCESS" and second["publication_created"] is False
    assert second["publication"]["id"] == first["publication"]["id"]


def test_native_workflow_create_returns_lineage_projection(workflow_tables: None) -> None:
    """Generic inserts may return a model instance that was not read through the annotated query."""

    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-create-admin")
    created = result_data(
        execute_schema(
            schema,
            """
            mutation {
              insert_workflows_one(object: {name: "Created workflow"}) {
                id name lineage_id current_published_id current_published_version
                current_published_subject_declaration publication_status
              }
            }
            """,
            user=admin,
        )
    )["insert_workflows_one"]

    assert created == {
        "id": created["id"],
        "name": "Created workflow",
        "lineage_id": created["id"],
        "current_published_id": None,
        "current_published_version": None,
        "current_published_subject_declaration": None,
        "publication_status": "draft",
    }


def test_definition_adapter_rejects_unavailable_relations_and_explicit_null_endpoint(workflow_tables: None) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-reference-admin")
    workflow, _entry = _draft()
    unavailable = result_data(
        execute_schema(
            schema,
            SAVE,
            {
                "workflow": workflow.sqid,
                "revision": workflow.draft_revision,
                "edit": {"workflow": {"error_workflow": "wfl_unavailable"}},
            },
            user=admin,
        )
    )["save_workflow_definition"]
    explicit_null = result_data(
        execute_schema(
            schema,
            SAVE,
            {
                "workflow": workflow.sqid,
                "revision": workflow.draft_revision,
                "edit": {"edge_patches": [{"id": "wed_unavailable", "source": None}]},
            },
            user=admin,
        )
    )["save_workflow_definition"]
    assert unavailable["status"] == "STRUCTURAL"
    assert unavailable["diagnostics"][0]["field"] == "error_workflow"
    assert explicit_null["status"] == "STRUCTURAL"
    assert {item["field"] for item in explicit_null["diagnostics"]} >= {"identity", "source"}


def test_definition_adapter_does_not_catch_unexpected_errors(
    workflow_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("definition-unexpected-admin")
    workflow, _entry = _draft()

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected-command-failure")

    monkeypatch.setattr(Workflow.objects, "apply_definition", fail)
    result = execute_schema(
        schema,
        SAVE,
        {"workflow": workflow.sqid, "revision": workflow.draft_revision, "edit": {}},
        user=admin,
    )

    assert result.errors is not None
    assert result.errors[0].message == "An unexpected error occurred."
    assert result.errors[0].extensions == {"code": "INTERNAL"}


def test_legacy_publish_adapter_does_not_collapse_unexpected_errors(
    workflow_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("legacy-publish-unexpected-admin")
    workflow, _entry = _draft()

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected-legacy-publication-failure")

    monkeypatch.setattr(Workflow, "publish", fail)
    result = execute_schema(
        schema,
        "mutation Publish($workflow: ID!) { publish_workflow(workflow: $workflow) { ok message } }",
        {"workflow": workflow.sqid},
        user=admin,
    )
    assert result.errors is not None
    assert result.errors[0].message == "An unexpected error occurred."
    assert result.errors[0].extensions == {"code": "INTERNAL"}
