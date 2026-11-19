"""Party same_as detection and the merge it leads to."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import actor_context, system_context, to_subject_ref

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.testing import capture_broadcasts
from angee.messaging.testing.models import Handle, Party, Person
from tests.conftest import create_user


def _same_as_pair(owner: Any) -> set[Any]:
    """Create two parties of ``owner`` that share one normalized phone handle."""

    with system_context(reason="same_as fixture"):
        left = Party._base_manager.create(display_name="Alex Example", created_by_id=owner.pk)
        right = Party._base_manager.create(display_name="A. Example", created_by_id=owner.pk)
        for party, value in ((left, "+420777123456"), (right, "+420 777 123 456")):
            Handle._base_manager.create(platform="phone", value=value, party=party, created_by_id=owner.pk)
        Party.objects.reconcile_same_as()
    return {left.pk, right.pk}


def test_same_as_pairs_scope_handles_to_the_pinned_actor(db: None) -> None:
    """A queryset pinned to an actor finds that actor's same_as pairs without an ambient actor."""

    del db
    owner = get_user_model().objects.create_user(username="same-as-owner")
    pair = _same_as_pair(owner)

    candidates = Party.objects.with_actor(owner).same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]


def test_same_as_pairs_run_under_system_context_without_an_actor(db: None) -> None:
    """A system scan with no actor at all still finds the pair."""

    del db
    pair = _same_as_pair(get_user_model().objects.create_user(username="same-as-system-owner"))

    with system_context(reason="same_as scan"):
        candidates = Party.objects.same_as_pairs()

    assert [{candidate.left.pk, candidate.right.pk} for candidate in candidates] == [pair]


def test_merge_scalar_fields_admit_person_choices_only_between_people(db: None) -> None:
    """Merge validation and its GraphQL metadata share the row's declared scalar choices."""

    del db
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


def test_reverting_a_merge_publishes_the_survivors_restored_values_on_its_record_feed(
    db: None, django_capture_on_commit_callbacks: Any,
) -> None:
    """A person's page reads the Party feed live; the revert's Person restore reaches it like an ordinary edit."""

    del db
    owner = get_user_model().objects.create_user(username="merge-revert-feed-owner")
    with actor_context(owner):
        gamma, delta = [Person.objects.create(display_name=f"Feedprobe {name}") for name in ("Gamma", "Delta")]
        with django_capture_on_commit_callbacks(execute=True):  # the merge's changeset closes on commit
            Party.objects.get(pk=gamma.pk).merge(records=[delta.public_id], field_overrides={"display_name": "Merged"})
        boundary = Party.history.filter(id=delta.pk).order_by("-history_id").first().changeset
        with capture_broadcasts() as sent, django_capture_on_commit_callbacks(execute=True):
            boundary.with_actor(owner).revert()
    assert Person._base_manager.get(pk=gamma.pk).display_name == "Feedprobe Gamma"
    assert any(
        (event["model"], event["id"], event["action"]) == ("parties.Party", gamma.public_id, "update")
        and "display_name" in event["changed_fields"]
        for event in sent
    ), sent


def _address(owner: Any, value: str = "ada@example.com", **fields: Any) -> Any:
    """One email address row, with no holder yet."""

    with system_context(reason="same_as address"):
        return Handle._base_manager.create(platform="email", value=value, created_by=owner, **fields)


def _holder(owner: Any, handle: Any, name: str = "Ada Example") -> Any:
    """A party whose own statement of ``handle`` decides it: the address's holder."""

    with system_context(reason="same_as holder"):
        party = Party._base_manager.create(display_name=name, created_by=owner)
        Party.objects.assert_handle(party, handle, asserted_by=owner)
    return party


def _claimant(owner: Any, handle: Any, name: str = "A. Example") -> Any:
    """Another source's party, which claims ``handle`` below review confidence as an importer does."""

    with system_context(reason="same_as claimant"):
        party = Party._base_manager.create(display_name=name, created_by=owner)
        Party.objects.propose_claimed_handle_as_system(party, handle, evidence=handle, reason="tests.parties.claim")
    return party


