"""PostgreSQL interleavings of the organization domain key: the key repair beside a writer and beside another repair.

The repair reads every organization's domain and key, then reads a stale row
again under its row lock (``OrganizationManager.renormalize_domains`` over
``repair_keys``). Between the two reads a writer may save the row or rewrite its
domain by a queryset update; the second read decides. The lock is ``FOR NO KEY
UPDATE``: a repair writes no column another row's foreign key references, so a
writer inserting a row that references a locked party does not wait on it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest
from django.apps import apps
from django.db import connection, transaction
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.querysets import row_lock_queryset
from angee.messaging.testing.models import Handle, Organization
from angee.parties import managers
from tests.conftest import create_user
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def _stale(count: int = 3) -> list[Any]:
    """``count`` committed organizations as their migration left them: a domain and no key."""

    owner = create_user("organization-domains-race")
    with system_context(reason="organization domain key race fixture"):
        rows = [
            Organization._base_manager.create(display_name=f"Race {index}", domain=f"Straße{index}.example",
                                              created_by=owner)
            for index in range(count)
        ]
        Organization._base_manager.filter(pk__in=[row.pk for row in rows]).update(domain_key="")
    return rows


def _stored() -> dict[Any, tuple[str, str]]:
    with system_context(reason="organization domain key race"):
        return {pk: (domain, key) for pk, domain, key in Organization._base_manager.values_list(
            "pk", "domain", "domain_key",
        )}


def _versions(pk: Any) -> int:
    with system_context(reason="organization domain key race"):
        return Organization.history.model._base_manager.filter(id=pk).count()


def _repair() -> int:
    return Organization.objects.renormalize_domains().saved


def test_a_writer_holding_the_row_saves_a_domain_and_the_repair_waits_reads_it_again_and_writes_nothing_more() -> None:
    """The writer's save keys its domain; the repair, parked on the row, finds the key current and leaves it."""

    first, raced, last = _stale()
    before = _versions(raced.pk)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="organization domain key race holder"):
            held = row_lock_queryset(Organization).get(pk=raced.pk)
            future, pid = submit(pool, _repair)
            wait_for_lock(pid, future)
            held.domain = "xn--bcher-kva.example"
            held.save(update_fields=["domain", "updated_at"])
        saved = future.result(timeout=20)

    assert saved == 2
    assert _stored()[raced.pk] == ("xn--bcher-kva.example", "bücher.example")
    assert _versions(raced.pk) == before + 1, "the writer's save is the row's one new version"


def test_a_domain_a_holder_rewrites_by_queryset_update_is_keyed_after_the_repair_waits() -> None:
    """A queryset update writes the domain and no key; the repair reads the row again under its lock and keys it."""

    first, raced, last = _stale()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="organization domain key race holder"):
            row_lock_queryset(Organization).get(pk=raced.pk)
            future, pid = submit(pool, _repair)
            wait_for_lock(pid, future)
            Organization._base_manager.filter(pk=raced.pk).update(domain="Other.example", domain_key="")
        saved = future.result(timeout=20)

    assert saved == 3
    assert _stored()[raced.pk] == ("Other.example", "other.example")


def test_two_repairs_at_once_key_each_row_once() -> None:
    """Both repairs select every row; the one that locks a row first keys it, and the other finds it current."""

    rows = _stale(5)
    versions = {row.pk: _versions(row.pk) for row in rows}
    with ThreadPoolExecutor(max_workers=2) as pool:
        with transaction.atomic(), system_context(reason="organization domain key race holder"):
            row_lock_queryset(Organization).get(pk=rows[0].pk)
            first, first_pid = submit(pool, _repair)
            second, second_pid = submit(pool, _repair)
            wait_for_lock(first_pid, first)
            wait_for_lock(second_pid, second)
        results = (first.result(timeout=30), second.result(timeout=30))

    assert sum(results) == 5
    assert {row.pk: _versions(row.pk) - versions[row.pk] for row in rows} == dict.fromkeys(versions, 1)
    assert all(key == Handle.normalize_email_domain(domain) for domain, key in _stored().values())


def test_a_writer_committing_a_reference_to_a_party_the_repair_holds_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repair's ``FOR NO KEY UPDATE`` leaves a foreign key's ``FOR KEY SHARE`` free: an address commits at once."""

    (raced,) = _stale(1)
    locked, released = Event(), Event()
    derived = managers.most_derived_concrete

    def hold(row: Any) -> Any:
        # The key write's own step, under the repair's row lock: hold it there once, while the writer commits.
        if not locked.is_set():
            locked.set()
            assert released.wait(timeout=30), "The test never released the repair."
        return derived(row)

    monkeypatch.setattr(managers, "most_derived_concrete", hold)
    address = apps.get_model("parties", "Address")

    def reference() -> Any:
        with system_context(reason="organization domain key race writer"), transaction.atomic():
            return address._base_manager.create(
                party_id=raced.pk, city="Springfield", country="US", created_by_id=raced.created_by_id,
            ).pk

    with ThreadPoolExecutor(max_workers=1) as repairs, ThreadPoolExecutor(max_workers=1) as writers:
        repair, _repair_pid = submit(repairs, _repair)
        try:
            assert locked.wait(timeout=20), "The repair never reached its row lock."
            written, _writer_pid = submit(writers, reference)
            assert written.result(timeout=10) is not None
        finally:
            released.set()
        assert repair.result(timeout=20) == 1
