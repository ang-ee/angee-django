"""Workflow installation and additive composition through native resources."""

from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.db import IntegrityError, transaction
from rebac import system_context

from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from angee.workflows.definition import (
    Definition,
    DefinitionInvalid,
    SourcedWorkflowExtension,
    WorkflowExtension,
)
from angee.workflows.steps import Step
from angee.workflows.testing.models import Workflow, WorkflowVersion
from tests.conftest import make_addon, write_addon_manifest

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


class InstallProbe(Step[None, None, None]):
    """Registered graph declaration used by resource installation contracts."""

    key = "install_probe"


def _extensible_document() -> dict[str, Any]:
    """Return the base graph used by the consumer-extension example."""

    return {
        "nodes": {
            "entry": {"step": "install_probe", "next": {"done": "finish"}},
            "finish": {"step": "install_probe"},
        },
        "points": {"after_entry": {"after": "entry"}},
    }


def _write_workflow(path: Path, draft: dict[str, Any]) -> str:
    """Write one base workflow resource and return its relative source."""

    source = "100_workflows.workflow.yaml"
    (path / source).write_text(yaml.safe_dump({"rows": [{
        "xref": "extensible",
        "fields": {"key": "extensible", "name": "Extensible", "draft": draft},
    }]}))
    return source


def _extension_addon(tmp_path: Path):
    """Materialize the checked-in extcontrib reference as a fixture addon."""

    path = tmp_path / "consumer"
    path.mkdir()
    source = "100_workflows.workflowextension.yaml"
    copyfile(
        Path(__file__).parent / "extcontrib/resources/install" / source,
        path / source,
    )
    return make_addon(
        name="tests.workflow_extension_consumer",
        path=path,
        resources={"demo": [{"path": source, "kind": "workflows.workflowextension"}]},
    )


def _contribution(addon: str, key: str, *, sequence: int) -> SourcedWorkflowExtension:
    """Build a one-node fragment for deterministic ordering assertions."""

    return SourcedWorkflowExtension(
        addon,
        WorkflowExtension.model_validate({
            "extends": "extensible",
            "at": "after_entry",
            "sequence": sequence,
            "nodes": {key: {"step": "install_probe"}},
        }),
    )


def test_definition_orders_contributions_by_addon_then_sequence() -> None:
    """Addon order is primary; sequence orders declarations from one addon."""

    base = Definition.model_validate(_extensible_document())
    composed = base.with_extensions((
        _contribution("consumer.first", "first_20", sequence=20),
        _contribution("consumer.second", "second_0", sequence=0),
        _contribution("consumer.first", "first_10", sequence=10),
    ))
    assert composed.nodes["entry"].next == {"done": "first_10"}
    assert composed.nodes["first_10"].next == {"done": "first_20"}
    assert composed.nodes["first_20"].next == {"done": "second_0"}
    assert composed.nodes["second_0"].next == {"done": "finish"}
    assert [item.addon for item in composed.contributions] == [
        "consumer.first", "consumer.first", "consumer.second",
    ]


def test_definition_composes_before_point_edges_and_bindings() -> None:
    """Extension rows use the definition's native edge and input vocabulary."""

    base = Definition.model_validate({
        **_extensible_document(),
        "points": {"before_finish": {"before": "finish"}},
    })
    extension = WorkflowExtension.model_validate({
        "extends": "extensible",
        "at": "before_finish",
        "nodes": {
            "review": {"step": "install_probe"},
            "record": {"step": "install_probe"},
        },
        "edges": [{"from": "review", "to": "record"}],
        "bindings": {"review": {"reason": {"value": "extension"}}},
    })
    composed = base.with_extensions((SourcedWorkflowExtension("consumer", extension),))
    assert composed.nodes["entry"].next == {"done": "review"}
    assert composed.nodes["review"].next == {"done": "record"}
    assert composed.nodes["review"].model_dump(mode="json", by_alias=True)["input"] == {
        "reason": {"value": "extension"},
    }
    assert composed.nodes["record"].next == {"done": "finish"}


