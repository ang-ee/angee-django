"""Workflow installation and additive composition through native resources."""

from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.db import IntegrityError, transaction
from rebac import actor_context, system_context

from angee.provenance.context import changeset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from angee.workflows.definition import (
    Definition,
    DefinitionInvalid,
    SourcedWorkflowExtension,
    WorkflowExtension,
)
from angee.workflows.steps import Step
from angee.workflows.testing.models import Trigger, Workflow, WorkflowRun, WorkflowVersion
from tests.conftest import create_platform_admin, create_user, make_addon, vault_for, write_addon_package
from tests.test_workflows_triggers import capture, restrict_source_grant_to_record
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.workflow_steps import document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")

RELEASED = "install/100_workflows.workflow.yaml"
DRAFT_REFUSAL = (
    "draft is installed and published on every changed row, so it is never create_only: under create_only "
    "a changed row would publish the draft staff hold."
)
PUBLISH_REFUSAL = (
    "publish applies on every changed row, so it is never create_only: under create_only a changed row "
    "would publish whatever the entry declares."
)
REFUSALS = {"draft": DRAFT_REFUSAL, "publish": PUBLISH_REFUSAL}
STAFF_DRAFT = document("entry", "staff_draft")
UNPUBLISHABLE_DRAFT = {"nodes": {"entry": {"step": "echo"}, "stray": {"step": "echo"}}, "results": [{"from": "entry"}]}
"""A draft staff may save but not publish: it has two entry nodes."""


class InstallProbe(Step[None, None, None]):
    """Registered graph declaration used by resource installation contracts."""

    key = "install_probe"


class Release:
    """An addon shipping one install-tier workflow row, rewritten between loads."""

    def __init__(self, path: Path, **fields: Any) -> None:
        (path / "resources" / "install").mkdir(parents=True)
        self.path = path
        self.fields = {"key": "shipped", "name": "Shipped", "draft": document("entry"), **fields}
        self.addon = make_addon(path=path, resources={"install": [{"path": RELEASED}]})

    def load(self, *, create_only: tuple[str, ...] = (), **changes: Any) -> Any:
        """Declare the row with ``changes`` and the entry with ``create_only``, then load the install tier."""

        entry: dict[str, Any] = {"path": RELEASED, **({"create_only": list(create_only)} if create_only else {})}
        write_addon_package(self.addon, resources={"install": [entry]})
        row = {"xref": "shipped", "fields": {**self.fields, **changes}}
        (self.path / "resources" / RELEASED).write_text(yaml.safe_dump({"rows": [row]}, sort_keys=False))
        return Resource.objects.load_addons((self.addon,), tiers=[Resource.Tier.INSTALL])

    def workflow(self) -> Any:
        with system_context(reason="tests.workflows.release read"):
            return Workflow.system_queryset().get(key="shipped")

    def state(self) -> tuple[Any, ...]:
        """What staff and admission see: the name, the draft, the served version and every version."""

        workflow = self.workflow()
        with system_context(reason="tests.workflows.release read"):
            versions = list(WorkflowVersion.system_queryset().filter(workflow=workflow).values_list("pk", flat=True))
        return workflow.name, workflow.draft, workflow.draft_revision, workflow.published_id, versions

    def save_staff_draft(self, draft: dict[str, Any], actor: Any) -> None:
        workflow = self.workflow()
        with actor_context(actor), changeset(fresh=True, change_reason="Staff draft"):
            Workflow.objects.save_draft(
                workflow, draft=draft, expected_revision=workflow.draft_revision, actor=actor,
            )


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

    (path / "resources").mkdir(exist_ok=True)
    source = "100_workflows.workflow.yaml"
    (path / "resources" / source).write_text(yaml.safe_dump({"rows": [{
        "xref": "extensible",
        "fields": {"key": "extensible", "name": "Extensible", "draft": draft},
    }]}))
    return source


