"""Tests for Angee principal identities."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import SubjectRef, system_context

from angee.base.actors import actor_user_id
from tests.conftest import IAM_CONNECTION_TEST_MODELS, INTEGRATE_TEST_MODELS, _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, User
from tests.test_integrate_vcs import VCS_TEST_MODELS


@pytest.fixture()
def agents_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete agents tables needed by the principal tests."""

    del transactional_db
    models = IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS + AGENTS_GRAPHQL_MODELS
    created = _create_tables(models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_agent_principal_subject_is_its_own_rebac_subject(agents_console_tables: None) -> None:
    """An agent acts as ``agents/agent:<sqid>``, not as its owner."""

    owner = User.objects.create_user(username="principal-owner", email="principal@example.com")
    with system_context(reason="test.agent.principal_subject"):
        agent = Agent.objects.create(name="Principal", owner=owner)

    subject = agent.principal_subject()

    assert subject == SubjectRef.of("agents/agent", str(agent.sqid))
    assert actor_user_id(subject) is None