def test_resource_composes_extensions_without_editing_the_base_draft(
    composed_tables: None,
    tmp_path: Path,
    monkeypatch,
    register_step,
) -> None:
    """Contribution changes and removal republish the immutable composed graph."""

    register_step(InstallProbe)
    base_path = tmp_path / "base"
    base_path.mkdir()
    base_document = _extensible_document()
    base = make_addon(
        name="tests.workflow_extension_base",
        path=base_path,
        resources={"demo": [{"path": _write_workflow(base_path, base_document)}]},
    )
    consumer = _extension_addon(tmp_path)

    Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    workflow = Workflow.system_queryset().get(key="extensible")
    assert workflow.draft == base_document
    assert workflow.published.document["nodes"]["entry"]["next"] == {"done": "contributed_review"}
    assert workflow.published.document["nodes"]["contributed_review"]["next"] == {"done": "finish"}
    assert workflow.published.document["contributions"] == [{
        "addon": consumer.name,
        "extends": "extensible",
        "at": "after_entry",
        "sequence": 20,
        "nodes": ["contributed_review"],
        "fingerprint": workflow.published.definition.contributions[0].fingerprint,
    }]
    first_version = workflow.published_id

    try:
        with monkeypatch.context() as patch:
            base.apps = apps
            base.models = {}
            patch.setitem(apps.app_configs, base.label, base)
            apps.clear_cache()
            with system_context(reason="load one workflow resource without dropping composed contributions"):
                loaded = Resource.objects.load_xref(
                    f"{base.name}.extensible", model=Workflow, allow_non_dev=True,
                )
    finally:
        apps.clear_cache()
    assert loaded.published_id == first_version
    assert "contributed_review" in loaded.published.document["nodes"]
    assert loaded.draft == base_document

    replay = Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    loaded.refresh_from_db()
    assert loaded.published_id == first_version and replay.skipped == 1

    extension_path = Path(consumer.path) / "100_workflows.workflowextension.yaml"
    changed = yaml.safe_load(extension_path.read_text())
    changed["rows"][0]["nodes"]["contributed_review"]["label"] = "Changed contribution"
    extension_path.write_text(yaml.safe_dump(changed))
    Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    workflow.refresh_from_db()
    assert workflow.published_id != first_version
    assert workflow.published.document["nodes"]["contributed_review"]["label"] == "Changed contribution"
    assert workflow.draft == base_document
    changed_version = workflow.published_id

    replay = Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    workflow.refresh_from_db()
    assert workflow.published_id == changed_version and replay.skipped == 1

    write_addon_manifest(consumer, resources={"demo": []})
    Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    workflow.refresh_from_db()
    assert workflow.published_id != changed_version
    assert "contributed_review" not in workflow.published.document["nodes"]
    assert workflow.draft == base_document


