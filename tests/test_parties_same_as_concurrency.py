"""PostgreSQL interleavings of same-person pair and party merge locks with handle resolution.

A pair locks its two endpoints in one identity pass (``Party.lock_subjects``):
both parties' Handles, then the parties. Locking party after party would take
the second party's Handles after the first party's row, the reverse of the
Handle, Party order resolution keeps, and the two would deadlock.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import actor_context, system_context

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


def test_a_merge_takes_its_pair_partners_handles_before_any_party() -> None:
    """A merge re-keys the merged party's other same-person pairs onto the survivor, so it locks the partners too.

    B holds hb and C claims it, so (B, C) awaits review; C holds hc. Merging B into A
    re-keys (B, C) to (A, C), which locks C's Handles. A resolution of hc that then
    goes on to C must make the merge wait on hc before it holds any party, not deadlock.
    """

    owner = create_user("same-as-merge-lock-order")
    with system_context(reason="same_as merge lock order fixture"):
        survivor, merged, partner = (
            Party._base_manager.create(display_name=name, created_by=owner) for name in ("Ada", "Bea", "Cy")
        )
        addresses = {}
        for party, value in ((survivor, "a@example.com"), (merged, "b@example.com"), (partner, "c@example.com")):
            addresses[party.pk] = Handle._base_manager.create(platform="email", value=value, created_by=owner)
            Party.objects.assert_handle(party, addresses[party.pk], asserted_by=owner)
        Party.objects.propose_claimed_handle_as_system(
            partner, addresses[merged.pk], evidence=addresses[merged.pk], reason="tests.claim",
        )
        assert Party.objects.reconcile_same_as() == 1

    def merge() -> None:
        with actor_context(owner):
            Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="same_as merge lock order resolution"):
            # A resolution of the partner's own address takes its Handle row ...
            list(Handle._base_manager.filter(pk=addresses[partner.pk].pk).select_for_update())
            contender, pid = submit(pool, merge)
            wait_for_lock(pid, contender)
            # ... then goes on to the partner, which the merge must not hold yet.
            list(Party._base_manager.filter(pk=partner.pk).select_for_update())
        contender.result(timeout=20)

    with system_context(reason="same_as merge lock order readback"):
        assert Party._base_manager.get(pk=merged.pk).merged_into_id == survivor.pk
        assert Party.same_as.model._base_manager.filter(
            left_id=min(survivor.pk, partner.pk), right_id=max(survivor.pk, partner.pk),
        ).exists()
