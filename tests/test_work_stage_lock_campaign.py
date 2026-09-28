"""The task row stays locked from reservation validation through persistence."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.models.signals import pre_save
from rebac import actor_context

from tests.work_campaign import CreateTask, make_task
from tests.work_campaign import productivity_create_case as productivity_create_case
from tests.work_campaign import work_case as work_case

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="PostgreSQL work stage row-lock contract",
)


def test_stage_save_holds_source_lock_until_the_write_finishes(work_case):
    actor, queue = work_case
    task = make_task(queue)
    observations = []

    def competing_lock():
        close_old_connections()
        try:
            with transaction.atomic():
                CreateTask._base_manager.select_for_update(nowait=True).get(pk=task.pk)
            return "acquired"
        except DatabaseError as error:
            assert getattr(error.__cause__, "sqlstate", None) == "55P03"
            return "locked"
        finally:
            connections.close_all()

    def observe(sender, instance, **kwargs):
        if instance is task:
            assert connection.in_atomic_block
            with ThreadPoolExecutor(max_workers=1) as pool:
                observations.append(pool.submit(competing_lock).result(timeout=5))

    pre_save.connect(observe, sender=CreateTask, weak=False)
    try:
        with actor_context(actor):
            task.start()
    finally:
        pre_save.disconnect(observe, sender=CreateTask)
    assert observations == ["locked"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(competing_lock).result(timeout=5) == "acquired"
