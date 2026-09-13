"""One-shot REBAC primary-key identity migration coverage."""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from typing import Any

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from rebac import system_context
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee.base.identity_migration import migrate_rebac_ids, plan_rebac_id_migration
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    PLATFORM_TEST_MODELS,
    _clear_model_tables,
    _create_missing_tables,
)
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, MCPServer, MCPTool
from tests.test_integrate_vcs import VCS_TEST_MODELS


@pytest.fixture
def identity_group() -> Any:
    group_model = apps.get_model("iam", "Group")
    with system_context(reason="test rebac identity migration"):
        return group_model._base_manager.create(name="Identity migration group")


@pytest.fixture
def platform_identity_tables(transactional_db: Any) -> Iterator[None]:
    """Materialize the composed platform model used by the alternate-id proof."""

    del transactional_db
    created = _create_missing_tables(PLATFORM_TEST_MODELS)
    try:
        yield
    finally:
        _clear_model_tables(PLATFORM_TEST_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture
def agent_identity_tables(transactional_db: Any) -> Iterator[None]:
    """Materialize the MCP catalogue models used by the grant-id proof."""

    del transactional_db
    models = (
        IAM_CONNECTION_TEST_MODELS
        + INTEGRATE_TEST_MODELS
        + VCS_TEST_MODELS
        + AGENTS_GRAPHQL_MODELS
    )
    created = _create_missing_tables(models)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_migrate_rebac_ids_converges_both_stores_and_preserves_synthetic_refs(
    identity_group: Any,
) -> None:
    """Model ids become PKs while conditions, newest xid, and named anchors survive."""

    old_id = str(identity_group.sqid)
    new_id = str(identity_group.pk)
    condition = {"tenant": {"enabled": True, "regions": ["eu", "us"]}, "scope": "one"}
    reordered_condition = {
        "scope": "one",
        "tenant": {"regions": ["eu", "us"], "enabled": True},
    }
    source = Relationship.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
        relation="member",
        subject_type="auth/group",
        subject_id=old_id,
        optional_subject_relation="member",
        caveat_name="tenant",
        caveat_context=reordered_condition,
        written_at_xid=30,
    )
    destination = Relationship.objects.create(
        resource_type="auth/group",
        resource_id=new_id,
        relation="member",
        subject_type="auth/group",
        subject_id=new_id,
        optional_subject_relation="member",
        caveat_name="tenant",
        caveat_context=condition,
        written_at_xid=20,
    )
    synthetic = Relationship.objects.create(
        resource_type="storage/role",
        resource_id="storage_admin",
        relation="member",
        subject_type="auth/user",
        subject_id="*",
        written_at_xid=7,
    )
    content_type = ContentType.objects.get_for_model(type(identity_group))
    old_resource = RebacResource.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
        content_type=content_type,
        object_pk=new_id,
    )
    current_resource = RebacResource.objects.create(
        resource_type="auth/group",
        resource_id=new_id,
    )
    old_registry = RelationshipRegistry.objects.create(
        resource_fk=old_resource,
        relation="member",
        subject_fk=old_resource,
        caveat_name="tenant",
        caveat_context=condition,
        written_at_xid=40,
    )
    current_registry = RelationshipRegistry.objects.create(
        resource_fk=current_resource,
        relation="member",
        subject_fk=current_resource,
        caveat_name="tenant",
        caveat_context=condition,
        written_at_xid=10,
    )

    plan = migrate_rebac_ids()
    second = migrate_rebac_ids()

    assert plan.changes
    assert second.changes == 0
    assert not Relationship.objects.filter(pk=source.pk).exists()
    destination.refresh_from_db()
    assert destination.resource_id == new_id
    assert destination.subject_id == new_id
    assert destination.caveat_context == condition
    assert destination.written_at_xid == 30
    synthetic.refresh_from_db()
    assert (synthetic.resource_id, synthetic.subject_id, synthetic.written_at_xid) == (
        "storage_admin",
        "*",
        7,
    )
    assert not RelationshipRegistry.objects.filter(pk=old_registry.pk).exists()
    current_registry.refresh_from_db()
    assert current_registry.resource_fk_id == current_resource.pk
    assert current_registry.subject_fk_id == current_resource.pk
    assert current_registry.caveat_context == condition
    assert current_registry.written_at_xid == 40
    assert not RebacResource.objects.filter(pk=old_resource.pk).exists()
    current_resource.refresh_from_db()
    assert current_resource.content_type_id == content_type.pk
    assert current_resource.object_pk == new_id


