"""Production entrypoints own their changeset boundaries; this module runs without the test one."""

from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

import pytest
import strawberry
from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from channels.db import database_sync_to_async
from django.apps import apps
from django.db import close_old_connections, connections
from django.http import HttpResponse
from django.test import RequestFactory
from graphql import GraphQLError
from rebac import actor_context, system_context, to_subject_ref
from strawberry.types import ExecutionResult

from angee.base.changesets import ChangesetRequired, changeset, changeset_model_label, current_changeset
from angee.base.middleware import ChangesetMiddleware, ChangesetScopeMiddleware
from angee.compose import manage
from angee.graphql.views import AngeeGraphQLView
from tests.conftest import create_user
from tests.core_persistence import OwnedRow

pytestmark = [pytest.mark.entrypoint_boundaries, pytest.mark.django_db(transaction=True)]


def write(title: str = "captured") -> Any:
    """Capture one history row as a system write under whatever actor is ambient."""

    with system_context(reason="boundary-test"):
        return OwnedRow.objects.create(title=title)


def changesets() -> list[Any]:
    return list(apps.get_model(changeset_model_label())._base_manager.order_by("pk"))


def changeset_of(row: Any) -> Any:
    return row.history.get(history_type="+").changeset


@pytest.mark.parametrize("command", sorted(manage.SERVER_COMMANDS))
def test_servers_run_without_a_process_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, command: str) -> None:
    seen: list[Any] = []
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(manage, "execute_from_command_line", lambda argv: seen.append(current_changeset()))
    manage.main(["manage.py", command])
    assert seen == [None]


@pytest.mark.parametrize("command", ["shell", "migrate"])
def test_main_attributes_a_command_to_one_closed_changeset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, command: str
) -> None:
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(manage, "execute_from_command_line", lambda argv: write())
    manage.main(["manage.py", command])
    (context,) = changesets()
    assert changeset_of(OwnedRow.system_queryset().get()) == context
    assert (context.change_reason, context.activity, context.actor) == (f"manage.{command}", "system", "")
    assert (context.state, context.outcome) == ("closed", "success")


def test_a_thread_a_command_starts_writes_under_the_command_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A whole-process entrypoint publishes its boundary: a plain thread writes under it, in its own row."""

    def command(argv: list[str]) -> None:
        write("main thread")
        thread = threading.Thread(target=lambda: (write("plain thread"), connections.close_all()))
        thread.start()
        thread.join()

    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(manage, "execute_from_command_line", command)
    manage.main(["manage.py", "migrate"])
    rows = changesets()
    assert len(rows) == 2
    assert {row.change_reason for row in rows} == {"manage.migrate"}
    assert {changeset_of(row) for row in OwnedRow.system_queryset().all()} == set(rows)
    assert current_changeset() is None


def test_a_write_without_a_boundary_names_the_manage_py_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["/srv/app/manage.py", "seed_lorem_notes"])
    with pytest.raises(ChangesetRequired, match=r"manage\.py must call angee\.compose\.manage\.main\(\)"):
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
    assert {title: context.actor for title, context in by_title.items()} == {
        "system": "",
        "alice": str(to_subject_ref(alice)),
        "alice-again": str(to_subject_ref(alice)),
        "bob": str(to_subject_ref(bob)),
    }
    # Each user's storage Trash folder keeps history, so creating the users records its own boundary.
    assert {(context.change_reason, context.state, context.outcome) for context in changesets()} == {
        ("loop", "closed", "success"), ("storage.trash_folder", "closed", "success"),
    }


@pytest.mark.parametrize("errors", [False, True])
def test_graphql_errors_close_the_request_changeset_as_failed(errors: bool) -> None:
    @strawberry.type
    class Query:
        @strawberry.field
        def ready(self) -> bool:
            return True

    view = AngeeGraphQLView(schema=strawberry.Schema(query=Query))
    result = ExecutionResult(data=None, errors=[GraphQLError("boom")] if errors else None)
    with changeset(fresh=True):
        write()
        assert view.process_result(RequestFactory().post("/graphql/"), result) == (
            {"data": None, "errors": [{"message": "boom"}]} if errors else {"data": None}
        )
    (context,) = changesets()
    assert (context.state, context.outcome) == ("closed", "failure" if errors else "success")
