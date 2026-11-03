"""Worker wake publication across a Redis channel layer."""

import asyncio
import multiprocessing
import os

import pytest
from channels_redis.core import RedisChannelLayer
from django.db import connections
from django.test import override_settings
from redis.exceptions import RedisError

from angee.decisions.testing.models import Decision
from angee.graphql import publishing
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import vault_for


def _worker_wake(decision_id: int, url: str, result: multiprocessing.Queue) -> None:
    """Use Celery's fork shape with inherited publisher registrations."""

    from angee.workflows.tasks import wake_decisions

    connections.close_all()
    with override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [url]}}},
        ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER=False,
    ):
        result.put(wake_decisions(decision_id, using="default"))


def test_worker_decision_wake_publishes_to_web_subscriber(execution) -> None:
    """A forked task's committed run update reaches another process."""

    url = os.environ.get("ANGEE_TEST_CHANNEL_REDIS_URL")
    if not url:
        pytest.skip("Set ANGEE_TEST_CHANNEL_REDIS_URL to an available test Redis database")

    actor, _ = execution
    subject = vault_for(actor)
    workflow = load_workflow(
        {
            "nodes": {
                "review": {
                    "step": "ask_decision",
                    "config": {
                        "kind": "review",
                        "proposal": {
                            "alternatives": [
                                {"key": "accept", "label": "Accept", "outcome": "accepted"},
                            ]
                        },
                    },
                }
            },
            "results": [{"from": "review", "when": ["accepted"], "as": "accepted"}],
        },
        actor=actor,
    )
    run = start_run(workflow, actor=actor, subject=subject)
    run_until(run)
    step = StepRun.system_queryset().get(run=run)
    decision = Decision.system_queryset().get(pk=step.decision_id)
    decision.decide(actor=actor, chosen=["accept"])

    async def scenario() -> None:
        layer = RedisChannelLayer(hosts=[url])
        channel = await layer.new_channel()
        group = publishing.change_group(type(run))
        try:
            await asyncio.wait_for(layer.group_add(group, channel), timeout=2)
        except RedisError, OSError, TimeoutError:
            await layer.close_pools()
            pytest.skip("ANGEE_TEST_CHANNEL_REDIS_URL is unreachable")
        try:
            ctx = multiprocessing.get_context("fork")
            result = ctx.Queue()
            process = ctx.Process(target=_worker_wake, args=(decision.pk, url, result))
            process.start()
            process.join(20)
            assert process.exitcode == 0
            assert result.get(timeout=2) == 1
            event = await asyncio.wait_for(layer.receive(channel), 5)
            assert event["type"] == "angee.change"
            assert event["payload"]["model"] == "workflows.WorkflowRun"
            assert event["payload"]["id"] == run.sqid
            assert event["payload"]["action"] == "update"
        finally:
            await layer.group_discard(group, channel)
            await layer.close_pools()

    asyncio.run(scenario())