@pytest.mark.django_db(transaction=True)
def test_rebac_id_collision_fails_before_either_store_changes(identity_group) -> None:
    """Condition changes on a converging key reject the complete plan."""

    old_id = str(identity_group.sqid)
    new_id = str(identity_group.pk)
    source = Relationship.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
        relation="member",
        subject_type="auth/user",
        subject_id="*",
        caveat_name="tenant",
        caveat_context={"tenant": {"limits": [1, {"enabled": True}]}},
    )
    Relationship.objects.create(
        resource_type="auth/group",
        resource_id=new_id,
        relation="member",
        subject_type="auth/user",
        subject_id="*",
        caveat_name="tenant",
        caveat_context={"tenant": {"limits": [1, {"enabled": 1}]}},
    )
    registry_resource = RebacResource.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
    )
    wildcard = RebacResource.objects.create(resource_type="auth/user", resource_id="*")
    registry_row = RelationshipRegistry.objects.create(
        resource_fk=registry_resource,
        relation="member",
        subject_fk=wildcard,
    )

    with pytest.raises(ImproperlyConfigured, match="different caveat context or expiry"):
        migrate_rebac_ids()

    source.refresh_from_db()
    registry_row.refresh_from_db()
    assert source.resource_id == old_id
    assert registry_row.resource_fk_id == registry_resource.pk


@pytest.mark.django_db(transaction=True)
def test_rebac_id_migration_rejects_mismatched_and_missing_model_evidence(identity_group) -> None:
    """Registry pointers and model-backed orphan ids fail instead of guessing."""

    old_id = str(identity_group.sqid)
    wrong_type = ContentType.objects.get_for_model(ContentType)
    resource = RebacResource.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
        content_type=wrong_type,
        object_pk=str(identity_group.pk),
    )
    with pytest.raises(ImproperlyConfigured, match="mismatched Django backing pointer"):
        plan_rebac_id_migration()

    resource.delete()
    orphan = Relationship.objects.create(
        resource_type="auth/group",
        resource_id="grp_not-a-real-public-id",
        relation="member",
        subject_type="auth/user",
        subject_id="*",
    )
    with pytest.raises(ImproperlyConfigured, match="unresolvable"):
        plan_rebac_id_migration()
    orphan.refresh_from_db()
    assert orphan.resource_id == "grp_not-a-real-public-id"


@pytest.mark.django_db(transaction=True)
def test_migrate_rebac_ids_command_is_dry_run_by_default(identity_group) -> None:
    """The maintenance command reports a plan and requires --apply to write."""

    old_id = str(identity_group.sqid)
    row = Relationship.objects.create(
        resource_type="auth/group",
        resource_id=old_id,
        relation="member",
        subject_type="auth/user",
        subject_id="*",
    )
    output = StringIO()

    call_command("migrate_rebac_ids", stdout=output)

    row.refresh_from_db()
    assert row.resource_id == old_id
    assert "dry run" in output.getvalue()


def test_rebac_id_migration_uses_model_owned_alternate_legacy_identity(
    platform_identity_tables: None,
) -> None:
    """A named model identity resolves through its own upgrade-only lookup."""

    del platform_identity_tables
    addon_model = apps.get_model("platform", "Addon")
    with system_context(reason="test alternate legacy rebac identity"):
        addon = addon_model._base_manager.create(name="angee.identity-probe")
    row = Relationship.objects.create(
        resource_type="platform/addon",
        resource_id=addon.name,
        relation="read",
        subject_type="auth/user",
        subject_id="*",
    )

    migrate_rebac_ids()

    row.refresh_from_db()
    assert row.resource_id == str(addon.pk)


def test_rebac_id_migration_resolves_mcp_tool_grant_id(
    agent_identity_tables: None,
) -> None:
    """The MCPTool owner maps its concrete former grant id onto its primary key."""

    del agent_identity_tables
    with system_context(reason="test MCP tool legacy rebac identity"):
        server = MCPServer._base_manager.create(name="identity-migration-server")
        tool = MCPTool._base_manager.create(server=server, name="search")
    row = Relationship.objects.create(
        resource_type="agents/tool_grant",
        resource_id=tool.grant_id,
        relation="grantee",
        subject_type="auth/user",
        subject_id="*",
    )

    migrate_rebac_ids()

    row.refresh_from_db()
    assert row.resource_id == str(tool.pk)


def test_rebac_id_plan_is_empty_before_rebac_tables_exist(monkeypatch) -> None:
    """A fresh database without the native stores is a safe empty preview."""

    monkeypatch.setattr(connection.introspection, "table_names", lambda: [])
    assert plan_rebac_id_migration().changes == 0
