"""PostgreSQL interleavings of the message template verbs with each other, with console writes and with a reload.

``override()`` locks the shipped default, ``keep_override()`` the override, and a
reload's update locks the default it changes, so each contender here parks on a
row lock the holder took and then answers from the committed rows. Row locks do
nothing on SQLite, so this module runs only on PostgreSQL.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_of
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging_templates.testing.models import MessageTemplate
from tests.conftest import create_platform_admin
from tests.lock_waits import submit, wait_for_lock
from tests.test_messaging_templates import _CONTENT, _LAYOUT_HEADERS, _OVERRIDE, _UPDATE, _load, _read, _row, _schema

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def _override(pk: Any, admin: Any) -> Any:
    with actor_context(admin):
        return MessageTemplate.objects.override(MessageTemplate.objects.get(pk=pk))


def _keep(pk: Any, admin: Any) -> Any:
    with actor_context(admin):
        return MessageTemplate.objects.keep_override(MessageTemplate.objects.get(pk=pk))


EDITED = f"<main>{_CONTENT}Edited</main>"
"""An edit a save walks: a layout's forms hold the content block."""


def _overrides(key: str = "base") -> list[Any]:
    with system_context(reason="tests.messaging_templates.concurrency.read"):
        return list(MessageTemplate.objects.filter(key=key, is_baseline=False))


def _release(baseline: Any) -> None:
    with system_context(reason="tests.messaging_templates.concurrency.release"):
        released = MessageTemplate.objects.get(pk=baseline.pk)
        released.source = "<main>Released</main>"
        released.save()


def test_a_second_override_waits_on_the_default_and_returns_the_first() -> None:
    admin = create_platform_admin("messaging-templates-race-overrider")
    baseline = _row(baseline=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            first = _override(baseline.pk, admin)
            contender, pid = submit(pool, lambda: _override(baseline.pk, admin).pk)
            wait_for_lock(pid, contender)
        assert contender.result(timeout=10) == first.pk
    assert [row.pk for row in _overrides()] == [first.pk]


def test_the_override_verb_racing_an_insert_of_the_same_row_returns_that_row() -> None:
    """A console insert does not take the default's lock; the verb's insert waits on the row, then answers with it."""

    admin = create_platform_admin("messaging-templates-race-inserter")
    baseline = _row(baseline=True)
    verb = lambda: result_data(  # noqa: E731
        execute_schema(_schema(), _OVERRIDE, {"id": public_id_of(baseline)}, user=admin),
    )["override_message_template"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            own = _row(source="<main>Inserted first</main>")
            contender, pid = submit(pool, verb)
            wait_for_lock(pid, contender)
        answer = contender.result(timeout=10)
    assert answer == {"ok": True, "message": "Message template overridden.", "id": own.sqid}
    assert [row.pk for row in _overrides()] == [own.pk]


@pytest.mark.parametrize("holder", ["edit", "keep"])
def test_keep_and_an_edit_of_the_override_serialise_and_lose_nothing(holder: str) -> None:
    admin = create_platform_admin(f"messaging-templates-race-{holder}")
    baseline = _row(baseline=True)
    override = _row(source="<main>Ours</main>")
    _release(baseline)

    def edit() -> Any:
        variables = {"id": public_id_of(override), "set": {"source": EDITED}}
        return result_data(execute_schema(_schema(), _UPDATE, variables, user=admin))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            if holder == "edit":
                with system_context(reason="tests.messaging_templates.concurrency.edit"):
                    held = MessageTemplate.system_queryset(lock=("self",)).get(pk=override.pk)
                    held.source = EDITED
                    held.save()
                contender, pid = submit(pool, lambda: _keep(override.pk, admin))
            else:
                _keep(override.pk, admin)
                contender, pid = submit(pool, edit)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    kept = _read(override)
    assert kept.source == EDITED
    assert kept.baseline_hash == _read(baseline).content_hash
    assert not kept.has_updated_default


@pytest.mark.parametrize("holder", ["reload", "override"])
def test_an_override_and_a_reload_of_its_default_serialise_and_tell_the_truth(holder: str) -> None:
    """Reload first: the override copies the new text. Override first: the reload waits, then the default moved."""

    admin = create_platform_admin(f"messaging-templates-race-{holder}-first")
    one = ("base", "layout", "base", "en", "Base", "html", f"<main>{_CONTENT}One</main>", f"{_CONTENT}One")
    two = ("base", "layout", "base", "en", "Base", "html", f"<main>{_CONTENT}Two</main>", f"{_CONTENT}Two")
    _load(one, headers=_LAYOUT_HEADERS)
    with system_context(reason="tests.messaging_templates.concurrency.read"):
        baseline = MessageTemplate.objects.get(key="base", is_baseline=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            if holder == "reload":
                _load(two, headers=_LAYOUT_HEADERS)
                contender, pid = submit(pool, lambda: _override(baseline.pk, admin).pk)
            else:
                _override(baseline.pk, admin)
                contender, pid = submit(pool, lambda: _load(two, headers=_LAYOUT_HEADERS).updated)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    (override,) = _overrides()
    assert _read(baseline).source == f"<main>{_CONTENT}Two</main>"
    if holder == "reload":
        assert (override.source, override.has_updated_default) == (f"<main>{_CONTENT}Two</main>", False)
    else:
        assert (override.source, override.has_updated_default) == (f"<main>{_CONTENT}One</main>", True)
