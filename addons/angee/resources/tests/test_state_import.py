"""Seeded transition state is initial data, never an update to live state."""

from __future__ import annotations

import hashlib
import json
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
import tablib
import yaml
from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.fields import NOT_PROVIDED
from import_export.results import RowResult
from rebac import system_context

from angee.agents.testing.models import Agent, AgentSession, AgentTurn, InferenceModel, InferenceProvider
from angee.base.transitions import StateTransitions
from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource, build_resource
from angee.resources.testing.models import Resource
from angee.resources.tests.test_resources import addon
from angee.resources.widgets import split_xref
from tests.chatterdemo.models import TrackedRecordChild
from tests.conftest import (  # noqa: F401 -- share fake-addon lifetime
    Repository,
    Source,
    Template,
    VcsBridge,
    addon_fixture_resources,
    make_integration,
)


def _write_rows(path: Path, model: str, rows: list[dict[str, Any]]) -> None:
    """Write equivalent structured or tabular seed rows."""

    if path.suffix in {".csv", ".tsv"}:
        dataset = tablib.Dataset(headers=list(rows[0]))
        for row in rows:
            dataset.append(list(row.values()))
        content = dataset.export(path.suffix[1:])
    else:
        payload = {"_meta": {"model": model}, "rows": rows}
        content = json.dumps(payload) if path.suffix == ".json" else yaml.safe_dump(payload)
    path.write_text(content, encoding="utf-8")


def test_transition_field_names_include_inheritance_without_companion_fields() -> None:
    assert StateTransitions.get_field_names(TrackedRecordChild) == {"status"}
    assert StateTransitions.get_field_names(Agent) == {"lifecycle"}
    assert InferenceProvider._meta.parents
    assert StateTransitions.get_field_names(InferenceProvider) == {"lifecycle"}
    assert StateTransitions.get_field_names(InferenceModel) == set()


@pytest.mark.django_db
@pytest.mark.parametrize("suffix", ["yaml", "yml", "json", "csv", "tsv"])
@pytest.mark.parametrize("tier", ["master", "install", "demo"])
def test_seed_state_is_initial_and_does_not_drive_reimports(tmp_path: Path, suffix: str, tier: str) -> None:
    """All formats and tiers initialize state, then preserve transition writes."""

    path = tmp_path / f"rows.{suffix}"
    model = TrackedRecordChild._meta.label
    rows = [
        {"_xref": "initial", "title": "Initial", "note": "First", "status": "closed"},
        {"_xref": "live", "title": "Live", "note": "Second", "status": "open"},
    ]
    _write_rows(path, model, rows)
    owner = addon(tmp_path, manifest={tier: ({"path": path.name, "model": model},)})

    with system_context(reason="test resources initial state"):
        validated = Resource.objects.validate_addons((owner,), tiers=[tier])
        assert validated.checked_rows == 2
        assert not TrackedRecordChild.objects.exists()
        loaded = Resource.objects.load_addons((owner,), tiers=[tier], allow_non_dev=True)
        assert loaded.created == 2
        assert TrackedRecordChild.objects.get(title="Initial").status == "closed"
        live = TrackedRecordChild.objects.get(title="Live")
        assert live.status == "open"
        live.close()

        rows[1].update(title="Updated", note="Updated note")
        _write_rows(path, model, rows)
        Resource.objects.validate_addons((owner,), tiers=[tier])
        live.refresh_from_db()
        assert live.title == "Live" and live.status == "closed"
        updated = Resource.objects.load_addons((owner,), tiers=[tier], allow_non_dev=True)
        assert (updated.updated, updated.skipped) == (1, 1)
        live.refresh_from_db()
        assert (live.title, live.note, live.status) == ("Updated", "Updated note", "closed")

        previous_hash = Resource.objects.get(source_addon=owner.name, xref="live").content_hash
        live.title = "Operator title"
        live.save(update_fields={"title"})
        rows[1]["status"] = "closed"
        _write_rows(path, model, rows)
        unchanged = Resource.objects.load_addons((owner,), tiers=[tier], allow_non_dev=True)
        assert (unchanged.loaded, unchanged.skipped) == (0, 2)
        assert Resource.objects.get(source_addon=owner.name, xref="live").content_hash == previous_hash
        live.refresh_from_db()
        assert (live.title, live.status) == ("Operator title", "closed")


