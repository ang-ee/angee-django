"""Tests for Angee principal identities."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import SubjectRef, app_settings, system_context

from angee.base import actors as actor_module
from angee.base.actors import actor_user_id
from tests.conftest import IAM_CONNECTION_TEST_MODELS, INTEGRATE_TEST_MODELS, SOCIAL_TEST_MODELS, _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, User
from tests.test_integrate_vcs import VCS_TEST_MODELS


def _test_actor_user_resolver(subject_id: str) -> str | None:
    """Resolver used by the base registry unit test."""

    return {"agent-1": "42"}.get(subject_id)


@pytest.fixture()
def agents_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete agents tables needed by the principal tests."""

    del transactional_db
    from tests.test_messaging import MESSAGING_TEST_MODELS
    from tests.test_parties_graphql import PARTIES_TEST_MODELS

    models = tuple(dict.fromkeys(
        IAM_CONNECTION_TEST_MODELS
        + INTEGRATE_TEST_MODELS
        + VCS_TEST_MODELS
        + AGENTS_GRAPHQL_MODELS
        + MESSAGING_TEST_MODELS
        + PARTIES_TEST_MODELS
        + SOCIAL_TEST_MODELS
    ))
    _create_tables(models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(models)


def test_agent_principal_subject_is_its_own_rebac_subject(agents_console_tables: None) -> None:
    """An agent acts as ``agents/agent:<sqid>``, not as its owner."""

    owner = User.objects.create_user(username="principal-owner", email="principal@example.com")
    with system_context(reason="test.agent.principal_subject"):
        agent = Agent.objects.create(name="Principal", owner=owner)

    subject = agent.principal_subject()

    assert subject == SubjectRef.of("agents/agent", str(agent.sqid))
    assert actor_user_id(subject) is None


def test_actor_user_id_non_user_without_resolver_fails_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Base does not import any resolver when a non-user actor has no configured mapping."""

    def forbidden_import(path: str) -> object:
        raise AssertionError(f"resolver import should not run for {path}")

    monkeypatch.setattr(actor_module, "import_string", forbidden_import, raising=False)

    with override_settings(ANGEE_ACTOR_USER_RESOLVERS={}):
        assert actor_user_id(SubjectRef.of("agents/agent", "agent-1")) is None


def test_is_user_actor_checks_authorization_species() -> None:
    """Base owns the REBAC user-species predicate, distinct from attribution."""

    from angee.base.actors import is_user_actor

    assert is_user_actor(SubjectRef.of(app_settings.REBAC_USER_TYPE, "usr_123"))
    assert not is_user_actor(SubjectRef.of(app_settings.REBAC_USER_TYPE, ""))
    assert not is_user_actor(SubjectRef.of("agents/agent", "agt_123"))
    assert not is_user_actor(None)


def test_actor_user_id_uses_configured_non_user_resolver() -> None:
    """A settings-keyed resolver can map a non-user subject to a user FK."""

    with override_settings(
        ANGEE_ACTOR_USER_RESOLVERS={"agents/agent": "tests.test_principals._test_actor_user_resolver"}
    ):
        assert actor_user_id(SubjectRef.of("agents/agent", "agent-1")) == "42"
        assert actor_user_id(SubjectRef.of("agents/agent", "unknown")) is None


def test_agent_actor_resolver_returns_linked_service_user(agents_console_tables: None) -> None:
    """The agents addon resolves an agent subject to its service user without REBAC scoping."""

    owner = User.objects.create_user(username="principal-owner-resolver", email="principal-resolver@example.com")
    with system_context(reason="test.agent.actor_resolver"):
        agent = Agent.objects.create(name="Resolver Agent", owner=owner)

    with override_settings(
        ANGEE_ACTOR_USER_RESOLVERS={"agents/agent": "angee.agents.actor_resolvers.agent_user_id"}
    ):
        assert actor_user_id(agent.principal_subject()) == agent.user_id


def test_agent_create_materializes_service_user(agents_console_tables: None) -> None:
    """Agent rows own a linked non-login service user for FK attribution."""

    owner = User.objects.create_user(username="principal-owner-create", email="principal-create@example.com")
    with system_context(reason="test.agent.service_user.create"):
        agent = Agent.objects.create(name="Principal Service", owner=owner)

    assert agent.user_id is not None
    with system_context(reason="test.agent.service_user.assert_create"):
        service_user = User.objects.get(pk=agent.user_id)
    assert service_user.username == f"agent-{agent.sqid}"
    assert service_user.kind == "service"
    assert service_user.first_name == "Principal Service"
    assert service_user.last_name == ""
    assert service_user.is_active is True
    assert not service_user.has_usable_password()


def test_agent_rename_resyncs_service_user_label(agents_console_tables: None) -> None:
    """Renaming an agent keeps its service user's display name in sync."""

    owner = User.objects.create_user(username="principal-owner-rename", email="principal-rename@example.com")
    with system_context(reason="test.agent.service_user.rename"):
        agent = Agent.objects.create(name="Before Rename", owner=owner)
        agent.name = "After Rename"
        agent.save(update_fields=["name"])

    with system_context(reason="test.agent.service_user.assert_rename"):
        service_user = User.objects.get(pk=agent.user_id)
    assert service_user.username == f"agent-{agent.sqid}"
    assert service_user.first_name == "After Rename"


def test_agent_full_save_without_name_change_does_not_touch_service_user(
    agents_console_tables: None,
) -> None:
    """A no-op agent save must not query or update the service-user row."""

    owner = User.objects.create_user(username="principal-owner-noop", email="principal-noop@example.com")
    with system_context(reason="test.agent.service_user.noop_setup"):
        agent = Agent.objects.create(name="Noop Save", owner=owner)

    user_table = User._meta.db_table
    with CaptureQueriesContext(connection) as captured:
        with system_context(reason="test.agent.service_user.noop_save"):
            agent.save()

    assert all(user_table not in query["sql"] for query in captured.captured_queries)


def test_agent_create_rolls_back_when_service_user_sync_fails(
    agents_console_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent creation and service-user sync commit or roll back together."""

    owner = User.objects.create_user(username="principal-owner-rollback", email="principal-rollback@example.com")

    def fail_sync(agent: object, *, active: bool = True) -> object:
        raise RuntimeError("sync failed")

    monkeypatch.setattr(Agent.objects, "sync_service_user", fail_sync)

    with pytest.raises(RuntimeError, match="sync failed"):
        with system_context(reason="test.agent.service_user.rollback"):
            Agent.objects.create(name="Half Created", owner=owner)

    with system_context(reason="test.agent.service_user.assert_rollback"):
        assert not Agent._base_manager.filter(name="Half Created").exists()


def test_agent_deprovision_keeps_service_user_active_and_rename_preserves_it(
    agents_console_tables: None,
) -> None:
    """Deprovision is reversible; it does not own the service-user active flag."""

    owner = User.objects.create_user(username="principal-owner-deprovision", email="principal-deprovision@example.com")
    with system_context(reason="test.agent.service_user.deprovision"):
        agent = Agent.objects.create(name="Before Deprovision Rename", owner=owner)
        user_id = agent.user_id
        agent.mark_deprovisioned()

    with system_context(reason="test.agent.service_user.assert_deprovision_active"):
        assert User.objects.get(pk=user_id).is_active is True

    with system_context(reason="test.agent.service_user.rename_after_deprovision"):
        agent.name = "After Deprovision Rename"
        agent.save(update_fields=["name"])

    with system_context(reason="test.agent.service_user.assert_rename_active"):
        service_user = User.objects.get(pk=user_id)
    assert service_user.first_name == "After Deprovision Rename"
    assert service_user.is_active is True


def test_agent_instance_delete_deactivates_service_user(agents_console_tables: None) -> None:
    """Deleting an agent deactivates its service principal."""

    owner = User.objects.create_user(username="principal-owner-delete", email="principal-delete@example.com")
    with system_context(reason="test.agent.service_user.delete"):
        agent = Agent.objects.create(name="Deleted", owner=owner)
        user_id = agent.user_id
        agent.delete()

    with system_context(reason="test.agent.service_user.assert_delete"):
        assert User.objects.get(pk=user_id).is_active is False


def test_agent_owner_delete_cascade_deactivates_service_user(agents_console_tables: None) -> None:
    """Owner-user cascades still run the Agent service-user lifecycle side effect."""

    owner = User.objects.create_user(username="principal-owner-cascade", email="principal-cascade@example.com")
    with system_context(reason="test.agent.service_user.cascade_setup"):
        agent = Agent.objects.create(name="Cascade Deleted", owner=owner)
        user_id = agent.user_id
        owner.delete()

    with system_context(reason="test.agent.service_user.assert_cascade"):
        assert User.objects.get(pk=user_id).is_active is False


def test_agent_bulk_delete_deactivates_service_user(agents_console_tables: None) -> None:
    """Bulk queryset deletes still deactivate service users through post_delete."""

    owner = User.objects.create_user(username="principal-owner-bulk", email="principal-bulk@example.com")
    with system_context(reason="test.agent.service_user.bulk_setup"):
        agent = Agent.objects.create(name="Bulk Deleted", owner=owner)
        user_id = agent.user_id
        Agent.objects.filter(pk=agent.pk).delete()

    with system_context(reason="test.agent.service_user.assert_bulk"):
        assert User.objects.get(pk=user_id).is_active is False