def _extension_addon(tmp_path: Path):
    """Materialize the checked-in extcontrib reference as a fixture addon."""

    path = tmp_path / "consumer"
    path.mkdir()
    (path / "resources").mkdir()
    source = "100_workflows.workflowextension.yaml"
    copyfile(
        Path(__file__).parent / "extcontrib/resources/install" / source,
        path / "resources" / source,
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


def test_a_fragment_splices_at_a_point_an_earlier_fragment_declares() -> None:
    """A later addon's fragment runs inside an earlier fragment's graph, before the node its point names."""

    base = Definition.model_validate(_extensible_document())
    ledger = WorkflowExtension.model_validate({
        "extends": "extensible",
        "at": "after_entry",
        "nodes": {
            "ledger": {"step": "install_probe", "next": {"done": ["movements", "references"]}},
            "references": {"step": "install_probe", "next": {"done": "movements"}},
            "movements": {"step": "install_probe", "join": "all"},
        },
        "points": {"before_movements": {"before": "movements"}},
    })
    orders = _contribution("consumer.orders", "orders", sequence=0).extension.model_copy(
        update={"at": "before_movements"},
    )
    projects = _contribution("consumer.projects", "projects", sequence=0).extension.model_copy(
        update={"at": "before_movements"},
    )
    composed = base.with_extensions((
        SourcedWorkflowExtension("consumer.ledger", ledger),
        SourcedWorkflowExtension("consumer.orders", orders),
        SourcedWorkflowExtension("consumer.projects", projects),
    ))
    # Both later fragments run inside the ledger fragment, in addon order, before the node its point names.
    assert composed.nodes["entry"].next == {"done": "ledger"}
    assert composed.nodes["ledger"].next == {"done": ["orders", "references"]}
    assert composed.nodes["references"].next == {"done": "orders"}
    assert composed.nodes["orders"].next == {"done": "projects"}
    assert composed.nodes["projects"].next == {"done": "movements"}
    assert composed.nodes["movements"].next == {"done": "finish"}
    retained = composed.retained_extensions()
    assert [item.extension.points for item in retained] == [ledger.points, {}, {}]
    replayed = base.with_extensions(retained)
    assert replayed.model_dump(mode="json", by_alias=True) == composed.model_dump(mode="json", by_alias=True)


def test_an_after_point_a_fragment_declares_retains_its_authored_route() -> None:
    """Splicing after a fragment's node rewrites that node's route; retention restores the authored one."""

    base = Definition.model_validate(_extensible_document())
    ledger = WorkflowExtension.model_validate({
        "extends": "extensible",
        "at": "after_entry",
        "nodes": {
            "ledger": {"step": "install_probe", "next": {"done": "movements"}},
            "movements": {"step": "install_probe"},
        },
        "points": {"after_ledger": {"after": "ledger"}},
    })
    orders = _contribution("consumer.orders", "orders", sequence=0).extension.model_copy(
        update={"at": "after_ledger"},
    )
    composed = base.with_extensions((
        SourcedWorkflowExtension("consumer.ledger", ledger),
        SourcedWorkflowExtension("consumer.orders", orders),
    ))
    assert composed.nodes["ledger"].next == {"done": "orders"}
    assert composed.nodes["orders"].next == {"done": "movements"}
    assert composed.nodes["movements"].next == {"done": "finish"}
    assert composed.contributions[0].routes == {"ledger": {"done": "movements"}}
    replayed = base.with_extensions(composed.retained_extensions())
    assert replayed.model_dump(mode="json", by_alias=True) == composed.model_dump(mode="json", by_alias=True)


def test_a_fragment_point_must_be_declared_earlier_and_once() -> None:
    """Fragment points fail fast: unknown at their use, duplicated, or anchored outside their fragment."""

    base = Definition.model_validate(_extensible_document())
    declaring = WorkflowExtension.model_validate({
        "extends": "extensible",
        "at": "after_entry",
        "nodes": {"ledger": {"step": "install_probe"}},
        "points": {"after_ledger": {"after": "ledger"}},
    })
    using = _contribution("consumer.early", "early", sequence=0).extension.model_copy(update={"at": "after_ledger"})
    with pytest.raises(DefinitionInvalid, match="unknown point 'after_ledger'"):
        base.with_extensions((
            SourcedWorkflowExtension("consumer.early", using),
            SourcedWorkflowExtension("consumer.ledger", declaring),
        ))
    redeclaring = declaring.model_copy(update={"nodes": {"other": {"step": "install_probe"}},
                                               "points": {"after_entry": {"after": "other"}}})
    with pytest.raises(DefinitionInvalid, match="duplicate point 'after_entry'"):
        base.with_extensions((SourcedWorkflowExtension("consumer.other", redeclaring),))
    with pytest.raises(ValueError, match="must name a node this extension contributes"):
        stray = {**declaring.model_dump(), "points": {"after_x": {"after": "entry"}}}
        WorkflowExtension.model_validate(stray).fragment()
    # A fragment without points serializes, and so fingerprints, exactly as before points existed.
    assert "points" not in using.model_dump(mode="json", by_alias=True)


def test_resource_composes_extensions_without_editing_the_base_draft(
    db: None,
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

    extension_path = Path(consumer.path) / "resources" / "100_workflows.workflowextension.yaml"
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

    write_addon_package(consumer, resources={"demo": []})
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
    db: None,
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
    (consumer_path / "resources").mkdir()
    source = "100_workflows.workflowextension.yaml"
    (consumer_path / "resources" / source).write_text(yaml.safe_dump({"rows": [{
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
    db: None,
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
    (tmp_path / "resources").mkdir(exist_ok=True)
    (tmp_path / "resources" / source).write_text(yaml.safe_dump({"rows": [{"xref": "graph", "fields": fields}]}))
    owner = make_addon(path=tmp_path, resources={"demo": [{"path": source}]})
    result = Resource.objects.load_addons((owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert result.created == 1
    with system_context(reason="read installed workflow resource"):
        workflow = Workflow.objects.get(key="resource-graph")
        assert workflow.owner_id is None
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


def test_the_installing_user_owns_the_workflow(execution):
    """An install as a user makes that user the transferable owner beside its attribution."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="owned_workflow",
        name="Owned workflow",
        draft={"nodes": {"entry": {"step": "echo"}}},
        actor=actor,
    )
    assert (workflow.owner_id, workflow.created_by_id) == (actor.pk, actor.pk)


@pytest.mark.parametrize(("create_only", "refusal"), [
    pytest.param(("draft",), DRAFT_REFUSAL, id="draft"),
    pytest.param(("publish",), PUBLISH_REFUSAL, id="publish"),
    pytest.param(("publish", "draft"), f"{DRAFT_REFUSAL} {PUBLISH_REFUSAL}", id="both-named"),
])
def test_a_first_load_listing_draft_or_publish_under_create_only_is_refused(
    db: None, tmp_path: Path, create_only: tuple[str, ...], refusal: str,
) -> None:
    """The entry is refused before any row: under create_only a changed row would publish against it."""

    release = Release(tmp_path, publish=False)

    with pytest.raises(ResourceLoadError) as refused:
        release.load(create_only=create_only)

    assert str(refused.value).endswith(f": {refusal}")
    with system_context(reason="tests.workflows.release ledger"):
        assert not Workflow.system_queryset().filter(key="shipped").exists()
        assert not Resource.objects.filter(source_addon=release.addon.name).exists()


@pytest.mark.parametrize(("create_only", "changes", "staff_draft"), [
    pytest.param(("draft",), {"name": "Renamed"}, STAFF_DRAFT, id="draft-on-a-renamed-row"),
    pytest.param(("draft",), {}, STAFF_DRAFT, id="draft-newly-create-only"),
    pytest.param(("draft",), {"name": "Renamed"}, UNPUBLISHABLE_DRAFT, id="draft-over-an-unpublishable-staff-draft"),
    pytest.param(("publish",), {"publish": False}, STAFF_DRAFT, id="publish-newly-create-only"),
])
def test_a_reapplied_row_listing_draft_or_publish_under_create_only_is_refused_and_staffs_draft_stays(
    db: None, tmp_path: Path, create_only: tuple[str, ...], changes: dict[str, Any], staff_draft: dict[str, Any],
) -> None:
    """The release's entry is refused on every load, never on what staff hold; nothing is published."""

    staff = create_platform_admin("workflow-release-staff")
    release = Release(tmp_path)
    assert release.load().created == 1
    release.save_staff_draft(staff_draft, staff)
    before = release.state()

    for _load in ("release load", "the next load"):
        with pytest.raises(ResourceLoadError) as refused:
            release.load(create_only=create_only, **changes)
        assert str(refused.value).endswith(f": {REFUSALS[create_only[0]]}")
    assert release.state() == before


def test_a_refused_release_never_serves_staffs_draft_to_a_run_as_the_workflow_principal(
    execution, trigger_resource_schema, tmp_path: Path, monkeypatch,
) -> None:
    """An editor's draft is not published as system-installed, so admission keeps running the release's version."""

    admin, _sent = execution
    release = Release(tmp_path, subject_model="knowledge.vault")
    release.load()
    workflow = release.workflow()
    record = vault_for(admin, name="Ready")
    editor = create_user("workflow-release-editor")
    workflow.with_actor(admin).grant_record_access("editor", editor)
    restrict_source_grant_to_record(monkeypatch, record)
    with actor_context(admin):
        trigger = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label="knowledge.vault",
            condition={"name": {"_eq": "Ready"}},
        )
    Trigger.objects.enable(trigger, actor=admin)
    release.save_staff_draft(STAFF_DRAFT, editor)
    released = release.workflow().published_id

    with pytest.raises(ResourceLoadError, match="never create_only"):
        release.load(create_only=("draft",), name="Renamed")

    capture(record)
    assert Trigger.objects.drain() == 1
    with system_context(reason="tests.workflows.release runs"):
        assert list(WorkflowRun.system_queryset().values_list("version_id", flat=True)) == [released]


def test_a_staff_save_after_a_release_load_takes_the_loads_revert_off_the_workflows_history(
    db: None, tmp_path: Path, django_capture_on_commit_callbacks,
) -> None:
    """A later write refuses reverting the load, so its history no longer offers it; staff's own save reverts."""

    staff = create_platform_admin("workflow-history-staff")
    release = Release(tmp_path)
    release.load()
    release.save_staff_draft(STAFF_DRAFT, staff)
    with django_capture_on_commit_callbacks(execute=True):
        release.load(draft=document("entry", "released_next"))
    with django_capture_on_commit_callbacks(execute=True):
        release.save_staff_draft(document("entry", "staff_after_release"), staff)
    workflow = release.workflow()
    with system_context(reason="tests.workflows.history"):
        *_, load, save = dict.fromkeys(version.changeset for version in workflow.history.order_by("history_id"))
    assert (load.change_reason, save.change_reason) == ("resources.load", "Staff draft")

    with actor_context(staff):
        assert workflow.revertable_changesets((load, save), actor=staff) == {save.pk}
        assert not load.revertable(workflow, actor=staff)
