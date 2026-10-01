"""Party duplicate detection."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from tests.test_messaging import Handle, Party


@pytest.mark.django_db(transaction=True)
def test_duplicate_candidates_scope_handles_to_the_pinned_actor(composed_tables: None) -> None:
    """A queryset pinned to an actor finds that actor's duplicates without an ambient actor."""

    del composed_tables
    owner = get_user_model().objects.create_user(username="duplicate-owner")
    with system_context(reason="duplicate fixture"):
        left = Party._base_manager.create(display_name="Alex Example", created_by_id=owner.pk)
        right = Party._base_manager.create(display_name="A. Example", created_by_id=owner.pk)
        for party, value in ((left, "+420777123456"), (right, "+420 777 123 456")):
            Handle._base_manager.create(platform="phone", value=value, party=party, created_by_id=owner.pk)

    candidates = Party.objects.with_actor(owner).duplicate_candidates()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [{left.pk, right.pk}]
