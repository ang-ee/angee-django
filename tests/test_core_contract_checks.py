"""Positive and negative system-check contracts for persistence and SQL scopes."""


import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.db import models

from angee.base.checks import (
    check_creation_key_constraints,
    check_ownership,
    check_rebac_caveats,
)
from angee.base.mixins import CreationKeyMixin
from angee.compose.permissions import apply_schema_paths, extension_source_map
from tests.core_persistence import OWNERSHIP_SCHEMA, CreationRow, OwnedRow
from tests.test_zed_extensions import _base_addon, _contrib_addon


@pytest.fixture
def ownership_schema(tmp_path, monkeypatch):
    config = apps.get_app_config("scopedemo")
    path = tmp_path / "ownership.zed"
    path.write_text(OWNERSHIP_SCHEMA)
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    monkeypatch.setattr(config, "get_models", lambda *args, **kwargs: iter([OwnedRow]))
    return config, path


@pytest.mark.parametrize("mutation", [
    "valid", "no-definition", "no-transfer", "stored-owner", "audit-owner",
    "no-owner-gate", "wrong-owner-gate", "widened-owner-gate",
])
def test_ownership_requires_transfer_backing_and_the_owner_gate(ownership_schema, mutation):
    config, path = ownership_schema
    text = OWNERSHIP_SCHEMA
    if mutation == "no-definition":
        text = "definition auth/user {}"
    elif mutation == "no-transfer":
        text = text.replace("permission transfer = owner", "")
    elif mutation == "stored-owner":
        text = text.replace(" // rebac:field=owner", "")
    elif mutation == "audit-owner":
        text = text.replace("rebac:field=owner", "rebac:field=created_by")
    elif mutation == "no-owner-gate":
        text = text.replace("permission write__owner = transfer", "")
    elif mutation == "wrong-owner-gate":
        text = text.replace("write__owner = transfer", "write__owner = write")
    elif mutation == "widened-owner-gate":
        text = text.replace("write__owner = transfer", "write__owner = transfer + write")
    path.write_text(text)
    errors = check_ownership([config])
    if mutation == "valid":
        assert errors == []
    elif mutation == "no-definition":
        assert [error.id for error in errors] == ["angee.E022", "angee.E027"]
        assert all(error.obj is OwnedRow for error in errors)
    else:
        [error] = errors
        assert error.obj is OwnedRow
        if mutation.endswith("owner-gate"):
            assert error.id == "angee.E027"
            assert error.msg == (
                "scopedemo.OwnedRow: definition 'scopedemo/owned_row' must declare write__owner = transfer."
            )
        else:
            assert error.id == "angee.E022"


def test_ownership_honors_the_models_declared_transfer_permission(ownership_schema, monkeypatch):
    config, path = ownership_schema
    monkeypatch.setattr(OwnedRow, "owner_transfer_permission", "reassign")
    assert [error.id for error in check_ownership([config])] == ["angee.E022", "angee.E027"]
    path.write_text(OWNERSHIP_SCHEMA.replace("permission transfer", "permission reassign"))
    assert [error.id for error in check_ownership([config])] == ["angee.E027"]
    path.write_text(OWNERSHIP_SCHEMA.replace("transfer", "reassign"))
    assert check_ownership([config]) == []


@pytest.mark.parametrize("container", [None, "container", "missing", "title", "owner"])
def test_e025_requires_a_foreign_key_to_an_item_owning_model(ownership_schema, monkeypatch, container):
    config, _path = ownership_schema
    monkeypatch.setattr(OwnedRow, "owner_container", container)
    errors = check_ownership([config])
    if container in (None, "container"):
        assert errors == []
    else:
        [error] = errors
        assert error.id == "angee.E025" and error.obj is OwnedRow


@pytest.mark.parametrize("constraint", ["valid", "renamed", "absent", "wrong-scope", "unconditional"])
def test_e023_requires_exact_scoped_partial_uniqueness(monkeypatch, constraint):
    constraints = {
        "valid": [CreationKeyMixin.creation_key_constraint(name="valid")],
        "renamed": [CreationKeyMixin.creation_key_constraint(name="retained_historical_name")],
        "absent": [],
        "wrong-scope": [CreationKeyMixin.creation_key_constraint(scope="title", name="wrong")],
        "unconditional": [models.UniqueConstraint(fields=("created_by", "client_creation_key"), name="wrong")],
    }
    monkeypatch.setattr(CreationRow._meta, "constraints", constraints[constraint])
    config = apps.get_app_config("scopedemo")
    monkeypatch.setattr(config, "get_models", lambda *args, **kwargs: iter([CreationRow]))
    errors = check_creation_key_constraints([config])
    if constraint in ("valid", "renamed"):
        assert errors == []
    else:
        [error] = errors
        assert error.id == "angee.E023" and error.obj is CreationRow


@pytest.mark.parametrize("caveated", [False, True])
def test_e024_inspects_the_emitted_fragment_not_just_the_base(tmp_path, caveated):
    base = _base_addon(tmp_path, """
caveat admitted(allowed bool) { allowed }
definition demo/thing { permission read = nil }
""")
    fragment = _contrib_addon(tmp_path, f"""
definition demo/thing {{
    relation reviewer: auth/user{' with admitted' if caveated else ''}
    permission read = reviewer
}}
""")
    assert check_rebac_caveats([base]) == []
    sources = extension_source_map([base, fragment])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    apply_schema_paths([base, fragment], runtime, sources=sources)
    errors = check_rebac_caveats([base])
    if caveated:
        [error] = errors
        assert error.id == "angee.E024"
        assert "demo/thing#reviewer" in error.msg
    else:
        assert errors == []


def test_e024_accepts_the_installed_composed_schema():
    assert check_rebac_caveats() == []


def test_contract_checks_are_registered_once_by_base():
    for check in (check_ownership, check_creation_key_constraints, check_rebac_caveats):
        assert sum(candidate is check for candidate in registry.registered_checks) == 1
