"""Historical ToolRole subject relation migration coverage."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee.agents.runtime_migrations.role_subject_relations import (
    applies,
    rewrite_role_subject_relations,
)


def test_role_subject_relation_migration_requires_complete_agents_catalogue() -> None:
    """Fresh absent builds skip while partial agents model state fails closed."""

    state = ProjectState.from_apps(apps)
    assert applies(state)
    absent = state.clone()
    for model_name in ("agent", "mcpserver", "mcptool"):
        absent.remove_model("agents", model_name)
    assert not applies(absent)
    partial = state.clone()
    partial.remove_model("agents", "mcptool")
    with pytest.raises(ImproperlyConfigured, match="partial agents catalogue"):
        applies(partial)


@pytest.mark.django_db(transaction=True)
def test_role_subject_relation_migration_rewrites_both_physical_stores() -> None:
    """Both obsolete tuple shapes change while conditions and unrelated grants survive."""

    expires_at = timezone.now() + timedelta(days=1)
    context = {"tenant": "north"}
    denormalized = _create_denormalized_sources(context, expires_at)
    registry = _create_registry_sources(context, expires_at)
    unrelated = Relationship._base_manager.create(
        resource_type="agents/tool_grant",
        resource_id="server.manual",
        relation="grantee",
        subject_type="auth/user",
        subject_id="person",
        optional_subject_relation="",
    )

    rewrite_role_subject_relations(apps, SimpleNamespace(connection=connection))

    for rows in (denormalized, registry):
        rows[0].refresh_from_db()
        rows[1].refresh_from_db()
        assert (rows[0].relation, rows[0].optional_subject_relation) == ("includes", "")
        assert (rows[1].relation, rows[1].optional_subject_relation) == ("role", "")
        for row in rows:
            assert row.caveat_name == "tenant_access"
            assert row.caveat_context == context
            assert row.expires_at == expires_at
            assert row.written_at_xid == 41
    unrelated.refresh_from_db()
    assert unrelated.relation == "grantee"


@pytest.mark.django_db(transaction=True)
def test_role_subject_relation_migration_deduplicates_identical_target() -> None:
    """Equivalent targets absorb old sources and retain the newest transaction."""

    source = Relationship._base_manager.create(
        resource_type="agents/tool_grant",
        resource_id="server.reader",
        relation="grantee",
        subject_type="agents/toolrole",
        subject_id="readers",
        optional_subject_relation="effective_member",
        caveat_name="tenant_access",
        caveat_context={
            "tenant": "north",
            "rules": {"enabled": True, "regions": ["eu", "us"]},
        },
        written_at_xid=42,
    )
    target = Relationship._base_manager.create(
        resource_type="agents/tool_grant",
        resource_id="server.reader",
        relation="role",
        subject_type="agents/toolrole",
        subject_id="readers",
        optional_subject_relation="",
        caveat_name="tenant_access",
        caveat_context={
            "rules": {"regions": ["eu", "us"], "enabled": True},
            "tenant": "north",
        },
        written_at_xid=17,
    )
    resource = _resource("agents/toolrole", "registry-parents")
    subject = _resource("agents/toolrole", "registry-children")
    registry_source = RelationshipRegistry._base_manager.create(
        resource_fk=resource,
        relation="includes",
        subject_fk=subject,
        optional_subject_relation="effective_member",
        written_at_xid=21,
    )
    registry_target = RelationshipRegistry._base_manager.create(
        resource_fk=resource,
        relation="includes",
        subject_fk=subject,
        optional_subject_relation="",
        written_at_xid=55,
    )

    rewrite_role_subject_relations(apps, SimpleNamespace(connection=connection))

    assert not Relationship._base_manager.filter(pk=source.pk).exists()
    target.refresh_from_db()
    assert target.written_at_xid == 42
    assert not RelationshipRegistry._base_manager.filter(pk=registry_source.pk).exists()
    registry_target.refresh_from_db()
    assert registry_target.written_at_xid == 55


@pytest.mark.django_db(transaction=True)
def test_role_subject_relation_migration_preflights_all_stores() -> None:
    """A conflicting target rejects the migration before either store changes."""

    source = Relationship._base_manager.create(
        resource_type="agents/toolrole",
        resource_id="parents",
        relation="includes",
        subject_type="agents/toolrole",
        subject_id="children",
        optional_subject_relation="effective_member",
    )
    resource = _resource("agents/tool_grant", "server.reader")
    subject = _resource("agents/toolrole", "readers")
    old = RelationshipRegistry._base_manager.create(
        resource_fk=resource,
        relation="grantee",
        subject_fk=subject,
        optional_subject_relation="effective_member",
        caveat_name="tenant_access",
        caveat_context={"tenant": {"limits": [1, {"enabled": True}]}},
    )
    RelationshipRegistry._base_manager.create(
        resource_fk=resource,
        relation="role",
        subject_fk=subject,
        optional_subject_relation="",
        caveat_name="tenant_access",
        caveat_context={"tenant": {"limits": [1, {"enabled": 1}]}},
    )

    with pytest.raises(ImproperlyConfigured, match="different caveat context or expiry"):
        rewrite_role_subject_relations(apps, SimpleNamespace(connection=connection))

    source.refresh_from_db()
    old.refresh_from_db()
    assert source.optional_subject_relation == "effective_member"
    assert (old.relation, old.optional_subject_relation) == ("grantee", "effective_member")


def _create_denormalized_sources(context: dict[str, str], expires_at: Any) -> tuple[Any, Any]:
    common = {
        "subject_type": "agents/toolrole",
        "optional_subject_relation": "effective_member",
        "caveat_name": "tenant_access",
        "caveat_context": context,
        "expires_at": expires_at,
        "written_at_xid": 41,
    }
    return (
        Relationship._base_manager.create(
            resource_type="agents/toolrole",
            resource_id="parents",
            relation="includes",
            subject_id="children",
            **common,
        ),
        Relationship._base_manager.create(
            resource_type="agents/tool_grant",
            resource_id="server.reader",
            relation="grantee",
            subject_id="readers",
            **common,
        ),
    )


def _create_registry_sources(context: dict[str, str], expires_at: Any) -> tuple[Any, Any]:
    common = {
        "optional_subject_relation": "effective_member",
        "caveat_name": "tenant_access",
        "caveat_context": context,
        "expires_at": expires_at,
        "written_at_xid": 41,
    }
    return (
        RelationshipRegistry._base_manager.create(
            resource_fk=_resource("agents/toolrole", "registry-parents"),
            relation="includes",
            subject_fk=_resource("agents/toolrole", "registry-children"),
            **common,
        ),
        RelationshipRegistry._base_manager.create(
            resource_fk=_resource("agents/tool_grant", "registry.reader"),
            relation="grantee",
            subject_fk=_resource("agents/toolrole", "registry-readers"),
            **common,
        ),
    )


def _resource(resource_type: str, resource_id: str) -> Any:
    return RebacResource._base_manager.get_or_create(
        resource_type=resource_type,
        resource_id=resource_id,
    )[0]
