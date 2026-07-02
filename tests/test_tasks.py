"""Smoke tests for the framework queue seam."""

from __future__ import annotations

from django.apps import apps
from procrastinate import testing
from procrastinate.contrib.django import DjangoApp


def test_tasks_app_installs_procrastinate_and_registers_in_memory_task() -> None:
    """The framework queue app installs Procrastinate without requiring Postgres in unit tests."""

    assert apps.is_installed("angee.tasks")
    assert apps.is_installed("procrastinate.contrib.django")

    connector = testing.InMemoryConnector()
    task_app = DjangoApp(connector=connector)
    results: list[int] = []

    @task_app.task(name="tests.tasks.smoke")
    def smoke(value: int) -> None:
        results.append(value)

    smoke.defer(value=42)

    assert len(connector.jobs) == 1

    task_app.run_worker(wait=False)

    assert results == [42]