def _pairs() -> list[tuple[set[Any], tuple[Any, ...]]]:
    """Each pair awaiting review, as its two parties and the handles it cites."""

    with system_context(reason="same_as review"):
        return [({pair.left.pk, pair.right.pk}, pair.handles) for pair in Party.objects.same_as_pairs()]


def _reconcile() -> int:
    with system_context(reason="same_as reconcile"):
        return Party.objects.reconcile_same_as()


def test_a_holder_and_a_claimant_of_one_address_are_paired(db: None) -> None:
    """One address row: its holder and a party a source states it for are proposed as one person."""

    del db
    owner = create_user("same-as-holder-claimant")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    assert (_reconcile(), _reconcile()) == (1, 0)
    assert _pairs() == [({holder.pk, claimant.pk}, (handle,))]


def test_two_claimants_of_one_address_nobody_holds_are_paired(db: None) -> None:
    del db
    owner = create_user("same-as-two-claimants")
    handle = _address(owner)
    first, second = _claimant(owner, handle, "Ada Example"), _claimant(owner, handle, "A. Example")

    assert _reconcile() == 1
    assert _pairs() == [({first.pk, second.pk}, (handle,))]


def test_links_parties_infer_from_a_shared_name_pair_nobody(db: None) -> None:
    """Two same-named people suggested for one unresolved handle are not proposed as one person."""

    del db
    owner = create_user("same-as-inferred")
    with system_context(reason="same_as inferred links"):
        phone = Handle._base_manager.create(
            platform="phone", value="+442079460958", display_name="Ada Lovelace", created_by=owner,
        )
    for value in ("ada@example.com", "lovelace@example.org"):
        _holder(owner, _address(owner, value, display_name="Ada Lovelace"), name="Ada Lovelace")
    with system_context(reason="same_as inferred links"):
        assert Party.objects.reconcile_suggestions() == 2
        assert Party.statements.model._base_manager.filter(handle=phone, confidence=0.4).count() == 2

    assert _reconcile() == 0
    assert _pairs() == []


def test_a_link_a_person_dismissed_pairs_nobody(db: None) -> None:
    del db
    owner = create_user("same-as-dismissed-link")
    handle = _address(owner)
    _holder(owner, handle)
    claimant = _claimant(owner, handle)
    claim = Party.statements.model._base_manager.get(subject=claimant, handle=handle)
    claim.with_actor(owner).dismiss()

    assert _reconcile() == 0
    assert _pairs() == []


def test_a_pair_kept_separate_is_not_proposed_again(db: None) -> None:
    del db
    owner = create_user("same-as-kept-separate")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    assert _reconcile() == 1

    Party.objects.apply_same_as_pair(
        left_id=holder.public_id, right_id=claimant.public_id, survivor="left", action="keep_separate", actor=owner,
    )

    assert _reconcile() == 0
    assert _pairs() == []
    with system_context(reason="same_as readback"):
        assert Party.same_as.kept_separate(holder, claimant)


def test_withdrawing_the_claim_withdraws_the_pair(db: None) -> None:
    del db
    owner = create_user("same-as-withdrawn-claim")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    assert _reconcile() == 1

    with system_context(reason="the source drops the address"):
        Party.statements.withdraw(Party.objects.rule_source("source_sender"), [claimant])

    assert _reconcile() == 1
    assert _pairs() == []
    with system_context(reason="same_as readback"):
        pair = Party.same_as.model._base_manager.get(left_id=min(holder.pk, claimant.pk))
        assert [row.status for row in pair.statements.all()] == ["withdrawn"]


def _rule_pair_statements(left: Any, right: Any) -> list[Any]:
    """The ``parties.same_as`` rule's statements on the ordered pair of ``left`` and ``right``."""

    rule = str(to_subject_ref(Party.objects.rule_source("same_as")))
    first, second = sorted((left.pk, right.pk))
    with system_context(reason="same_as readback"):
        return list(
            Party.same_as.model.statements.model._base_manager.filter(
                subject__left_id=first, subject__right_id=second, asserted_by=rule,
            ).order_by("pk")
        )