@pytest.mark.django_db(transaction=True)
def test_seed_update_does_not_write_state_read_before_a_concurrent_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rows.yaml"
    model = TrackedRecordChild._meta.label
    row = {"_xref": "live", "title": "Original", "note": "Original note", "status": "open"}
    _write_rows(path, model, [row])
    owner = addon(tmp_path, manifest={"master": ({"path": path.name, "model": model},)})

    with system_context(reason="test resource save after concurrent transition"):
        Resource.objects.load_addons((owner,), tiers=["master"])
        live = TrackedRecordChild.objects.get()
        row.update(title="Updated", note="Updated note")
        _write_rows(path, model, [row])
        before_save = AngeeResource.before_save_instance
        transitioned = []

        def close_after_read(resource: AngeeResource, instance: Any, row: Any, **kwargs: Any) -> None:
            assert instance.status == "open"
            assert not connection.in_atomic_block
            # A separate instance commits the competing transition after the read.
            concurrent = TrackedRecordChild.objects.get(pk=instance.pk)
            concurrent.close()
            assert not connection.in_atomic_block
            assert TrackedRecordChild.objects.get(pk=instance.pk).status == "closed"
            transitioned.append(instance.pk)
            before_save(resource, instance, row, **kwargs)

        monkeypatch.setattr(AngeeResource, "before_save_instance", close_after_read)
        entry = ResourceEntry.from_declaration(owner, "master", {"path": path.name, "model": model})
        (group,) = entry.read_groups()
        resource = build_resource(TrackedRecordChild, entry, ledger_model=Resource, addon_aliases={})
        # Keep this deterministic SQLite simulation outside the batch transaction
        # so the transition genuinely commits before the loader's stale save.
        result = resource.import_data(group.dataset, use_transactions=False, raise_errors=True)
        assert result.rows[0].import_type == RowResult.IMPORT_TYPE_UPDATE
        assert transitioned == [live.pk]
        live = TrackedRecordChild.objects.get(pk=live.pk)
        assert (live.title, live.note, live.status) == ("Updated", "Updated note", "closed")


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["validate", "load"])
@pytest.mark.parametrize("change_title", [False, True], ids=["hash-skip", "update"])
def test_invalid_seed_state_is_rejected_for_existing_targets(
    tmp_path: Path, operation: str, change_title: bool,
) -> None:
    path = tmp_path / "rows.yaml"
    model = TrackedRecordChild._meta.label
    row = {"_xref": "live", "title": "Original", "status": "open"}
    _write_rows(path, model, [row])
    owner = addon(tmp_path, manifest={"master": ({"path": path.name, "model": model},)})

    with system_context(reason="test resources invalid existing state"):
        Resource.objects.load_addons((owner,), tiers=["master"])
        previous_hash = Resource.objects.get(source_addon=owner.name, xref="live").content_hash
        row["status"] = "bogus"
        if change_title:
            row["title"] = "Updated"
        _write_rows(path, model, [row])
        importer = getattr(Resource.objects, f"{operation}_addons")
        with pytest.raises(ResourceLoadError, match="bogus"):
            importer((owner,), tiers=["master"])
        live = TrackedRecordChild.objects.get()
        assert (live.title, live.status) == ("Original", "open")
        assert Resource.objects.get(source_addon=owner.name, xref="live").content_hash == previous_hash


