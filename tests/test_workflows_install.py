"""Whole-document installation through the native resource import pipeline."""

from typing import Any

import pytest
import yaml
from django.db import IntegrityError, transaction
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.resources.testing.models import Resource
from angee.workflows.definition import DefinitionInvalid
from angee.workflows.steps import Step
from angee.workflows.testing.models import Workflow, WorkflowVersion
from tests.conftest import make_addon

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


class InstallProbe(Step[None, None, None]):
    """Registered graph declaration used by resource installation contracts."""

    key = "install_probe"


@pytest.mark.parametrize("publish", [True, False, None])
def test_resource_row_installs_one_document_and_reuses_its_hash(
    composed_tables: None, tmp_path: Any, register_step, publish: bool | None,
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
        assert Resource.objects.count() == 1


@pytest.mark.parametrize("subject_model", ["knowledge.Vault", "knowledge.vault", "knowledge.VAULT"])
def test_install_canonicalizes_subject_model_identity(execution, subject_model):
    """The installation owner stores Django's canonical model identity once."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="canonical_subject", name="Canonical subject", subject_model=subject_model,
        draft={"nodes": {"entry": {"step": "echo"}}}, actor=actor,
    )
    assert workflow.subject_model == "knowledge.Vault"
    assert system_queryset(Workflow).get(pk=workflow.pk).subject_model == "knowledge.Vault"
    reinstalled = Workflow.objects.install_definition(
        key=workflow.key, name=workflow.name, subject_model="knowledge.Vault",
        draft=workflow.draft, actor=actor,
    )
    assert reinstalled.subject_model == "knowledge.Vault" and reinstalled.published_id == workflow.published_id


@pytest.mark.parametrize("subject_model", ["knowledge.Missing", "uninstalled.Record", "not_a_label"])
def test_install_rejects_unknown_subject_model_with_issue(execution, subject_model):
    """An invalid identity is diagnosed before an unusable workflow can be installed."""
    actor, _ = execution
    with pytest.raises(DefinitionInvalid) as caught:
        Workflow.objects.install_definition(
            key="unknown_subject", name="Unknown subject", subject_model=subject_model,
            draft={"nodes": {"entry": {"step": "echo"}}}, actor=actor,
        )
    assert len(caught.value.issues) == 1
    issue = caught.value.issues[0]
    assert issue.code == "subject_model" and issue.path == ["subject_model"]
    assert subject_model in issue.message
    assert not system_queryset(Workflow).filter(key="unknown_subject").exists()
    assert not system_queryset(WorkflowVersion).exists()


def test_published_version_requires_a_number(execution):
    """The database rejects unnumbered versions, including direct insertions."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="numbered_version", name="Numbered version",
        draft={"nodes": {"entry": {"step": "echo"}}}, actor=actor,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        system_queryset(WorkflowVersion).create(
            workflow=workflow, number=None, document=workflow.published.document,
            content_hash=workflow.published.content_hash,
        )
    assert workflow.versions.with_actor(actor).count() == 1
