"""Opt-in pytest fixtures for real workflow commits and captured task transport."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.workflows.steps import Step
from angee.workflows.testing.drivers import RunFactory, register_steps
from angee.workflows.testing.drivers import capture_tasks as capture_task_sends


@pytest.fixture
def capture_tasks() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    """Capture task sends after real commits for the duration of a test."""
    with capture_task_sends() as sent:
        yield sent


@pytest.fixture
def execution(composed_tables: None, capture_tasks: list[tuple[str, dict[str, Any]]]) -> tuple[Any, list[Any]]:
    """Provide an acting administrator and sends captured after real commits."""

    with system_context(reason="workflows.testing execution actor"):
        actor = get_user_model().objects.create_user(username="workflow-runner")
        grant_role(actor=actor, role="angee/role:admin")
    return actor, capture_tasks


@pytest.fixture
def register_step() -> Iterator[Callable[[type[Step[Any, Any, Any]]], None]]:
    """Contribute a step through trusted settings, including function-local classes."""

    with ExitStack() as stack:
        def register(step: type[Step[Any, Any, Any]]) -> None:
            stack.enter_context(register_steps(step))

        yield register


@pytest.fixture
def run_factory(execution: tuple[Any, list[Any]]) -> Callable[..., RunFactory]:
    """Bind ``run_factory(workflow, actor=...).at(node, status=...)`` to a workflow."""

    default_actor, _sent = execution

    def factory(workflow: Any, *, actor: Any = default_actor) -> RunFactory:
        return RunFactory(workflow, actor)

    return factory
