"""PostgreSQL: reports on one target serialize on the target row and project committed health."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, transaction
from rebac import actor_context

from angee.portfolio.testing.models import Update
from tests.test_decisions_concurrency import submit, wait_for_lock
from tests.test_portfolio_updates import reportable as reportable

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_concurrent_report_saves_project_the_latest_committed_health(reportable: dict[str, Any]) -> None:
    """A second report waits on the target its competitor holds, then projects the newest committed report."""

    manager, initiative = reportable["manager"], reportable["initiative"]
    with actor_context(manager):
        first = Update.objects.report(target=initiative, health="on_track")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                first.health = "at_risk"
                first.save()
                contender, pid = submit(pool, lambda: Update.objects.report(target=initiative, health="off_track"))
                wait_for_lock(pid, contender)
                initiative.refresh_from_db()
                assert initiative.health == "at_risk"
            second = contender.result(timeout=10)
    initiative.refresh_from_db()
    assert (initiative.health, initiative.health_updated_at) == ("off_track", second.updated_at)
    assert Update._base_manager.get(pk=first.pk).health == "at_risk"


def test_report_delete_holds_the_target_before_a_concurrent_report(reportable: dict[str, Any]) -> None:
    """A collector delete takes the target first, so a concurrent report projects after the survivors."""

    manager, initiative = reportable["manager"], reportable["initiative"]
    with actor_context(manager):
        Update.objects.report(target=initiative, health="on_track")
        doomed = Update.objects.report(target=initiative, health="at_risk")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Update.objects.filter(pk=doomed.pk).delete()
                contender, pid = submit(pool, lambda: Update.objects.report(target=initiative, health="off_track"))
                wait_for_lock(pid, contender)
                initiative.refresh_from_db()
                assert initiative.health == "on_track"
            second = contender.result(timeout=10)
    initiative.refresh_from_db()
    assert (initiative.health, initiative.health_updated_at) == ("off_track", second.updated_at)
