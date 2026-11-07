"""Party same_as detection."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party


def _same_as_pair(owner: Any) -> set[Any]:
    """Create two parties of ``owner`` that share one normalized phone handle."""

    with system_context(reason="same_as fixture"):
        left = Party._base_manager.create(display_name="Alex Example", created_by_id=owner.pk)
        right = Party._base_manager.create(display_name="A. Example", created_by_id=owner.pk)
        for party, value in ((left, "+420777123456"), (right, "+420 777 123 456")):
            Handle._base_manager.create(platform="phone", value=value, party=party, created_by_id=owner.pk)
        Party.objects.reconcile_same_as()
    return {left.pk, right.pk}


@pytest.mark.django_db(transaction=True)
def test_same_as_pairs_scope_handles_to_the_pinned_actor(composed_tables: None) -> None:
    """A queryset pinned to an actor finds that actor's same_as pairs without an ambient actor."""

    del composed_tables
    owner = get_user_model().objects.create_user(username="same-as-owner")
    pair = _same_as_pair(owner)

    candidates = Party.objects.with_actor(owner).same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]


@pytest.mark.django_db(transaction=True)
def test_same_as_pairs_run_under_system_context_without_an_actor(composed_tables: None) -> None:
    """A system scan with no actor at all still finds the pair."""

    del composed_tables
    pair = _same_as_pair(get_user_model().objects.create_user(username="same-as-system-owner"))

    with system_context(reason="same_as scan"):
        candidates = Party.objects.same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]
