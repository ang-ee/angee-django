"""Every ownership adopter gates raw owner writes, including the queue parent."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from rebac import PermissionDenied, actor_context, system_context

from angee.base.checks import check_ownership
from tests.conftest import Backend, Drive, File, Vault
from tests.messaging_campaign import grant
from tests.messaging_models import Person, Thread
from tests.projects_models import Project, Queue, Task
from tests.spaces_models import Group, Membership
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema


@pytest.fixture(params=(Vault, Thread, Group, Queue, Project, Task, Drive, File))
def owned_record(request, campaign_access, campaign_user):
    owner, writer, replacement = (campaign_user(role) for role in ("owner", "writer", "replacement"))
    model = request.param
    with system_context(reason="tests.t3.owned_record"):
        if model in (Drive, File):
            backend = Backend.objects.create(slug="owner-gate", label="Storage", backend_class="local")
            drive = Drive.objects.create(backend=backend, slug="owner-gate", name="Storage", owner=owner)
            row = (
                drive
                if model is Drive
                else File.objects.create(
                    drive=drive,
                    filename="record.txt",
                    content_hash="a" * 64,
                    storage_path="record.txt",
                    owner=owner,
                )
            )
        else:
            values = {"owner": owner, "created_by": owner}
            if model in (Vault, Group, Queue):
                values["name"] = "Owned row"
            if model is Queue:
                values["key"] = "GATE"
            if model in (Task, Project):
                values["title"] = "Owned row"
            row = model.objects.create(**values)
        if model in (Group, Queue):
            Membership.objects.create(
                group=row.group_ptr if model is Queue else row,
                party=Person.objects.for_user(writer),
                role="moderator",
                is_confirmed=True,
            )
        elif model is Task:
            Task._base_manager.filter(pk=row.pk).update(assignee=writer)
        elif model is File:
            grant(drive, "editor", writer)
        else:
            grant(row, "editor", writer)
    assert row.with_actor(writer).has_access("write")
    assert not row.with_actor(writer).has_access("transfer")
    return row, owner, writer, replacement


@pytest.mark.parametrize("field", ("owner", "owner_id"))
@pytest.mark.parametrize("path", ("full_save", "partial_save", "queryset"))
def test_raw_owner_writes_require_transfer_on_every_adopter(owned_record, field, path):
    row, owner, writer, replacement = owned_record
    value = replacement if field == "owner" else replacement.pk
    with actor_context(writer), pytest.raises(PermissionDenied):
        if path == "queryset":
            type(row).objects.filter(pk=row.pk).update(**{field: value})
        else:
            candidate = type(row).objects.get(pk=row.pk)
            setattr(candidate, field, value)
            candidate.save(**({"update_fields": (field,)} if path == "partial_save" else {}))
    persisted = type(row)._base_manager.get(pk=row.pk)
    assert persisted.owner_id == owner.pk


def test_transfer_uses_owner_authority_and_preserves_attribution(owned_record):
    row, owner, _, replacement = owned_record
    author = type(row)._base_manager.get(pk=row.pk).created_by_id
    with actor_context(owner):
        type(row).objects.get(pk=row.pk).transfer_ownership(replacement)
    persisted = type(row)._base_manager.get(pk=row.pk)
    assert persisted.owner_id == replacement.pk
    assert persisted.created_by_id == author
    with actor_context(replacement):
        type(row).objects.get(pk=row.pk).transfer_ownership(None)
    persisted.refresh_from_db()
    assert persisted.owner_id is None and persisted.created_by_id == author


@pytest.mark.parametrize("mutation", ("missing", "row_write", "widened"))
def test_e027_names_the_real_adopter_and_expected_transfer_gate(owned_record, tmp_path, monkeypatch, mutation):
    row, _, _, _ = owned_record
    model = type(row)
    config = apps.get_app_config(model._meta.app_label)
    original = Path(config.rebac_schema).read_text()
    start = original.index(f"definition {model._meta.rebac_resource_type} {{")
    # No inline JSON occurs after the field gate; delimit on a standalone closing brace.
    end = original.index("\n}", start)
    definition = original[start:end]
    gate = "group->transfer" if model is Queue else "transfer"
    old = f"permission write__owner = {gate}"
    assert old in definition
    new = {
        "missing": "",
        "row_write": "permission write__owner = write",
        "widened": f"permission write__owner = {gate} + write",
    }[mutation]
    changed = definition.replace(old, new)
    path = tmp_path / "invalid-owner-gate.zed"
    path.write_text(original[:start] + changed + original[end:])
    monkeypatch.setattr(config, "rebac_schema", str(path))
    errors = check_ownership([SimpleNamespace(get_models=lambda: [model])])
    assert [error.id for error in errors] == ["angee.E027"]
    assert errors[0].obj is model
    assert model._meta.label in errors[0].msg and model._meta.rebac_resource_type in errors[0].msg
    assert gate in errors[0].msg
