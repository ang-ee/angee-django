"""PostgreSQL interleavings of a same-person pair's locks with handle resolution.

A pair locks its two endpoints in one identity pass (``Party.lock_subjects``):
both parties' Handles, then the parties. Locking party after party would take
the second party's Handles after the first party's row, the reverse of the
Handle, Party order resolution keeps, and the two would deadlock.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party
from tests.conftest import create_user
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_a_pair_takes_both_endpoints_handles_before_either_party() -> None:
    """A resolution holding the second endpoint's address goes on to the first party; the pair waits, no deadlock."""

    owner = create_user("same-as-lock-order")
    with system_context(reason="same_as lock order fixture"):
        first = Party._base_manager.create(display_name="Ada Example", created_by=owner)
        second = Party._base_manager.create(display_name="A. Example", created_by=owner)
        addresses = {}
        for party, value in ((first, "ada@example.com"), (second, "a.example@example.com")):
            addresses[party.pk] = Handle._base_manager.create(platform="email", value=value, created_by=owner)
            Party.objects.assert_handle(party, addresses[party.pk], asserted_by=owner)

    def pair() -> None:
        with system_context(reason="same_as lock order pair"), transaction.atomic():
            Party.same_as.for_pair(first.with_actor(owner), second)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="same_as lock order resolution"):
            # A resolution of the second endpoint's address: its Handle, then its party ...
            Party.objects.lock_identity_rows(party_ids=(), handle_ids=(addresses[second.pk].pk,))
            contender, pid = submit(pool, pair)
            wait_for_lock(pid, contender)
            # ... then the first party, which the pair has not locked: it waits on the Handle above.
            Party.objects.lock_identity_rows(party_ids=(first.pk,), handle_ids=())
        contender.result(timeout=10)

    with system_context(reason="same_as lock order readback"):
        assert Party.same_as.model._base_manager.filter(left_id=first.pk, right_id=second.pk).exists()
