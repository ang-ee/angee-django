"""Opt-in pytest fixtures for real workflow commits and captured task transport."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.jobs.enqueue import celery_app
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import RunFactory, register_steps


@pytest.fixture
def workflow_permissions(
    composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path,
) -> None:
    """Compose permission contributions for the combined source-test models."""
    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs)
    runtime = tmp_path / "permissions"
    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)


@pytest.fixture
def execution(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    """Provide an acting administrator and sends captured after real commits."""

    sent: list[Any] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kwargs: sent.append((name, kwargs)))
    with system_context(reason="workflows.testing execution actor"):
        actor = get_user_model().objects.create_user(username="workflow-runner")
        grant_role(actor=actor, role="angee/role:admin")
    return actor, sent


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
