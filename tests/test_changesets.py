"""Production entrypoints own their changeset boundaries; this module runs without the test one."""

from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from typing import Any

import pytest
import strawberry
from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from channels.db import database_sync_to_async
from django.apps import apps
from django.core.management import ManagementUtility
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connections
from django.http import HttpResponse
from django.test import RequestFactory
from django_compose import management as manage
from fastmcp.exceptions import ToolError
from rebac import actor_context, system_context, to_subject_ref
from rebac.middleware import ActorMiddleware

from angee.graphql.schema import AngeeSchema
from angee.jobs.celery import app as celery_app
from angee.mcp.graphql import _execute_sync
from angee.provenance.context import (
    ChangesetRequired,
    changeset,
    changeset_model_label,
    current_changeset,
    require_changeset,
)
from angee.provenance.contrib.angee.context import SERVER_COMMANDS
from angee.provenance.middleware import ChangesetMiddleware, ChangesetScopeMiddleware
from angee.provenance.tasks import Task
from tests.conftest import create_user
from tests.core_persistence import OwnedRow

pytestmark = [pytest.mark.entrypoint_boundaries, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def management_changeset_context(settings):
    """Select the optional provenance policy for these attribution contracts."""

    settings.COMPOSE_COMMAND_CONTEXT = ("angee.provenance.contrib.angee.context.management_context",)


def write(title: str = "captured") -> Any:
    """Capture one history row as a system write under whatever actor is ambient."""

    with system_context(reason="boundary-test"):
        return OwnedRow.objects.create(title=title)


def changesets() -> list[Any]:
    return list(apps.get_model(changeset_model_label())._base_manager.order_by("pk"))


def changeset_of(row: Any) -> Any:
    return row.history.get(history_type="+").changeset


def test_require_changeset_refuses_missing_storage_and_returns_the_open_owner(settings) -> None:
    with pytest.raises(ChangesetRequired):
        require_changeset()
    with changeset(), system_context(reason="required changeset"):
        assert require_changeset() is current_changeset()
    settings.ANGEE_CHANGESET_MODEL = ""
    with changeset(), pytest.raises(ChangesetRequired):
        require_changeset()


def _native_command(monkeypatch, operation):
    """Use Django's winning command and parser with the native context dispatcher."""

    class Command(BaseCommand):
        requires_system_checks = []

        def add_arguments(self, parser):
            parser.add_argument("--noreload", action="store_true")
            parser.add_argument("--database", default="default")

        def handle(self, *args, **options):
            operation()

    monkeypatch.setattr(ManagementUtility, "fetch_command", lambda self, name: Command())


@pytest.mark.parametrize("command", sorted(SERVER_COMMANDS))
def test_servers_run_without_a_process_boundary(monkeypatch, command):
    seen = []
    _native_command(monkeypatch, lambda: seen.append(current_changeset()))
    manage.execute(["manage.py", command, *(["--noreload"] if command == "runserver" else [])])
    assert seen == [None]


@pytest.mark.parametrize("command", ["shell", "migrate"])
def test_main_attributes_a_command_to_one_closed_changeset(monkeypatch, command):
    _native_command(monkeypatch, write)
    manage.execute(["manage.py", command])
    (context,) = changesets()
    assert changeset_of(OwnedRow.system_queryset().get()) == context
    assert (context.change_reason, context.activity, context.actor_ref) == (f"manage.{command}", "system", "")
    assert (context.state, context.outcome) == ("closed", "success")


def test_a_thread_a_command_starts_writes_under_the_command_boundary(monkeypatch):
    """Plain threads share the process attribution intent while storing their own rows."""

    def command():
        write("main thread")
        thread = threading.Thread(target=lambda: (write("plain thread"), connections.close_all()))
        thread.start()
        thread.join()

    _native_command(monkeypatch, command)
    manage.execute(["manage.py", "migrate"])
    rows = changesets()
    assert len(rows) == 2
    assert {row.change_reason for row in rows} == {"manage.migrate"}
    assert {changeset_of(row) for row in OwnedRow.system_queryset().all()} == set(rows)
    assert current_changeset() is None


def test_a_write_without_a_boundary_names_the_manage_py_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["/srv/app/manage.py", "seed_lorem_notes"])
    with pytest.raises(ChangesetRequired, match=r"manage\.py must call django_compose\.management\.execute\(\)"):
        write()
    monkeypatch.setattr(sys, "argv", ["celery", "worker"])
    with pytest.raises(ChangesetRequired) as error:
        write()
    assert "manage.py" not in str(error.value)
    assert not OwnedRow.system_queryset().exists()