@pytest.mark.django_db
@pytest.mark.parametrize("suffix", ["csv", "tsv"])
def test_blank_tabular_state_uses_default_on_create_and_is_dropped_on_update(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"rows.{suffix}"
    model = TrackedRecordChild._meta.label
    row = {"_xref": "live", "title": "Original", "status": ""}
    _write_rows(path, model, [row])
    owner = addon(tmp_path, manifest={"master": ({"path": path.name, "model": model},)})

    with system_context(reason="test resources blank tabular state"):
        Resource.objects.validate_addons((owner,), tiers=["master"])
        assert not TrackedRecordChild.objects.exists()
        assert Resource.objects.load_addons((owner,), tiers=["master"]).created == 1
        live = TrackedRecordChild.objects.get()
        assert live.status == TrackedRecordChild._meta.get_field("status").get_default()
        live.close()
        row["title"] = "Updated"
        _write_rows(path, model, [row])
        Resource.objects.validate_addons((owner,), tiers=["master"])
        assert Resource.objects.load_addons((owner,), tiers=["master"]).updated == 1
        live.refresh_from_db()
        assert (live.title, live.status) == ("Updated", "closed")


@pytest.mark.django_db
def test_old_state_inclusive_hash_reimports_once_without_resetting_state(tmp_path: Path) -> None:
    path = tmp_path / "rows.yaml"
    model = TrackedRecordChild._meta.label
    row = {"_xref": "live", "title": "Seed title", "note": "Seed note", "status": "open"}
    _write_rows(path, model, [row])
    owner = addon(tmp_path, manifest={"master": ({"path": path.name, "model": model},)})

    with system_context(reason="test resources previous hash"):
        Resource.objects.load_addons((owner,), tiers=["master"])
        live = TrackedRecordChild.objects.get()
        live.close()
        old_payload = {name: value for name, value in row.items() if name != "_xref"}
        old_hash = "sha256:" + hashlib.sha256(
            json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        Resource.objects.filter(source_addon=owner.name, xref="live").update(content_hash=old_hash)

        refreshed = Resource.objects.load_addons((owner,), tiers=["master"])
        assert refreshed.updated == 1
        live.refresh_from_db()
        assert live.status == "closed"
        assert Resource.objects.get(source_addon=owner.name, xref="live").content_hash != old_hash
        replay = Resource.objects.load_addons((owner,), tiers=["master"])
        assert (replay.loaded, replay.skipped) == (0, 1)


@pytest.mark.django_db
def test_adopted_target_preserves_state_and_updates_other_fields(tmp_path: Path) -> None:
    path = tmp_path / "turns.json"
    model = AgentTurn._meta.label
    owner = addon(tmp_path, manifest={"master": ({
        "path": path.name, "model": model, "adopt": ("session", "index"),
    },)})
    with system_context(reason="test resources adopted state"):
        user = get_user_model().objects.create_user(username="state-owner")
        agent = Agent.objects.create(name="Test agent", owner=user)
        session = AgentSession.objects.create(agent=agent, owner=user)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Original")
        turn.mark_running()
        Resource.objects.create(
            source_addon=owner.name, source_path="session.yaml", tier="master", xref="session",
            target_model=session._meta.label, target_id=session.public_id, content_hash="",
        )
        row = {"_xref": "turn", "session": "resource_addon.session", "index": 1,
               "prompt": "Updated prompt", "status": "pending"}
        _write_rows(path, model, [row])
        Resource.objects.validate_addons((owner,), tiers=["master"])
        turn.refresh_from_db()
        assert (turn.prompt, turn.status) == ("Original", "running")

        adopted = Resource.objects.load_addons((owner,), tiers=["master"])
        assert (adopted.created, adopted.updated) == (0, 1)
        turn.refresh_from_db()
        assert (turn.prompt, turn.status) == ("Updated prompt", "running")
        assert AgentTurn.objects.count() == 1
        previous_hash = Resource.objects.get(source_addon=owner.name, xref="turn").content_hash
        row["status"] = "canceled"
        _write_rows(path, model, [row])
        unchanged = Resource.objects.load_addons((owner,), tiers=["master"])
        assert (unchanged.loaded, unchanged.skipped) == (0, 1)
        assert Resource.objects.get(source_addon=owner.name, xref="turn").content_hash == previous_hash
        row["prompt"] = "Changed again"
        _write_rows(path, model, [row])
        assert Resource.objects.load_addons((owner,), tiers=["master"]).updated == 1
        turn.refresh_from_db()
        assert (turn.prompt, turn.status) == ("Changed again", "running")


@pytest.mark.django_db
def test_demo_agent_seed_reload_preserves_provisioned_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay an actual demo row with changed metadata over a provisioned agent."""

    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "examples/addons"))
    owner = AppConfig("example.notes", import_module("example.notes"))
    source = ResourceEntry.from_declaration(owner, "demo", {"path": "resources/demo/095_agents.agent.yaml"})
    (group,) = source.read_groups()
    assert len(group.dataset) == 4
    omitted = {"lifecycle", "runtime_status", "workspace", "service", "last_error"}
    for row in group.dataset.dict:
        assert omitted.isdisjoint(name for name, value in row.items() if value is not NOT_PROVIDED)
    aliases = {config.label: config.name for config in apps.get_app_configs()}
    aliases[owner.label] = owner.name

    with system_context(reason="test resources agent seed replay"):
        user = get_user_model().objects.create_user(username="demo-state-owner")
        provider = make_integration("state-provider", model=InferenceProvider, backend_class="manual")
        inference_model = InferenceModel.objects.create(provider=provider, name="Seed model")
        bridge = make_integration("state-vcs", model=VcsBridge, backend_class="stub")
        repository = Repository.objects.create(
            vcs_bridge=bridge, name="templates", org="tests", remote="https://example.test/templates",
        )
        template_source = Source.objects.create(repository=repository, kind="template")
        template = Template.objects.create(source=template_source, path="workspace", kind="workspace")
        index = group.dataset["_xref"].index("agent_demo_opencode")
        dataset = group.dataset.subset(rows=[index])
        row = dataset.dict[0]
        for column, target in (("owner", user), ("model", inference_model), ("workspace_template", template)):
            addon_name, xref = split_xref(row[column], aliases)
            Resource.objects.create(
                source_addon=addon_name, source_path="fixtures.yaml", tier="demo", xref=xref,
                target_model=target._meta.label, target_id=target.public_id, content_hash="",
            )
        resource = build_resource(Agent, source, ledger_model=Resource, addon_aliases=aliases)
        created = resource.import_data(dataset, raise_errors=True)
        assert created.rows[0].import_type == RowResult.IMPORT_TYPE_NEW
        agent = created.rows[0].instance
        agent.mark_provisioning()
        agent.mark_provisioned(workspace="live-workspace", service="live-service")
        agent.last_error = "Retained diagnostic"
        agent.save(update_fields={"last_error"})

        changed = dict(row, description="Updated seed description", lifecycle="draft")
        replay = tablib.Dataset(headers=list(changed))
        replay.append(list(changed.values()))
        updated = resource.import_data(replay, raise_errors=True)
        assert updated.rows[0].import_type == RowResult.IMPORT_TYPE_UPDATE
        agent.refresh_from_db()
        assert agent.description == "Updated seed description"
        assert (agent.lifecycle, agent.runtime_status, agent.workspace, agent.service, agent.last_error) == (
            "ready", "running", "live-workspace", "live-service", "Retained diagnostic",
        )

        installed = tuple(apps.get_app_configs())
        monkeypatch.setattr(apps, "get_app_configs", lambda: (*installed, owner))
        # Replay the real seed through load_xref, with an explicit initial state.
        original_read_groups = ResourceEntry.read_groups

        def read_groups_with_state(entry: ResourceEntry) -> Any:
            groups = original_read_groups(entry)
            for selected_group in groups:
                selected_group.dataset.append_col(
                    ["draft"] * len(selected_group.dataset), header="lifecycle",
                )
            return groups

        monkeypatch.setattr(ResourceEntry, "read_groups", read_groups_with_state)
        selected = Resource.objects.load_xref(
            f"{owner.name}.{row['_xref']}", model=Agent, allow_non_dev=True,
        )
        assert selected.pk == agent.pk
        assert selected.description == row["description"]
        assert (selected.lifecycle, selected.workspace, selected.service) == (
            "ready", "live-workspace", "live-service",
        )