@pytest.mark.parametrize(
    ("at", "node_key", "message"),
    [("missing", "contributed", "unknown point"), ("after_entry", "entry", "duplicate node key")],
)
def test_resource_fails_fast_on_unknown_points_and_node_collisions(
    composed_tables: None,
    tmp_path: Path,
    register_step,
    at: str,
    node_key: str,
    message: str,
) -> None:
    """Invalid cross-addon composition rolls the resource transaction back."""

    register_step(InstallProbe)
    base_path = tmp_path / "base"
    consumer_path = tmp_path / "consumer"
    base_path.mkdir()
    consumer_path.mkdir()
    base = make_addon(
        name="tests.workflow_extension_base_invalid",
        path=base_path,
        resources={"demo": [{"path": _write_workflow(base_path, _extensible_document())}]},
    )
    source = "100_workflows.workflowextension.yaml"
    (consumer_path / source).write_text(yaml.safe_dump({"rows": [{
        "extends": "extensible",
        "at": at,
        "nodes": {node_key: {"step": "install_probe"}},
    }]}))
    consumer = make_addon(
        name="tests.workflow_extension_consumer_invalid",
        path=consumer_path,
        resources={"demo": [{"path": source, "kind": "workflows.workflowextension"}]},
    )
    with pytest.raises((ResourceLoadError, DefinitionInvalid), match=message):
        Resource.objects.load_addons((base, consumer), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert not Workflow.system_queryset().filter(key="extensible").exists()


@pytest.mark.parametrize("publish", [True, False, None])
def test_resource_row_installs_one_document_and_reuses_its_hash(
    composed_tables: None,
    tmp_path: Any,
    register_step,
    publish: bool | None,
) -> None:
    """Native imports retain ledger identity and support draft-only installation."""

    register_step(InstallProbe)
    fields: dict[str, Any] = {
        "key": "resource-graph",
        "name": "Resource graph",
        "draft": {"nodes": {"entry": {"step": "install_probe"}}},
    }
    if publish is not None:
        fields["publish"] = publish
    source = "100_workflows.workflow.yaml"
    (tmp_path / source).write_text(yaml.safe_dump({"rows": [{"xref": "graph", "fields": fields}]}))
    owner = make_addon(path=tmp_path, resources={"demo": [{"path": source}]})
    result = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert result.created == 1
    with system_context(reason="read installed workflow resource"):
        workflow = Workflow.objects.get(key="resource-graph")
        expected_versions = 0 if publish is False else 1
        assert WorkflowVersion.objects.filter(workflow=workflow).count() == expected_versions
        version_id = workflow.published_id
    replay = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert replay.skipped == 1
    with system_context(reason="read replayed workflow resource"):
        workflow.refresh_from_db()
        assert workflow.published_id == version_id
        assert Resource.objects.filter(source_addon=owner.name).count() == 1


@pytest.mark.parametrize("subject_model", ["knowledge.Vault", "knowledge.vault", "knowledge.VAULT"])
def test_install_canonicalizes_subject_model_identity(execution, subject_model):
    """The installation owner stores Django's canonical model identity once."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="canonical_subject",
        name="Canonical subject",
        subject_model=subject_model,
        draft={"nodes": {"entry": {"step": "echo"}}},
        actor=actor,
    )
    assert workflow.subject_model == "knowledge.Vault"
    assert Workflow.system_queryset().get(pk=workflow.pk).subject_model == "knowledge.Vault"
    reinstalled = Workflow.objects.install_definition(
        key=workflow.key,
        name=workflow.name,
        subject_model="knowledge.Vault",
        draft=workflow.draft,
        actor=actor,
    )
    assert reinstalled.subject_model == "knowledge.Vault" and reinstalled.published_id == workflow.published_id


@pytest.mark.parametrize("subject_model", ["knowledge.Missing", "uninstalled.Record", "not_a_label"])
def test_install_rejects_unknown_subject_model_with_issue(execution, subject_model):
    """An invalid identity is diagnosed before an unusable workflow can be installed."""
    actor, _ = execution
    with pytest.raises(DefinitionInvalid) as caught:
        Workflow.objects.install_definition(
            key="unknown_subject",
            name="Unknown subject",
            subject_model=subject_model,
            draft={"nodes": {"entry": {"step": "echo"}}},
            actor=actor,
        )
    assert len(caught.value.issues) == 1
    issue = caught.value.issues[0]
    assert issue.code == "subject_model" and issue.path == ["subject_model"]
    assert subject_model in issue.message
    assert not Workflow.system_queryset().filter(key="unknown_subject").exists()
    assert not WorkflowVersion.system_queryset().exists()


def test_published_version_requires_a_number(execution):
    """The database rejects unnumbered versions, including direct insertions."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="numbered_version",
        name="Numbered version",
        draft={"nodes": {"entry": {"step": "echo"}}},
        actor=actor,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowVersion.system_queryset().create(
            workflow=workflow,
            number=None,
            document=workflow.published.document,
            content_hash=workflow.published.content_hash,
        )
    assert workflow.versions.with_actor(actor).count() == 1
