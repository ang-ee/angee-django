"""Which parties a person confirmed on an address: every confirmation, not only the party the address resolves to.

``PartyStatementQuerySet.confirmed`` is the applied statements a person confirmed, the stance
``Statement.is_confirmed`` reads; ``PartyQuerySet.confirmed_on(handles)`` is the canonical parties confirmed on
any of the handles, in one query.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party
from tests.conftest import create_platform_admin, create_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(db: None) -> Any:
    del db
    return create_platform_admin("confirmed-on-admin")


def _handle(owner: Any, value: str) -> Any:
    with system_context(reason="tests.parties.confirmed_on"):
        return Handle.objects.upsert(platform="email", value=value, created_by_id=owner.pk)


def _party(owner: Any, name: str) -> Any:
    with system_context(reason="tests.parties.confirmed_on"):
        return Party._base_manager.create(display_name=name, created_by=owner)


def _confirm(admin: Any, party: Any, handle: Any) -> None:
    with actor_context(admin):
        party.with_actor(admin).confirm_statement(aspect="handle", handle=handle, values={})


def _confirmed_on(*handles: Any) -> list[Any]:
    with system_context(reason="tests.parties.confirmed_on"):
        rows = Handle._base_manager.filter(pk__in=[handle.pk for handle in handles])
        return list(Party.objects.confirmed_on(rows).order_by("pk").values_list("pk", flat=True))


@pytest.mark.parametrize("last", ["first", "second"])
def test_every_party_a_person_confirmed_on_an_address_is_confirmed_on_it_whoever_was_last(
    admin: Any, last: str,
) -> None:
    """The handle resolves to one of them; both hold a confirmed claim."""

    handle = _handle(admin, "billing@supplier.example")
    first, second = _party(admin, "First"), _party(admin, "Second")
    for party in (second, first) if last == "first" else (first, second):
        _confirm(admin, party, handle)

    assert _confirmed_on(handle) == [first.pk, second.pk]


def test_a_claim_no_person_confirmed_and_a_dismissal_confirm_nobody(admin: Any) -> None:
    """A source's applied claim stands without a confirmation; a person's dismissal is no confirmation."""

    claimed, dismissed = _handle(admin, "claimed@example.test"), _handle(admin, "dismissed@example.test")
    party = _party(admin, "Claimant")
    with system_context(reason="tests.parties.confirmed_on"):
        Party.objects.assert_handle(party, claimed, asserted_by=admin)
    with actor_context(admin):
        party.with_actor(admin).dismiss_statement(aspect="handle", handle=dismissed)

    assert _confirmed_on(claimed, dismissed) == []


def test_a_handles_own_confirmed_party_is_confirmed_on_it_without_a_statement(admin: Any) -> None:
    """A row stored before its statements carries its confirmed party on the row itself."""

    handle = _handle(admin, "stored@example.test")
    party = _party(admin, "Stored")
    with system_context(reason="tests.parties.confirmed_on"):
        Handle._base_manager.filter(pk=handle.pk).update(party=party, party_link_confirmed=True)

    assert _confirmed_on(handle) == [party.pk]


def test_the_scope_and_the_stance_agree_on_every_statement(admin: Any) -> None:
    """``confirmed()`` returns exactly the applied statements whose ``is_confirmed`` holds."""

    handles = [_handle(admin, f"stance-{index}@example.test") for index in range(3)]
    party = _party(admin, "Stances")
    _confirm(admin, party, handles[0])
    with system_context(reason="tests.parties.confirmed_on"):
        Party.objects.assert_handle(party, handles[1], asserted_by=admin)
    with actor_context(admin):
        party.with_actor(admin).dismiss_statement(aspect="handle", handle=handles[2])
    with system_context(reason="tests.parties.confirmed_on"):
        statements = Party.objects.handle_statements().filter(subject=party)
        by_stance = {row.pk for row in statements.applied() if row.is_confirmed}
        assert set(statements.confirmed().values_list("pk", flat=True)) == by_stance
        assert len(by_stance) == 1


def test_the_parties_it_is_called_on_keep_their_actor_scope(admin: Any) -> None:
    """The confirmations are read structurally; which parties a reader sees is the caller's party scope."""

    handle = _handle(admin, "shared@example.test")
    shown, hidden = _party(admin, "Shown"), _party(create_user("confirmed-on-other"), "Hidden")
    for party in (shown, hidden):
        _confirm(admin, party, handle)
    reader = create_user("confirmed-on-reader")
    write_relationships([RelationshipTuple(to_object_ref(shown), "reader", to_subject_ref(reader))])
    rows = Handle._base_manager.filter(pk=handle.pk)
    with actor_context(admin):
        assert set(Party.objects.with_actor(admin).confirmed_on(rows).values_list("pk", flat=True)) == {
            shown.pk, hidden.pk,
        }
    with actor_context(reader):
        assert list(Party.objects.with_actor(reader).confirmed_on(rows).values_list("pk", flat=True)) == [shown.pk]


@pytest.mark.parametrize("count", [1, 5, 50])
def test_the_read_is_one_statement_whatever_the_number_of_handles(admin: Any, count: int) -> None:
    """The confirmations and the handles are subqueries of the parties' one query."""

    handles = [_handle(admin, f"many-{index}@example.test") for index in range(count)]
    party = _party(admin, "Many")
    for handle in handles:
        _confirm(admin, party, handle)
    with system_context(reason="tests.parties.confirmed_on"):
        rows = Handle._base_manager.filter(pk__in=[handle.pk for handle in handles])
        with CaptureQueriesContext(connection) as queries:
            assert list(Party.objects.confirmed_on(rows).values_list("pk", flat=True)) == [party.pk]

    assert len(queries) == 1