def test_an_importer_proposes_two_parties_as_one_person_as_the_job_does(db: None) -> None:
    """The verb names no caller: the rule's pair, citing the handle, which the job keeps and never merges."""

    del db
    owner = create_user("same-person-verb")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    statement = Party.objects.propose_same_person(claimant, holder, evidence=[handle])

    assert statement is not None
    assert (statement.rank, statement.snak, statement.source_kind, statement.status) == (
        "normal", "value", "rule", "asserted",
    )
    assert [ref["id"] for ref in statement.evidence] == [handle.public_id]
    assert _pairs() == [({holder.pk, claimant.pk}, (handle,))]
    assert Party.objects.propose_same_person(holder, claimant, evidence=[handle]) is None
    assert _reconcile() == 0
    assert [row.pk for row in _rule_pair_statements(holder, claimant)] == [statement.pk]
    with system_context(reason="same_as readback"):
        assert set(Party._base_manager.filter(merged_into__isnull=True).values_list("pk", flat=True)) >= {
            holder.pk, claimant.pk,
        }


def test_the_job_withdraws_a_proposal_whose_link_is_gone(db: None) -> None:
    del db
    owner = create_user("same-person-verb-withdrawn")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    Party.objects.propose_same_person(claimant, holder, evidence=[handle])

    with system_context(reason="the source drops the address"):
        Party.statements.withdraw(Party.objects.rule_source("source_sender"), [claimant])

    assert _reconcile() == 1
    assert [row.status for row in _rule_pair_statements(holder, claimant)] == ["withdrawn"]


def _kept_separate(owner: Any, left: Any, right: Any) -> None:
    Party.objects.apply_same_as_pair(
        left_id=left.public_id, right_id=right.public_id, survivor="left", action="keep_separate", actor=owner,
    )


def _confirmed(owner: Any, left: Any, right: Any) -> None:
    with actor_context(owner):
        Party.same_as.for_pair(left.with_actor(owner), right).with_actor(owner).confirm_statement(
            aspect="same_as", values={},
        )


def _trashed(owner: Any, left: Any, right: Any) -> None:
    del owner, left
    with system_context(reason="same_as trashed endpoint"):
        Party._base_manager.get(pk=right.pk).trash(reason="test")


def _merged(owner: Any, left: Any, right: Any) -> None:
    with actor_context(owner):
        Party.objects.get(pk=left.pk).merge(records=[right.public_id])


@pytest.mark.parametrize(
    "decided",
    [
        pytest.param(_kept_separate, id="kept-separate"),
        pytest.param(_confirmed, id="a-current-assertion"),
        pytest.param(_trashed, id="trashed"),
        pytest.param(_merged, id="merged-away"),
    ],
)
def test_a_proposal_does_nothing_on_a_decided_or_retired_pair(db: None, decided: Any) -> None:
    del db
    owner = create_user("same-person-verb-decided")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    decided(owner, holder, claimant)

    assert Party.objects.propose_same_person(holder, claimant, evidence=[handle]) is None
    assert _rule_pair_statements(holder, claimant) == []


def test_a_party_is_never_proposed_as_its_own_same_person(db: None) -> None:
    del db
    owner = create_user("same-person-verb-self")
    handle = _address(owner)
    holder = _holder(owner, handle)

    assert Party.objects.propose_same_person(holder, holder, evidence=[handle]) is None


@pytest.mark.parametrize("evidence", [pytest.param("none", id="empty"), pytest.param("party", id="not-a-handle")])
def test_a_proposal_cites_the_handles_that_link_the_two(db: None, evidence: str) -> None:
    del db
    owner = create_user("same-person-verb-evidence")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with pytest.raises(ValidationError, match="handle"):
        Party.objects.propose_same_person(holder, claimant, evidence={"none": [], "party": [holder]}[evidence])
    assert _rule_pair_statements(holder, claimant) == []


def test_a_proposal_joins_the_callers_transaction(db: None) -> None:
    del db
    owner = create_user("same-person-verb-transaction")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with transaction.atomic():
        assert Party.objects.propose_same_person(holder, claimant, evidence=[handle]) is not None
        transaction.set_rollback(True)

    assert _rule_pair_statements(holder, claimant) == []
