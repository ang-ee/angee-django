"""Held permissions are answered only on rows the actor can read."""

from __future__ import annotations

import pytest
from rebac import actor_context, system_context

from angee.graphql.capabilities import held_permissions
from tests.conftest import create_user
from tests.scopedemo.models import ProposalTarget

pytestmark = pytest.mark.django_db(transaction=True)

NAMES = ("create", "read", "write")


def test_held_permissions_require_the_rows_read(composed_tables: None) -> None:
    """``create`` is held by every signed-in actor, yet never on a row the actor cannot read."""

    owner = create_user("held-contract-owner")
    outsider = create_user("held-contract-outsider")
    with system_context(reason="tests.held_contract.setup"):
        target = ProposalTarget.objects.create(name="Hidden", owner=owner)
        row = ProposalTarget.objects.get(pk=target.pk)
    assert not ProposalTarget.objects.with_actor(outsider).filter(pk=target.pk).exists()

    with actor_context(outsider):
        assert held_permissions(row, NAMES) == frozenset()
    with actor_context(owner):
        assert held_permissions(row, NAMES) == frozenset(NAMES)
