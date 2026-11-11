"""Held permissions are answered only on rows the actor can read."""

from __future__ import annotations

from rebac import actor_context, system_context

from angee.graphql.capabilities import held_permissions, permission_expression
from tests.conftest import create_user
from tests.scopedemo.models import ProposalTarget

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


def test_permission_expression_holds_nothing_without_an_actor(composed_tables: None) -> None:
    """A queryset with neither a bound nor an ambient actor projects ``False`` on every row."""

    owner = create_user("expression-owner")
    with system_context(reason="tests.held_contract.expression"):
        target = ProposalTarget.objects.create(name="Owned", owner=owner)
    unbound = permission_expression(ProposalTarget.objects.all(), "write")
    bound = permission_expression(ProposalTarget.objects.with_actor(owner), "write")
    with system_context(reason="tests.held_contract.expression"):
        rows = ProposalTarget.objects.filter(pk=target.pk)
        assert list(rows.annotate(_held=unbound).values_list("_held", flat=True)) == [False]
    with actor_context(owner):
        rows = ProposalTarget.objects.filter(pk=target.pk)
        assert list(rows.annotate(_held=bound).values_list("_held", flat=True)) == [True]


def test_held_permissions_inside_system_context_are_the_actors_own(composed_tables: None) -> None:
    """Ambient elevation neither grants a capability nor hides the ambient actor's own."""

    owner = create_user("held-elevated-owner")
    outsider = create_user("held-elevated-outsider")
    with system_context(reason="tests.held_contract.setup"):
        target = ProposalTarget.objects.create(name="Owned", owner=owner)
        row = ProposalTarget.objects.get(pk=target.pk)
    with system_context(reason="tests.held_contract.elevated"):
        assert held_permissions(row, NAMES) == frozenset()
        with actor_context(owner):
            assert held_permissions(row, NAMES) == frozenset(NAMES)
        with actor_context(outsider):
            assert held_permissions(row, NAMES) == frozenset()
