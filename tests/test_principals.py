"""Tests for Angee principal identities."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context, to_subject_ref

from angee.base.actors import actor_user_id
from tests.conftest import IAM_CONNECTION_TEST_MODELS, INTEGRATE_TEST_MODELS, POSTS_TEST_MODELS, _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, User
from tests.test_integrate_vcs import VCS_TEST_MODELS
from tests.test_messaging import MESSAGING_TEST_MODELS
from tests.test_parties_graphql import PARTIES_TEST_MODELS
from tests.test_spaces import SPACES_TEST_MODELS


@pytest.fixture()
def agents_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete agents tables needed by the principal tests."""

    del transactional_db
    models = tuple(dict.fromkeys(
        IAM_CONNECTION_TEST_MODELS
        + INTEGRATE_TEST_MODELS
        + VCS_TEST_MODELS
        + AGENTS_GRAPHQL_MODELS
        + MESSAGING_TEST_MODELS
        + PARTIES_TEST_MODELS
        + SPACES_TEST_MODELS
        + POSTS_TEST_MODELS
    ))
    _create_tables(models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(models)


def test_agent_principal_subject_is_its_service_user(agents_console_tables: None) -> None:
    """An agent acts as its linked non-login user, not as its owner."""

    owner = User.objects.create_user(username="principal-owner", email="principal@example.com")
    with system_context(reason="test.agent.principal_subject"):
        agent = Agent.objects.create(name="Principal", owner=owner)

    subject = agent.principal_subject()

    assert subject == to_subject_ref(agent.user)
    assert subject != to_subject_ref(owner)
    assert actor_user_id(subject) == agent.user_id


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
