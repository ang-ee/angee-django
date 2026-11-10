"""The cycle manager maintains every cycle-enabled queue's cadence."""

from datetime import UTC, datetime

import pytest
from rebac import system_context

from angee.projects.testing.models import Cycle, Queue

pytestmark = pytest.mark.django_db(transaction=True)


def test_maintain_cadence_generates_enabled_queues_once() -> None:
    now = datetime(2026, 1, 7, 12, tzinfo=UTC)
    with system_context(reason="tests.work.cycles"):
        enabled = Queue.objects.create(key="CYC", name="Cycles", slug="cycles", cycles_enabled=True)
        disabled = Queue.objects.create(key="OFF", name="Off", slug="off")
        generated = Cycle.objects.maintain_cadence(now=now)
        assert generated == Cycle._base_manager.filter(queue=enabled).count() > 0
        assert not Cycle._base_manager.filter(queue=disabled).exists()
        assert Cycle.objects.maintain_cadence(now=now) == 0
