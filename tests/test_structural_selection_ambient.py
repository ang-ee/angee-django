"""Structural projection keeps filters while making its policy bypass explicit."""

import pytest
from rebac import actor_context, system_context

from tests.test_integrate_oauth import Credential, make_integration


@pytest.mark.usefixtures("composed_tables")
def test_ambient_actor_is_dropped_by_admitted_structural_selection():
    with system_context(reason="test.structural.seed"):
        first = make_integration("structural-first", kind="oauth")
        second = make_integration("structural-second", kind="oauth")
    with actor_context(first.owner):
        scoped = set(Credential.objects.values_list("pk", flat=True))
        structural = set(Credential.objects.all()._structural_selection_of_admitted().values_list("pk", flat=True))
        pinned = set(Credential.objects.with_actor(first.owner)._structural_selection_of_admitted().values_list(
            "pk", flat=True,
        ))
    assert second.credential_id not in scoped
    assert second.credential_id in structural
    assert pinned == scoped
