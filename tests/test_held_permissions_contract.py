"""held_permissions answers only for rows the actor can read (lane P5 contract)."""

from __future__ import annotations

import pytest
from rebac import actor_context, system_context
from rebac.models import PermissionAuditEvent

from angee.iam.graphql.capabilities import held_permissions
from tests.conftest import create_user

pytestmark = pytest.mark.django_db(transaction=True)

NAMES = ("create", "read", "write")


def test_held_permissions_require_the_read(composed_tables) -> None:
    """An actor who cannot read the row holds no permissions on it, however the actor is bound."""
    from tests.scopedemo.models import ProposalTarget

    owner = create_user("held-contract-owner")
    outsider = create_user("held-contract-outsider")
    with system_context(reason="tests.held_contract.setup"):
        target = ProposalTarget.objects.create(name="Hidden", owner=owner)
    assert not ProposalTarget.objects.with_actor(outsider).filter(pk=target.pk).exists()
    last_audit = PermissionAuditEvent.objects.order_by("-pk").values_list("pk", flat=True).first() or 0

    with actor_context(outsider):
        row = ProposalTarget._base_manager.get(pk=target.pk)
        assert held_permissions(row, NAMES) == set()
        with system_context(reason="tests.held_contract.ambient_sudo"):
            assert held_permissions(row, NAMES) == set()
    row = ProposalTarget._base_manager.get(pk=target.pk)
    row._rebac_actor = ProposalTarget.objects.with_actor(outsider)._rebac_actor
    assert held_permissions(row, NAMES) == set()

    with actor_context(owner):
        row = ProposalTarget._base_manager.get(pk=target.pk)
        assert held_permissions(row, NAMES) == set(NAMES)

    # The ambient-sudo block logs its own bypass; the parent wrote a
    # graphql.permissions audit on every unreadable-row answer.
    reasons = set(PermissionAuditEvent.objects.filter(pk__gt=last_audit).values_list("reason", flat=True))
    assert reasons <= {"tests.held_contract.ambient_sudo"}, reasons
