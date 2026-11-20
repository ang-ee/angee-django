"""A system claim through a parties rule source, under each REBAC relationship storage.

A rule source writes a party through its membership of
``provenance/role:parties_statement_source``, a stored grant the install tier
writes into the session baseline. Each case switches storage through
``relationship_storage``, which carries that baseline into the requested one.
"""

from __future__ import annotations

from typing import Any

import pytest
from rebac import system_context

from angee.base.testing.fixtures import relationship_storage
from angee.messaging.testing.models import Handle, Party, Person
from tests.conftest import create_user


def _claimant(name: str, *, held: bool) -> tuple[Any, Any]:
    """A person and an address, which another person holds by a rule when ``held``."""

    owner = create_user(name)
    with system_context(reason="tests.parties_claim_storage.seed"):
        person = Person._base_manager.create(display_name="Ada Example", created_by=owner)
        handle = Handle.objects.create(platform="email", value=f"{name}@example.com", created_by=owner)
        if held:
            holder = Person._base_manager.create(display_name="Ada Holder", created_by=owner)
            Party.objects.assert_handle(
                Party._base_manager.get(pk=holder.pk),
                handle,
                asserted_by=Party.objects.rule_source("source_sender"),
                confidence=1.0,
            )
    return person, handle


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
@pytest.mark.parametrize("held", [False, True], ids=["nobody-holds-the-address", "another-person-holds-it"])
def test_a_system_claim_through_a_rule_source_is_admitted_in_either_storage(db: None, storage: str, held: bool) -> None:
    """The baseline's grant admits the rule source in either storage, on an address held or not."""

    del db
    with relationship_storage(storage):
        person, handle = _claimant(f"claim-{storage}-{held}", held=held)
        claim = Party.objects.propose_claimed_handle_as_system(
            person,
            handle,
            evidence=handle,
            reason="tests.parties_claim_storage.claim",
        )

    assert claim.subject_id == person.pk and claim.confidence == pytest.approx(0.4)
