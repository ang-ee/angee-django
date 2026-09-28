"""Positive and negative system-check contracts for persistence and SQL scopes."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.db import models

from angee.base.checks import (
    check_authenticated_scopes,
    check_creation_key_constraints,
    check_ownership,
    check_rebac_caveats,
)
from angee.base.mixins import CreationKeyMixin
from angee.compose.permissions import apply_schema_paths, extension_source_map
from tests.core_persistence import OWNERSHIP_SCHEMA, CreationRow, OwnedRow
from tests.test_tags import Tag, TagAssignment
from tests.test_zed_extensions import _base_addon, _contrib_addon


@pytest.fixture
def ownership_schema(tmp_path, monkeypatch):
    config = apps.get_app_config("scopedemo")
    path = tmp_path / "ownership.zed"
    path.write_text(OWNERSHIP_SCHEMA)
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    return config, path


@pytest.mark.parametrize("mutation", [
    "valid", "no-definition", "no-transfer", "stored-owner", "audit-owner",
    "no-owner-gate", "wrong-owner-gate", "widened-owner-gate",
])
def test_e022_requires_transfer_and_the_owner_column(ownership_schema, mutation):
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
    else:
        [error] = errors
        assert error.id == "angee.E022" and error.obj is OwnedRow


def test_e022_honors_the_models_declared_transfer_permission(ownership_schema, monkeypatch):
    config, path = ownership_schema
    monkeypatch.setattr(OwnedRow, "owner_transfer_permission", "reassign")
    assert [error.id for error in check_ownership([config])] == ["angee.E022"]
    path.write_text(OWNERSHIP_SCHEMA.replace("permission transfer", "permission reassign"))
    assert [error.id for error in check_ownership([config])] == ["angee.E022"]
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
    errors = check_creation_key_constraints([apps.get_app_config("scopedemo")])
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


@pytest.mark.django_db
@pytest.mark.parametrize("recursive", [False, True])
def test_e026_checks_authenticated_and_dependent_arrow_without_queries(
    tmp_path, monkeypatch, recursive, django_assert_num_queries,
):
    config = apps.get_app_config("tags")
    text = (Path(config.path) / "permissions.zed").read_text()
    if recursive:
        text = text.replace("definition tags/role {", "definition tags/role {\n    relation includes: tags/role")
        text = text.replace(
            "permission effective_member = member + admin->member",
            "permission effective_member = member + admin->member + includes->effective_member",
        )
    path = tmp_path / "tags.zed"
    path.write_text(text)
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    with django_assert_num_queries(0):
        errors = check_authenticated_scopes([config])
    if recursive:
        assert {error.id for error in errors} == {"angee.E026"}
        assert {Tag, TagAssignment} <= {error.obj for error in errors}
        assert any("tags/tag_assignment#read" in error.msg for error in errors)
    else:
        assert errors == []


def test_e026_leaves_missing_arrow_diagnostics_to_native_schema_validation(tmp_path, monkeypatch):
    config = apps.get_app_config("tags")
    path = tmp_path / "unknown-arrow.zed"
    path.write_text("definition tags/tag { permission read = absent->read }")
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    assert check_authenticated_scopes([config]) == []


def test_contract_checks_are_registered_once_by_base():
    for check in (check_ownership, check_creation_key_constraints, check_rebac_caveats, check_authenticated_scopes):
        assert sum(candidate is check for candidate in registry.registered_checks) == 1
