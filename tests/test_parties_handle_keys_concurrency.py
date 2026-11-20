"""PostgreSQL interleavings of the handle-key repair with another process's write to a row it selected.

The repair reads the keys of a batch, then reads those rows again under their
locks (``HandleManager.renormalize_handles``). Between the two reads another
process may delete a row or save it. The run passes both, because each batch
starts after the last key the run selected, whatever became of that row.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, transaction
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle
from angee.parties.managers import KeyRepair
from tests.conftest import create_user
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def _behind() -> tuple[Any, Any, Any]:
    """Three committed handles as an earlier version of the rule left them, oldest first."""

    owner = create_user("handle-keys-race")
    with system_context(reason="handle key repair race fixture"):
        rows = [
            Handle.objects.upsert(
                platform="email", value=f"ada-{index}@xn--bcher-kva.example", created_by_id=owner.pk,
            )
            for index in range(3)
        ]
        Handle._base_manager.filter(pk__in=[row.pk for row in rows]).update(
            normalized_value="behind", normalized_version=0,
        )
    first, raced, last = rows
    return first, raced, last


def _keyed() -> dict[Any, tuple[str, int]]:
    """Every handle row there is: its comparison key and the version beside it."""

    rows = Handle._base_manager.values_list("pk", "normalized_value", "normalized_version")
    return {pk: (key, version) for pk, key, version in rows}


def _versions(handle: Any) -> int:
    history: Any = Handle.history
    return history.model._base_manager.filter(id=handle.pk).count()


def test_a_row_deleted_after_the_repair_selected_it_is_passed_and_the_others_are_saved() -> None:
    """The run has the row's key and waits on its lock; the row is gone when the lock is released."""

    first, raced, last = _behind()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="handle key repair race holder"):
            # Another process holds the row ...
            Handle.objects.locked_get(pk=raced.pk)
            contender, pid = submit(pool, Handle.objects.renormalize_handles)
            # ... the repair, which read the batch's keys without a lock, waits on it ...
            wait_for_lock(pid, contender)
            # ... and the other process deletes it before it commits.
            Handle._base_manager.filter(pk=raced.pk).delete()
        repair = contender.result(timeout=10)

    current = Handle.NORMALIZATION_VERSION
    assert repair == KeyRepair(saved=2)
    assert _keyed() == {first.pk: ("ada-0@bücher.example", current), last.pk: ("ada-2@bücher.example", current)}


def test_a_row_another_process_saved_after_the_repair_selected_it_is_read_current_and_not_saved_again() -> None:
    """The run reads the row again under its lock, finds it at the rule's version and writes nothing to it."""

    first, raced, last = _behind()
    versions = _versions(raced)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="handle key repair race holder"):
            held = Handle.objects.locked_get(pk=raced.pk)
            contender, pid = submit(pool, Handle.objects.renormalize_handles)
            wait_for_lock(pid, contender)
            # The other process's own save keys the row under the rule's version before it commits.
            held.save(update_fields=["normalized_value"])
        repair = contender.result(timeout=10)

    current = Handle.NORMALIZATION_VERSION
    assert repair == KeyRepair(saved=2)
    assert _keyed() == {
        row.pk: (f"ada-{index}@bücher.example", current) for index, row in enumerate((first, raced, last))
    }
    # The other process's save is the row's last write: one history version, and the stamp that save gave.
    assert _versions(raced) == versions + 1
    assert Handle._base_manager.get(pk=raced.pk).updated_at == held.updated_at
