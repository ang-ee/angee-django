"""Legacy addon hooks keep their call shape and receive the selected affinity."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.db import router
from django.utils.connection import ConnectionDoesNotExist
from rebac import system_context

from angee.agents.backends import InferenceBackend
from angee.agents.provisioning import _render_plan
from angee.base.db import get_write_alias
from angee.graphql.publishing import mute_changes
from angee.messaging import delivery
from angee.messaging.backends import LiveChannelBackend
from tests.test_agents import _provider
from tests.test_agents_graphql import Agent, _provisionable_agent, agents_schema
from tests.test_agents_graphql import agents_console_tables as agents_console_tables
from tests.test_integrate_live import Channel, _live_channel
from tests.test_integrate_live import live_tables as live_tables
from tests.test_messaging import Message
from tests.test_transitions import TransitionRouter


@pytest.mark.django_db(transaction=True)
def test_live_backend_legacy_hooks_follow_committed_desire(
    live_tables: Any,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new hook keyword is needed after the selected writer persists desire."""

    observed: list[tuple[str, str]] = []

    class LegacyBackend(LiveChannelBackend):
        def start_live(self) -> None:
            using = get_write_alias(type(self.bridge), instance=self.bridge)
            row = Channel._base_manager.using(using).get(pk=self.bridge.pk)
            observed.append((using, row.subscription_state["desired"]))

        def stop_live(self) -> None:
            using = get_write_alias(type(self.bridge), instance=self.bridge)
            row = Channel._base_manager.using(using).get(pk=self.bridge.pk)
            observed.append((using, row.subscription_state["desired"]))

    with database_alias("legacy_live_writer") as using, system_context(reason="test.alias.legacy_live"), mute_changes():
        channel = _live_channel("legacy-live-hooks")
        monkeypatch.setattr(Channel, "backend", property(LegacyBackend))
        monkeypatch.setattr(router, "routers", [TransitionRouter("wrong_writer")])
        channel._state.db = "wrong_instance"
        channel.stop_live(using=using)
        channel.start_live(using=using)
        assert observed == [(using, Channel.LiveState.STOPPED), (using, Channel.LiveState.LIVE)]


def test_agent_render_dispatches_legacy_hooks_on_selected_alias(
    agents_console_tables: None,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The render owner pins affinity before all four old-signature hooks run."""

    owner = get_user_model().objects.create_user(username="legacy-render-owner")
    agent = _provisionable_agent(owner, "Legacy render", slug="legacy-render")
    observed: list[str] = []

    def inputs(instance: Any) -> dict[str, str]:
        using = get_write_alias(type(instance), instance=instance)
        assert Agent._base_manager.using(using).get(pk=instance.pk).name == "Legacy render"
        observed.append(using)
        return {}

    def secret(instance: Any) -> str:
        inputs(instance)
        return ""

    for method in ("provision_workspace_inputs", "provision_service_inputs", "mcp_secrets"):
        monkeypatch.setattr(Agent, method, inputs)
    monkeypatch.setattr(Agent, "provision_inference_secret", secret)
    with database_alias("legacy_agent_writer") as using, system_context(reason="test.alias.legacy_render"):
        monkeypatch.setattr(router, "routers", [TransitionRouter("wrong_writer")])
        agent._state.db = "wrong_instance"
        plan = _render_plan(agent, using=using)
        assert plan.workspace_inputs == plan.service_inputs == plan.mcp_secrets == {}
        assert observed == [using] * 4


def test_inference_connect_dispatches_legacy_backend_hook(
    agents_console_tables: None,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend overrides still accept only the historical owner label."""

    provider = _provider("legacy-connect", backend_class="stub_inference")

    class LegacyBackend(InferenceBackend):
        def connect_oauth_client(self, owner_label: str) -> str:
            assert owner_label == "Inference provider"
            return get_write_alias(type(self.provider), instance=self.provider)

    monkeypatch.setattr(type(provider), "backend", property(LegacyBackend))
    with database_alias("legacy_provider_writer") as using:
        monkeypatch.setattr(router, "routers", [TransitionRouter("wrong_writer")])
        provider._state.db = "wrong_instance"
        assert agents_schema._provider_oauth_client(provider, using=using) == using


def test_delivery_rejects_unknown_dequeued_alias_before_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale deployment alias is an error before any worker side effect."""

    lock = Mock(side_effect=AssertionError("The worker must validate its alias before locking."))
    monkeypatch.setattr(delivery, "task_lock", lock)
    with pytest.raises(ConnectionDoesNotExist, match="removed_writer"):
        delivery.run_message_delivery(Message._meta.label_lower, 1, "external", using="removed_writer")
    lock.assert_not_called()
