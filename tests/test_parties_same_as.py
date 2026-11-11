"""Party same_as detection and the merge it leads to."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party, Person


def _same_as_pair(owner: Any) -> set[Any]:
    """Create two parties of ``owner`` that share one normalized phone handle."""

    with system_context(reason="same_as fixture"):
        left = Party._base_manager.create(display_name="Alex Example", created_by_id=owner.pk)
        right = Party._base_manager.create(display_name="A. Example", created_by_id=owner.pk)
        for party, value in ((left, "+420777123456"), (right, "+420 777 123 456")):
            Handle._base_manager.create(platform="phone", value=value, party=party, created_by_id=owner.pk)
        Party.objects.reconcile_same_as()
    return {left.pk, right.pk}


def test_same_as_pairs_scope_handles_to_the_pinned_actor(composed_tables: None) -> None:
    """A queryset pinned to an actor finds that actor's same_as pairs without an ambient actor."""

    del composed_tables
    owner = get_user_model().objects.create_user(username="same-as-owner")
    pair = _same_as_pair(owner)

    candidates = Party.objects.with_actor(owner).same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]


def test_same_as_pairs_run_under_system_context_without_an_actor(composed_tables: None) -> None:
    """A system scan with no actor at all still finds the pair."""

    del composed_tables
    pair = _same_as_pair(get_user_model().objects.create_user(username="same-as-system-owner"))

    with system_context(reason="same_as scan"):
        candidates = Party.objects.same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]


def test_merge_scalar_fields_admit_person_choices_only_between_people(composed_tables: None) -> None:
    """Merge validation and its GraphQL metadata share the row's declared scalar choices."""

    del composed_tables
    with system_context(reason="test.parties.merge_fields"):
        party = Party.objects.create(display_name="Plain")
        person = Person.objects.create(display_name="Person")
        assert party.merge_scalar_fields == ("display_name", "notes", "first_met_note")
        assert set(party.merge_scalar_fields) < set(person.merge_scalar_fields)
        assert "birthday" in person.merge_scalar_fields
        with pytest.raises(ValidationError, match="Unsupported merge field"):
            party.keep_merge_fields(person, {"birthday": "2000-01-01"})


@pytest.mark.parametrize(
    ("field_overrides", "kept"),
    [({}, ("Mergeprobe Gamma", "Gamma")), ({"display_name": "Mergeprobe Delta"}, ("Mergeprobe Delta", "Gamma"))],
    ids=("survivor-values", "override"),
)
def test_a_merge_keeps_the_chosen_values_over_the_newer_statements_it_moves(
    db: None, field_overrides: dict[str, str], kept: tuple[str, str]
) -> None:
    """The duplicate's newer statements move to the survivor; survivorship still keeps what the merge chose."""

    del db
    owner = get_user_model().objects.create_user(username="merge-values-owner")
    with actor_context(owner):
        gamma, delta = [
            Person.objects.create(display_name=f"Mergeprobe {name}", given_name="Mergeprobe", family_name=name)
            for name in ("Gamma", "Delta")
        ]
        Party.objects.get(pk=gamma.pk).merge(records=[delta.public_id], field_overrides=field_overrides)
        survivor = Person.objects.get(pk=gamma.pk)
        merged = (survivor.display_name, survivor.family_name)
        for statements in survivor.statement_managers():
            statements.resolve()
        survivor.refresh_from_db()
    assert merged == (survivor.display_name, survivor.family_name) == kept