@pytest.mark.parametrize(("status", "outcome"), [(200, "success"), (500, "failure")])
def test_sync_request_boundary_closes_with_the_response(status: int, outcome: str) -> None:
    def get_response(request: Any) -> HttpResponse:
        write()
        return HttpResponse(status=status)

    middleware = ChangesetMiddleware(get_response)
    assert not iscoroutinefunction(middleware)
    assert middleware(RequestFactory().get("/")).status_code == status
    (context,) = changesets()
    assert changeset_of(OwnedRow.system_queryset().get()) == context
    assert (context.state, context.outcome) == ("closed", outcome)


@pytest.mark.parametrize(("status", "outcome"), [(200, "success"), (500, "failure")])
def test_async_request_boundary_stays_on_the_request_task(status: int, outcome: str) -> None:
    tasks: list[Any] = []

    async def get_response(request: Any) -> HttpResponse:
        tasks.append(asyncio.current_task())
        await sync_to_async(write)()
        return HttpResponse(status=status)

    middleware = ChangesetMiddleware(get_response)
    assert iscoroutinefunction(middleware)

    async def scenario() -> HttpResponse:
        tasks.append(asyncio.current_task())
        return await middleware(RequestFactory().get("/"))

    assert async_to_sync(scenario)().status_code == status
    assert tasks[0] is tasks[1], "the request was awaited on its own task, not through a sync bridge"
    (context,) = changesets()
    assert changeset_of(OwnedRow.system_queryset().get()) == context
    assert (context.state, context.outcome) == ("closed", outcome)


@pytest.mark.parametrize("failing", [False, True])
def test_a_connection_scope_owns_one_changeset(failing: bool) -> None:
    async def consumer(scope: Any, receive: Any, send: Any) -> None:
        await database_sync_to_async(write)("first")
        await database_sync_to_async(write)("second")
        if failing:
            raise RuntimeError("consumer failed")

    application = ChangesetScopeMiddleware(consumer)
    if failing:
        with pytest.raises(RuntimeError):
            async_to_sync(application)({"type": "websocket"}, None, None)
    else:
        async_to_sync(application)({"type": "websocket"}, None, None)
    (context,) = changesets()
    assert {changeset_of(row) for row in OwnedRow.system_queryset()} == {context}
    assert (context.state, context.outcome) == ("closed", "failure" if failing else "success")


def test_worker_threads_record_their_own_changesets_under_the_shared_facts() -> None:
    def work(title: str) -> Any:
        close_old_connections()
        try:
            write(title)
            return current_changeset().pk  # type: ignore[union-attr]
        finally:
            connections.close_all()

    def in_new_thread(title: str) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(copy_context().run, work, title).result(timeout=10)

    with changeset(fresh=True, change_reason="pool"):
        write("main")
        main_pk = current_changeset().pk  # type: ignore[union-attr]
        worker_pks = [in_new_thread(title) for title in ("a", "b")]
    assert len({main_pk, *worker_pks}) == 3
    assert {row.title: changeset_of(row).pk for row in OwnedRow.system_queryset()} == {
        "main": main_pk,
        "a": worker_pks[0],
        "b": worker_pks[1],
    }
    assert {(context.change_reason, context.state, context.outcome) for context in changesets()} == {
        ("pool", "closed", "success")
    }


