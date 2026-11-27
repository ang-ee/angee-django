"""Which parties are confirmed on an address: every confirmation, not only the party the address resolves to.

``StatementQuerySet.confirmed`` is the applied statements whose stance is a confirmation (``CONFIRMED_STANCE``, a
person's or a seed's preferred statement with a value); ``PartyQuerySet.confirmed_on(handles)`` is the canonical
parties confirmed on any of the handles, in one query.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party
from angee.provenance.testing.models import Seed
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


def test_a_withdrawn_confirmation_is_one_as_a_row_and_is_not_in_the_scope(admin: Any) -> None:
    """``is_confirmed`` checks the stance alone; ``confirmed()`` keeps only applied statements. On purpose."""

    handle = _handle(admin, "changed-mind@example.test")
    party = _party(admin, "Changed mind")
    _confirm(admin, party, handle)
    with system_context(reason="tests.parties.confirmed_on"):
        assert party.statements.withdraw(admin) == 1
        (confirmation,) = Party.objects.handle_statements().filter(subject=party, handle=handle)
        assert confirmation.is_confirmed and confirmation.status == confirmation.StatementStatus.WITHDRAWN
        assert not Party.objects.handle_statements().confirmed().filter(subject=party).exists()
    assert _confirmed_on(handle) == []


def test_the_dismissal_stance_is_one_mapping_for_the_property_and_a_query(admin: Any) -> None:
    """``DISMISSED_STANCE`` is what ``is_dismissed`` checks and what a query for dismissals filters by."""

    handles = [_handle(admin, f"dismissal-{index}@example.test") for index in range(3)]
    party = _party(admin, "Dismissals")
    _confirm(admin, party, handles[0])
    with actor_context(admin):
        party.with_actor(admin).dismiss_statement(aspect="handle", handle=handles[1])
    with system_context(reason="tests.parties.confirmed_on"):
        Party.objects.assert_handle(party, handles[2], asserted_by=admin)
        statements = Party.objects.handle_statements().filter(subject=party)
        stance = statements.model.DISMISSED_STANCE
        assert set(statements.filter(**stance).values_list("pk", flat=True)) == {
            row.pk for row in statements if row.is_dismissed
        }
        assert statements.filter(**stance).count() == 1
    with actor_context(admin):
        party.with_actor(admin).dismiss_statement(aspect="handle", handle=handles[0])
    with system_context(reason="tests.parties.confirmed_on"):
        # The person's later stance replaces their row: a dismissal, with no confirmation left beside it.
        (row,) = Party.objects.handle_statements().model._base_manager.filter(subject=party, handle=handles[0])
        assert row.is_dismissed and not row.is_confirmed


def test_a_seeds_preferred_statement_confirms_and_a_holder_a_seed_row_names_is_stated_as_before(admin: Any) -> None:
    """A seed may confirm, as a person may; a seeded row's holder is a normal claim unless the row carries its flag.

    Each answer is the one the handle's resolved link gave before (``HandleQuerySet.confirmed``).
    """

    with system_context(reason="tests.parties.confirmed_on"):
        seed = Seed._base_manager.create(key="tests.parties.confirmed_on.seed")
    # A seed states parties' facts as parties' own rules do: a member of its statement-source role.
    write_relationships([
        RelationshipTuple(ObjectRef("provenance/role", "parties_statement_source"), "member", to_subject_ref(seed)),
    ])
    confirmed_by_seed, named, named_and_flagged = (
        _handle(admin, value) for value in ("seeded@example.test", "named@example.test", "flagged@example.test")
    )
    party = _party(admin, "Seeded")
    with system_context(reason="tests.parties.confirmed_on"):
        party.statements.set(
            aspect="handle", handle=confirmed_by_seed, values={}, asserted_by=seed,
            **Party.objects.handle_statements().model.CONFIRMED_STANCE,
        )
        Handle._base_manager.filter(pk=named.pk).update(party=party)
        Handle._base_manager.filter(pk=named_and_flagged.pk).update(party=party, party_link_confirmed=True)
        loaded = list(Handle._base_manager.filter(pk__in=[named.pk, named_and_flagged.pk]).order_by("pk"))
        Handle.after_resource_load(loaded, tier="demo", source="tests.parties.confirmed_on")
        before = {
            handle.pk: Handle.objects.confirmed("email", handle.value).filter(pk=handle.pk).exists()
            for handle in (confirmed_by_seed, named, named_and_flagged)
        }

    assert before == {confirmed_by_seed.pk: True, named.pk: False, named_and_flagged.pk: True}
    assert [_confirmed_on(handle) == [party.pk] for handle in (confirmed_by_seed, named, named_and_flagged)] == [
        True, False, True,
    ]
