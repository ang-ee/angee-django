"""Concurrent audience changes serialize on the task owner under PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection
from rebac import actor_context

from angee.base.mixins import StaleRevisionError
from tests.projects_models import Task
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.test_decisions_concurrency import submit
from tests.test_projects_wave3_campaign import project_case as project_case

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_concurrent_visibility_changes_accept_one_revision_and_refuse_the_stale_request(project_case):
    owner, _, _, _, task = project_case
    barrier = Barrier(2)

    def narrow():
        with actor_context(owner):
            row = Task.objects.get(pk=task.pk)
            barrier.wait(timeout=10)
            try:
                row.set_visibility("restricted", expected_revision=task.revision)
                return "changed"
            except StaleRevisionError:
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, _ = submit(pool, narrow)
        second, _ = submit(pool, narrow)
        outcomes = [future.result(timeout=15) for future in (first, second)]
    assert sorted(outcomes) == ["changed", "stale"]
    persisted = Task._base_manager.get(pk=task.pk)
    assert persisted.visibility == "restricted"
    assert persisted.revision == task.revision + 1
