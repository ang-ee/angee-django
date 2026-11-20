"""A system claim through a parties rule source, under each REBAC relationship storage.

The framework's own hosts store relationships in the registry shape
(``REBAC_LOCAL_BACKEND_STORAGE = "registry"``); a host may keep the library's
default, the denormalized shape. A rule source writes a party through its
membership of ``provenance/role:parties_statement_source``, a stored grant the
install tier writes, so each storage must hold that grant: a host in that mode
writes it there when it loads the tier, and a host that switches modes copies its
relationships first (``rebac migrate-storage``). Switching the setting alone leaves
every grant written before in the other storage's table.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from rebac import PermissionDenied, system_context

from angee.messaging.testing.models import Handle, Party, Person
from angee.resources.testing.models import Resource
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


def _claim(person: Any, handle: Any) -> Any:
    return Party.objects.propose_claimed_handle_as_system(
        person,
        handle,
        evidence=handle,
        reason="tests.parties_claim_storage.claim",
    )


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
@pytest.mark.parametrize("held", [False, True], ids=["nobody-holds-the-address", "another-person-holds-it"])
def test_a_system_claim_through_a_rule_source_is_admitted_in_either_storage(db: None, storage: str, held: bool) -> None:
    """The install tier's grant, written in the active storage, admits the rule source on either side."""

    del db
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        Resource.objects.load_addons((apps.get_app_config("parties"),), tiers=[Resource.Tier.INSTALL])
        person, handle = _claimant(f"claim-{storage}-{held}", held=held)
        claim = _claim(person, handle)

    assert claim.subject_id == person.pk and claim.confidence == pytest.approx(0.4)


def test_a_storage_switched_without_its_relationships_refuses_the_rule_source_until_they_are_copied(
    db: None,
) -> None:
    """The setting names which table is read; the grants this host wrote at setup are in its configured one."""

    del db
    other = "denormalized" if settings.REBAC_LOCAL_BACKEND_STORAGE == "registry" else "registry"
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=other):
        person, handle = _claimant("claim-switched", held=False)
        with pytest.raises(PermissionDenied, match="The asserting source requires write on the subject."):
            _claim(person, handle)

    call_command("rebac", "migrate-storage", "--to", other, verbosity=0)
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=other):
        claim = _claim(person, handle)

    assert claim.subject_id == person.pk