def test_each_actor_under_one_boundary_records_its_own_changeset() -> None:
    alice, bob = create_user("alice"), create_user("bob")
    with changeset(fresh=True, change_reason="loop"):
        write("system")
        with actor_context(alice):
            write("alice")
            write("alice-again")
        with actor_context(bob):
            write("bob")
    by_title = {row.title: changeset_of(row) for row in OwnedRow.system_queryset()}
    assert by_title["alice"] == by_title["alice-again"]
    assert len({context.pk for context in by_title.values()}) == 3
    assert {title: context.actor_ref for title, context in by_title.items()} == {
        "system": "",
        "alice": str(to_subject_ref(alice)),
        "alice-again": str(to_subject_ref(alice)),
        "bob": str(to_subject_ref(bob)),
    }
    # Each user's storage Trash folder keeps history, so creating the users records its own boundary.
    assert {(context.change_reason, context.state, context.outcome) for context in changesets()} == {
        ("loop", "closed", "success"), ("storage.trash_folder", "closed", "success"),
    }


@pytest.mark.parametrize("fail", [False, True])
def test_celery_task_owns_attribution_and_correlation_on_success_and_failure(fail: bool) -> None:
    """Celery's configured task base owns one boundary around the actual eager run."""

    assert issubclass(celery_app.Task, Task)
    name = f"tests.attributed_task.{fail}"

    @celery_app.task(name=name, lazy=False, shared=False)
    def attributed() -> str:
        write("task write")
        if fail:
            raise RuntimeError("task failed")
        return "done"

    try:
        if fail:
            with pytest.raises(RuntimeError, match="task failed"):
                attributed.apply(task_id="task-correlation", throw=True)
        else:
            assert attributed.apply(task_id="task-correlation", throw=True).result == "done"
    finally:
        celery_app.tasks.pop(name, None)
    (context,) = changesets()
    assert context.change_reason == f"task.{name}"
    assert context.correlation_id == "task-correlation"
    assert (context.state, context.outcome) == ("closed", "failure" if fail else "success")
    assert current_changeset() is None


@pytest.mark.parametrize("errors", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("join", [False, True])
def test_graphql_operation_attributes_direct_and_transport_execution(errors, asynchronous, join):
    @strawberry.type
    class Query:
        @strawberry.field
        def ready(self) -> bool:
            write()
            if errors:
                raise ValueError("boom")
            return True

    @strawberry.type
    class AsyncQuery:
        @strawberry.field
        async def ready(self) -> bool:
            await sync_to_async(write)()
            if errors:
                raise ValueError("boom")
            return True

    schema = AngeeSchema(query=AsyncQuery if asynchronous else Query)
    with changeset(fresh=True) if join else nullcontext():
        result = (async_to_sync(schema.execute)("{ready}") if asynchronous
                  else schema.execute_sync("{ready}"))
        assert bool(result.errors) == errors
    (context,) = changesets()
    assert (context.state, context.outcome) == ("closed", "failure" if errors else "success")



@pytest.mark.parametrize("boundary_outer", [True, False])
@pytest.mark.parametrize("asynchronous", [True, False])
def test_request_captures_actor_in_either_boundary_order(boundary_outer, asynchronous):
    with changeset(fresh=True):
        user = create_user("request-actor")
    request = RequestFactory().get("/")
    request.user = user

    def response(request):
        write("request-actor")
        return HttpResponse()

    async def async_response(request):
        await sync_to_async(write)("request-actor")
        return HttpResponse()

    endpoint = async_response if asynchronous else response
    endpoint = (ChangesetMiddleware(ActorMiddleware(endpoint)) if boundary_outer
                else ActorMiddleware(ChangesetMiddleware(endpoint)))
    if asynchronous:
        async_to_sync(endpoint)(request)
    else:
        endpoint(request)
    context = changeset_of(OwnedRow.system_queryset().get(title="request-actor"))
    assert context.actor_ref == str(to_subject_ref(user))
    assert (context.state, context.outcome) == ("closed", "success")


def test_direct_mcp_graphql_keeps_partial_writes_and_failed_attribution():
    @strawberry.type
    class Query:
        ready: bool = True

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def create(self) -> bool:
            write("partial MCP success")
            return True

        @strawberry.mutation
        def fail(self) -> bool | None:
            raise ValueError("handled failure")

    schema = AngeeSchema(query=Query, mutation=Mutation)
    with pytest.raises(ToolError):
        _execute_sync(schema, "mutation {create fail}", None)
    record = OwnedRow.system_queryset().get(title="partial MCP success")
    context = changeset_of(record)
    assert (context.state, context.outcome) == ("closed", "failure")
