"""Who an email address names: ``PartyManager.people_for_emails``, its bases, and what is nobody's answer."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.messaging.testing.models import Handle, Organization, Party, Person
from angee.parties.managers import EmailBasis, EmailHolder

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(db: None) -> Any:
    del db
    with system_context(reason="tests.people_for_emails.owner"):
        user = get_user_model().objects.create_user(username="email-owner")
    with system_context(reason="tests.people_for_emails"):
        yield user


def _person(owner: Any, name: str) -> Any:
    return Person.objects.create(display_name=name, created_by=owner)


def _holds(party: Any, value: str, owner: Any, *, confidence: float = 1.0) -> Any:
    """State ``value`` as ``party``'s by a matching rule: at full confidence it resolves the handle, below it claims."""

    handle = Handle.objects.upsert(platform="email", value=value, created_by_id=owner.pk)
    rule = Party.objects.rule_source("display_name")
    Party.objects.assert_handle(party, handle, asserted_by=rule, confidence=confidence)
    handle.refresh_from_db()
    return handle


def _answer(*emails: str) -> list[tuple[str, Any, EmailBasis, Any]]:
    return [
        (holder.email, holder.person.pk, holder.basis, holder.handle.pk if holder.handle else None)
        for holder in Party.objects.people_for_emails(emails)
    ]


def test_the_person_holding_an_address_answers_any_spelling_of_it(owner: Any) -> None:
    """Handles compare by their normalized value, so the row another spelling made still answers."""

    ada = _person(owner, "Ada Example")
    handle = _holds(ada, "Ada.Example@Example.COM", owner)
    assert handle.party_id == ada.pk

    assert _answer("ada.example@example.com") == [("ada.example@example.com", ada.pk, EmailBasis.HELD, handle.pk)]
    assert Party.objects.people_for_emails(["ada.example@example.com"]) == [
        EmailHolder("ada.example@example.com", Person._base_manager.get(pk=ada.pk), EmailBasis.HELD, handle)
    ]


def test_an_account_answers_its_own_person(owner: Any) -> None:
    grace = get_user_model().objects.create_user(username="grace-account", email="grace@example.com")
    person = Party.objects.for_user(grace)

    assert _answer("Grace@Example.com") == [("Grace@Example.com", person.pk, EmailBasis.ACCOUNT, None)]
    # The account's person also holding the address names them on both bases.
    handle = _holds(Party._base_manager.get(pk=person.pk), "grace@example.com", owner)
    assert _answer("grace@example.com") == [
        ("grace@example.com", person.pk, EmailBasis.ACCOUNT, handle.pk),
        ("grace@example.com", person.pk, EmailBasis.HELD, handle.pk),
    ]


def test_one_claimant_answers_a_held_by_nobody_address_and_two_answer_nobody(owner: Any) -> None:
    """A claim below the review threshold resolves no handle; its one claimant is the answer, two decide nothing."""

    ada, grace = _person(owner, "Ada Example"), _person(owner, "Grace Example")
    handle = _holds(ada, "family@example.com", owner, confidence=0.4)
    assert handle.party_id is None

    assert _answer("family@example.com") == [("family@example.com", ada.pk, EmailBasis.CLAIM, handle.pk)]
    _holds(grace, "family@example.com", owner, confidence=0.4)
    assert _answer("family@example.com") == []


def test_a_trashed_person_an_organization_and_a_merged_away_row_are_nobodys_answer(owner: Any) -> None:
    trashed = _person(owner, "Trashed Example")
    _holds(trashed, "trashed@example.com", owner)
    trashed.trash(reason="Removed by its holder")
    company = Organization.objects.create(display_name="Example Ltd", created_by=owner)
    _holds(company, "info@example.com", owner)
    retired, survivor = _person(owner, "Ada Old"), _person(owner, "Ada New")
    handle = _holds(retired, "ada@example.com", owner)
    Party._base_manager.filter(pk=retired.pk).update(merged_into=survivor)

    assert _answer("trashed@example.com", "info@example.com") == []
    # A merged-away holder answers through its survivor.
    assert _answer("ada@example.com") == [("ada@example.com", survivor.pk, EmailBasis.HELD, handle.pk)]


def test_the_answer_keeps_the_callers_preference_order_and_reads_any_batch_in_bounded_queries(owner: Any) -> None:
    people = [_person(owner, f"Person {index}") for index in range(5)]
    for index, person in enumerate(people):
        _holds(person, f"person{index}@example.com", owner)
    _holds(people[0], "weak@example.com", owner, confidence=0.4)

    assert [person for _email, person, _basis, _handle in _answer("person3@example.com", "person1@example.com")] == [
        people[3].pk,
        people[1].pk,
    ]
    with CaptureQueriesContext(connection) as one:
        Party.objects.people_for_emails(["person0@example.com"])
    with CaptureQueriesContext(connection) as many:
        answer = Party.objects.people_for_emails(
            [*(f"person{index}@example.com" for index in range(5)), "weak@example.com", "nobody@example.com"]
        )
    assert len(answer) == 6
    assert len(many.captured_queries) == len(one.captured_queries)
