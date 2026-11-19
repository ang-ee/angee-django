"""Party same_as detection and the merge it leads to."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, system_context, to_subject_ref

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.testing import capture_broadcasts
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.backends import ManualChannelBackend, ParsedHandle
from angee.messaging.testing.models import Channel, Handle, Organization, Party, Person, RelationshipKind
from angee.parties.connections import ParsedConnection
from angee.provenance.statements import SourceKind
from tests.conftest import create_user, current_stream, make_integration


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


def _address(owner: Any, value: str = "ada@example.com", *, platform: str = "email", **fields: Any) -> Any:
    """One address row (an email address unless ``platform`` says otherwise), with no holder yet."""

    with system_context(reason="same_as address"):
        return Handle._base_manager.create(platform=platform, value=value, created_by=owner, **fields)


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


def test_two_people_an_export_records_by_name_alone_are_not_paired(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two users' friends of one name share a name-only handle; a name identifies no account."""

    del db
    monkeypatch.setattr(ManualChannelBackend, "source_kind", SourceKind.IMPORT)
    friend = ParsedConnection(handle=ParsedHandle(platform="facebook", value="Ada Example", display_name="Ada Example"))
    with system_context(reason="same_as name-only friends"):
        for slug in ("acquaintance", "employee"):
            RelationshipKind.objects.get_or_create(slug=slug, defaults={"name": slug.title()})
        for name in ("first-exporter", "second-exporter"):
            user = create_user(name)
            channel = make_integration(name, model=Channel, backend_class="manual")
            stream = current_stream(channel, "connections", kind=StreamKind.RECORD_REPLICA)
            Party.objects.ingest_connections(
                [(friend, RecordLink.objects.observe(stream, "1"))],
                created_by_id=user.pk,
                asserted_by=channel.integration_ptr,
            )
        handle = Handle._base_manager.get(platform="facebook", value="Ada Example")
        assert Party.statements.model._base_manager.filter(handle=handle, confidence=0.4).count() == 2

    assert _reconcile() == 0
    assert _pairs() == []


def test_a_rule_the_link_allowlist_does_not_name_links_nobody(db: None) -> None:
    """Only a source stating its own record links; any other rule's statement of the address is a guess."""

    del db
    owner = create_user("same-as-unnamed-rule")
    handle = _address(owner)
    _holder(owner, handle)
    with system_context(reason="same_as unnamed rule"):
        guessed = Party._base_manager.create(display_name="A. Example", created_by=owner)
        Party.objects.assert_handle(guessed, handle, asserted_by=Party.objects.rule_source("same_as"), confidence=0.4)

    assert _reconcile() == 0
    assert _pairs() == []


def _person(owner: Any, name: str, *, user: Any = None) -> Any:
    with system_context(reason="same_as person"):
        if user is not None:
            return Person.objects.for_user(user)
        return Person._base_manager.create(display_name=name, created_by=owner)


def test_a_person_and_an_organization_are_never_paired(db: None) -> None:
    del db
    owner = create_user("same-as-person-organization")
    handle = _address(owner, "office@example.com")
    with system_context(reason="same_as organization"):
        person = _person(owner, "Ada Example")
        Party.objects.assert_handle(person, handle, asserted_by=owner)
        organization = Organization._base_manager.create(display_name="Example Ltd", created_by=owner)
        Party.objects.propose_claimed_handle_as_system(organization, handle, evidence=handle, reason="tests.claim")

    assert _reconcile() == 0
    assert _pairs() == []


def test_two_account_persons_are_never_paired(db: None) -> None:
    """Each account's own person is that account's identity: two of them are never one person."""

    del db
    ada, bob = create_user("ada-account"), create_user("bob-account")
    handle = _address(ada, "family@example.com")
    first, second = (_person(user, "", user=user) for user in (ada, bob))
    with system_context(reason="same_as account persons"):
        Party.objects.assert_handle(first, handle, asserted_by=ada)
        Party.objects.propose_claimed_handle_as_system(second, handle, evidence=handle, reason="tests.claim")

    assert _reconcile() == 0
    assert _pairs() == []


def test_a_contested_control_claim_links_nobody(db: None) -> None:
    """An account claiming an address another account controls records a claim that pairs it with no one."""

    del db
    controller, contender = create_user("same-as-controller"), create_user("same-as-contender")
    Handle.objects.claim_control(controller, platform="email", value="shared@example.com")
    Handle.objects.claim_control(contender, platform="email", value="shared@example.com")
    with system_context(reason="same_as contested claim"):
        handle = Handle._base_manager.get(value="shared@example.com")
        contact = _person(controller, "Ada Example")
        Party.objects.propose_claimed_handle_as_system(contact, handle, evidence=handle, reason="tests.claim")
        holder = Person.objects.for_user(controller)

    assert _reconcile() == 1
    assert _pairs() == [({holder.pk, contact.pk}, (handle,))]


@pytest.mark.parametrize(
    ("linked", "proposed"),
    [pytest.param(4, 6, id="four-parties-pair-every-two"), pytest.param(5, 0, id="a-fifth-party-makes-it-shared")],
)
def test_an_address_more_parties_than_the_limit_are_linked_to_is_shared(
    db: None, linked: int, proposed: int,
) -> None:
    """A switchboard on many cards gives no pairs at all, not one per two of them."""

    del db
    assert Party.objects.SHARED_ADDRESS_LIMIT == 4
    owner = create_user("same-as-shared-address")
    handle = _address(owner, "switchboard@example.com")
    _holder(owner, handle)
    for index in range(linked - 1):
        _claimant(owner, handle, f"Caller {index}")

    assert _reconcile() == proposed
    assert len(_pairs()) == proposed


def test_pairs_are_withdrawn_once_their_address_becomes_shared(db: None) -> None:
    del db
    owner = create_user("same-as-becomes-shared")
    handle = _address(owner, "switchboard@example.com")
    _holder(owner, handle)
    _claimant(owner, handle)
    assert _reconcile() == 1

    for index in range(3):
        _claimant(owner, handle, f"Caller {index}")

    assert _reconcile() == 1
    assert _pairs() == []


def test_two_parties_one_source_links_to_an_address_are_not_paired(db: None) -> None:
    """A source knows its own records are distinct: two cards of one address book sharing a number."""

    del db
    owner = create_user("same-as-one-source")
    with system_context(reason="same_as one source"):
        for name, value in (("Ada Example", "+44 20 7946 0958"), ("Bob Example", "+442079460958")):
            card = Party._base_manager.create(display_name=name, created_by=owner)
            phone = Handle._base_manager.create(platform="phone", value=value, created_by=owner)
            Party.objects.assert_handle(card, phone, asserted_by=owner)

    assert _reconcile() == 0
    assert _pairs() == []


def test_a_holding_is_the_source_of_the_statement_that_decides_it(db: None) -> None:
    """The source deciding a holder and stating the other party is one source; a claim on either is a second."""

    del db
    owner = create_user("same-as-holding-source")
    handle = _address(owner)
    holder = _holder(owner, handle)
    with system_context(reason="same_as one source below review"):
        other = Party._base_manager.create(display_name="A. Example", created_by=owner)
        Party.objects.assert_handle(other, handle, asserted_by=owner, confidence=0.4)
    assert _reconcile() == 0

    with system_context(reason="same_as a second source"):
        Party.objects.propose_claimed_handle_as_system(holder, handle, evidence=handle, reason="tests.claim")

    assert _reconcile() == 1
    assert _pairs() == [({holder.pk, other.pk}, (handle,))]


def shared_senders() -> list[Any]:
    """A shared-sender provider naming every ``noreply@`` address, as messaging names list senders."""

    return list(Handle._base_manager.filter(value__startswith="noreply@").values_list("pk", flat=True))


def test_an_address_the_shared_sender_providers_name_links_nobody(db: None) -> None:
    del db
    owner = create_user("same-as-shared-sender")
    handle = _address(owner, "noreply@example.com")
    _holder(owner, handle)
    _claimant(owner, handle)

    with override_settings(ANGEE_PARTIES_SHARED_SENDER_PROVIDERS=[f"{__name__}.shared_senders"]):
        assert _reconcile() == 0
    assert _pairs() == []


def test_the_review_list_fills_its_page_with_pairs_the_reader_can_read(db: None) -> None:
    """A pair whose evidence the reader cannot read is dropped before the page is cut, not after."""

    del db
    reader, other = create_user("same-as-review-reader"), create_user("same-as-review-other")
    readable = []
    for handle, visible in ((_address(other, "hidden@example.com"), False), (_address(reader), True)):
        pair = {_holder(reader, handle).pk, _claimant(reader, handle).pk}
        if visible:
            readable.append(pair)
    assert _reconcile() == 2

    page = Party.objects.with_actor(reader).same_as_pairs(limit=1)

    assert [{pair.left.pk, pair.right.pk} for pair in page] == readable


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


def _propose(capture: Any, left: Any, right: Any, evidence: list[Any], *, reason: str = "tests.importer.page") -> Any:
    """An importer's committed page proposing ``left`` and ``right``; return the rule's pair statements after commit."""

    with capture(execute=True):
        Party.objects.propose_same_person_as_system(left, right, evidence=evidence, reason=reason)
    return _rule_pair_statements(left, right)


def test_an_importer_proposes_two_parties_as_one_person_as_the_job_does(
    db: None, django_capture_on_commit_callbacks: Any,
) -> None:
    """The verb names no caller: the rule's pair, citing the handle, which the job keeps and never merges."""

    del db
    owner = create_user("same-person-verb")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    (statement,) = _propose(django_capture_on_commit_callbacks, claimant, holder, [handle])

    assert (statement.rank, statement.snak, statement.source_kind, statement.status) == (
        "normal", "value", "rule", "asserted",
    )
    assert [ref["id"] for ref in statement.evidence] == [handle.public_id]
    assert _pairs() == [({holder.pk, claimant.pk}, (handle,))]
    assert _propose(django_capture_on_commit_callbacks, holder, claimant, [handle]) == [statement]
    assert _reconcile() == 0
    assert [row.pk for row in _rule_pair_statements(holder, claimant)] == [statement.pk]
    with system_context(reason="same_as readback"):
        assert set(Party._base_manager.filter(merged_into__isnull=True).values_list("pk", flat=True)) >= {
            holder.pk, claimant.pk,
        }


def test_a_proposal_runs_nothing_inside_the_callers_transaction(
    db: None, django_capture_on_commit_callbacks: Any,
) -> None:
    """Inside the importer's page the verb only registers: no query, no lock; the pair lands after the commit."""

    del db
    owner = create_user("same-person-verb-deferred")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            with CaptureQueriesContext(connection) as page:
                Party.objects.propose_same_person_as_system(
                    claimant, holder, evidence=[handle], reason="tests.importer.page",
                )
            assert _rule_pair_statements(holder, claimant) == []

    assert page.captured_queries == []
    assert [row.status for row in _rule_pair_statements(holder, claimant)] == ["asserted"]


def test_a_rolled_back_caller_proposes_nothing(db: None, django_capture_on_commit_callbacks: Any) -> None:
    del db
    owner = create_user("same-person-verb-rollback")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with django_capture_on_commit_callbacks(execute=True) as committed:
        with transaction.atomic():
            Party.objects.propose_same_person_as_system(claimant, holder, evidence=[handle], reason="tests.importer")
            transaction.set_rollback(True)

    assert (committed, _rule_pair_statements(holder, claimant)) == ([], [])


def test_a_proposal_needs_a_named_reason(db: None) -> None:
    del db
    owner = create_user("same-person-verb-reason")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with pytest.raises(ValueError, match="named reason"):
        Party.objects.propose_same_person_as_system(holder, claimant, evidence=[handle], reason=" ")


def test_a_proposal_its_evidence_does_not_support_proposes_nothing(
    db: None, django_capture_on_commit_callbacks: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    """A cited handle links one of the two, and the two share a cited address, or nothing is proposed."""

    del db
    owner = create_user("same-person-verb-unsupported")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    unrelated = _address(owner, "someone-else@example.com")
    phone = _address(owner, "+442079460958", platform="phone")
    with system_context(reason="same_as the holder's own phone"):
        Party.objects.assert_handle(holder, phone, asserted_by=owner)

    with caplog.at_level("INFO", logger="angee.parties.managers"):
        assert _propose(django_capture_on_commit_callbacks, holder, claimant, [unrelated]) == []
        assert _propose(django_capture_on_commit_callbacks, holder, claimant, [phone]) == []
        assert _propose(django_capture_on_commit_callbacks, holder, claimant, [handle, unrelated]) == []

    assert [record.message for record in caplog.records].count(
        "A same-person proposal under tests.importer.page is not supported by its evidence."
    ) == 3
    assert _reconcile() == 1


def test_a_proposal_cites_the_survivor_of_a_merged_handle(db: None, django_capture_on_commit_callbacks: Any) -> None:
    del db
    owner = create_user("same-person-verb-merged-handle")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    alias = _address(owner, "Ada@Example.com")
    with system_context(reason="same_as a spelling folds into its address"):
        handle.fold([alias])

    (statement,) = _propose(django_capture_on_commit_callbacks, holder, claimant, [alias])

    assert [ref["id"] for ref in statement.evidence] == [handle.public_id]


def test_the_job_withdraws_a_proposal_whose_link_is_gone(db: None, django_capture_on_commit_callbacks: Any) -> None:
    del db
    owner = create_user("same-person-verb-withdrawn")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    _propose(django_capture_on_commit_callbacks, claimant, holder, [handle])

    with system_context(reason="the source drops the address"):
        Party.statements.withdraw(Party.objects.rule_source("source_sender"), [claimant])

    assert _reconcile() == 1
    assert [row.status for row in _rule_pair_statements(holder, claimant)] == ["withdrawn"]


def test_the_job_leaves_a_proposal_stated_after_its_run_began(
    db: None, django_capture_on_commit_callbacks: Any,
) -> None:
    """An importer's proposal committed while the job scans is the next run's to judge, never withdrawn on sight."""

    del db
    owner = create_user("same-person-verb-during-scan")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    (statement,) = _propose(django_capture_on_commit_callbacks, claimant, holder, [handle])
    with system_context(reason="the source drops the address"):
        Party.statements.withdraw(Party.objects.rule_source("source_sender"), [claimant])
    stances = Party.same_as.model.statements.model._base_manager.filter(pk=statement.pk)

    with system_context(reason="same_as a proposal stated after the run began"):
        stances.update(updated_at=timezone.now() + timedelta(minutes=5))
    assert _reconcile() == 0

    with system_context(reason="same_as a proposal stated before the run began"):
        stances.update(updated_at=timezone.now() - timedelta(minutes=5))
    assert _reconcile() == 1
    assert [row.status for row in _rule_pair_statements(holder, claimant)] == ["withdrawn"]


def test_a_proposal_citing_a_deleted_handle_is_restated_with_the_handles_that_link_the_two(db: None) -> None:
    """A deleted evidence row would hide the pair from every reader; the job cites what still links the two."""

    del db
    owner = create_user("same-person-deleted-evidence")
    with system_context(reason="same_as a holder written onto its row"):
        holder = Party._base_manager.create(display_name="Ada Example", created_by=owner)
        written = Handle._base_manager.create(platform="email", value="Ada@Example.com", party=holder, created_by=owner)
    stated = _address(owner, "ADA@example.com")
    with system_context(reason="same_as the holder's own statement"):
        Party.objects.assert_handle(holder, stated, asserted_by=owner)
    claimed = _address(owner)
    claimant = _claimant(owner, claimed)
    assert _reconcile() == 1
    with system_context(reason="same_as the written row goes"):
        Handle._base_manager.filter(pk=written.pk).delete()
    assert _pairs() == []

    assert _reconcile() == 1
    assert _pairs() == [({holder.pk, claimant.pk}, (stated, claimed))]


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
def test_a_proposal_does_nothing_on_a_decided_or_retired_pair(
    db: None, django_capture_on_commit_callbacks: Any, decided: Any,
) -> None:
    del db
    owner = create_user("same-person-verb-decided")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)
    decided(owner, holder, claimant)

    assert _propose(django_capture_on_commit_callbacks, holder, claimant, [handle]) == []


def test_a_party_is_never_proposed_as_its_own_same_person(db: None, django_capture_on_commit_callbacks: Any) -> None:
    del db
    owner = create_user("same-person-verb-self")
    handle = _address(owner)
    holder = _holder(owner, handle)

    with django_capture_on_commit_callbacks(execute=True) as committed:
        Party.objects.propose_same_person_as_system(holder, holder, evidence=[handle], reason="tests.importer")

    assert committed == []


@pytest.mark.parametrize("evidence", [pytest.param("none", id="empty"), pytest.param("party", id="not-a-handle")])
def test_a_proposal_cites_the_handles_that_link_the_two(db: None, evidence: str) -> None:
    del db
    owner = create_user("same-person-verb-evidence")
    handle = _address(owner)
    holder, claimant = _holder(owner, handle), _claimant(owner, handle)

    with pytest.raises(ValidationError, match="handle"):
        Party.objects.propose_same_person_as_system(
            holder, claimant, evidence={"none": [], "party": [holder]}[evidence], reason="tests.importer",
        )
    assert _rule_pair_statements(holder, claimant) == []


def _people(owner: Any, left: str, right: str, *, value: str = "zoe@example.com") -> tuple[Any, Any, Any]:
    """Two people one address links: the older states it, the newer's source claims it."""

    handle = _address(owner, value)
    with system_context(reason="same_as two people"):
        older = Person._base_manager.create(display_name=left, created_by=owner)
        Party.objects.assert_handle(older, handle, asserted_by=owner)
        newer = Person._base_manager.create(display_name=right, created_by=owner)
        Party.objects.propose_claimed_handle_as_system(newer, handle, evidence=handle, reason="tests.claim")
    return older, newer, handle


def _merge_certain(actor: Any, *, limit: int = 50) -> list[tuple[str, set[Any], Any]]:
    outcomes = Party.objects.merge_certain_same_as_pairs(actor=actor, limit=limit)
    return [
        (str(row.outcome), {row.left.pk, row.right.pk}, row.survivor.pk if row.survivor else None) for row in outcomes
    ]


def test_a_certain_pair_merges_into_the_older_record_as_the_acting_persons_merge(db: None) -> None:
    """The survivor keeps its spelling of the name, recorded as the acting person's choice, and holds the address."""

    del db
    owner = create_user("same-as-certain-owner")
    older, newer, handle = _people(owner, "Zoë Example-Smith", "zoe example smith")
    assert _reconcile() == 1

    assert _merge_certain(owner) == [("merged", {older.pk, newer.pk}, older.pk)]

    with system_context(reason="same_as certain readback"):
        assert Party._base_manager.get(pk=newer.pk).merged_into_id == older.pk
        assert Party._base_manager.get(pk=older.pk).display_name == "Zoë Example-Smith"
        kept = Party.statements.model._base_manager.get(subject_id=older.pk, aspect="identity", rank="preferred")
        assert (kept.asserted_by, kept.stated_value("display_name")) == (
            str(to_subject_ref(owner)), "Zoë Example-Smith",
        )
        assert set(Party.objects.handle_statements().filter(handle=handle).values_list("subject_id", flat=True)) == {
            older.pk,
        }
    assert _pairs() == []


def _not_people(owner: Any) -> tuple[Any, Any]:
    handle = _address(owner, "zoe@example.com")
    return _holder(owner, handle, "Zoë Example"), _claimant(owner, handle, "Zoe Example")


def _an_account_person(owner: Any) -> tuple[Any, Any]:
    handle = _address(owner, "zoe@example.com")
    with system_context(reason="same_as an account's person"):
        person = Person.objects.for_user(owner)
        Party.objects.assert_handle(person, handle, asserted_by=owner)
        contact = Person._base_manager.create(display_name=person.display_name, created_by=owner)
        Party.objects.propose_claimed_handle_as_system(contact, handle, evidence=handle, reason="tests.claim")
    return person, contact


def _a_third_party_linked(owner: Any) -> tuple[Any, Any]:
    older, newer, handle = _people(owner, "Zoë Example", "Zoe Example")
    with system_context(reason="same_as a third party"):
        third = Person._base_manager.create(display_name="Zoe Example", created_by=owner)
        Party.objects.propose_claimed_handle_as_system(third, handle, evidence=handle, reason="tests.claim")
    return older, newer


def _names_differ(owner: Any) -> tuple[Any, Any]:
    older, newer, _handle = _people(owner, "Zoë Example", "Zoe B. Example")
    return older, newer


def _a_name_missing(owner: Any) -> tuple[Any, Any]:
    older, newer, _handle = _people(owner, "Zoë Example", Party.PLACEHOLDER_NAME)
    return older, newer


@pytest.mark.parametrize(
    ("arrange", "outcome"),
    [
        pytest.param(_not_people, "not_people", id="not-both-people"),
        pytest.param(_an_account_person, "account_person", id="an-accounts-own-person"),
        pytest.param(_a_third_party_linked, "shared_address", id="a-third-party-on-the-address"),
        pytest.param(_names_differ, "names_differ", id="names-differ"),
        pytest.param(_a_name_missing, "names_differ", id="a-name-missing"),
    ],
)
def test_a_pair_that_is_not_certain_is_left_for_review_with_its_reason(db: None, arrange: Any, outcome: str) -> None:
    del db
    owner = create_user("same-as-uncertain")
    left, right = arrange(owner)
    _reconcile()
    pairs = {frozenset(pair) for pair, _handles in _pairs()}
    assert frozenset({left.pk, right.pk}) in pairs

    assert (outcome, {left.pk, right.pk}, None) in _merge_certain(owner)
    assert {frozenset(pair) for pair, _handles in _pairs()} == pairs


def test_a_person_the_merge_does_not_permit_leaves_the_pair(db: None) -> None:
    """Reading a pair is not merging it: the merge's own permission decides."""

    del db
    owner, reviewer = create_user("same-as-certain-writer"), create_user("same-as-certain-reader")
    older, newer, handle = _people(owner, "Zoë Example", "Zoe Example")
    assert _reconcile() == 1
    with actor_context(owner):
        for record in (*Party.objects.filter(pk__in=(older.pk, newer.pk)), Handle.objects.get(pk=handle.pk)):
            record.grant_record_access("reader", reviewer)

    assert _merge_certain(reviewer) == [("not_permitted", {older.pk, newer.pk}, None)]
    assert len(_pairs()) == 1


def refuse_merges(record: Any) -> str:
    """A merge guard an addon contributes to hold its records."""

    del record
    return "Held by a test addon."


def test_a_merge_the_guards_refuse_leaves_the_pair(db: None) -> None:
    del db
    owner = create_user("same-as-certain-guarded")
    older, newer, _handle = _people(owner, "Zoë Example", "Zoe Example")
    assert _reconcile() == 1

    with override_settings(ANGEE_MERGE_GUARDS=[f"{__name__}.refuse_merges"]):
        assert _merge_certain(owner) == [("refused", {older.pk, newer.pk}, None)]
    assert len(_pairs()) == 1


def test_one_call_merges_a_bounded_number_of_pairs(db: None) -> None:
    del db
    owner = create_user("same-as-certain-bounded")
    first = _people(owner, "Zoë Example", "Zoe Example", value="zoe@example.com")
    second = _people(owner, "Yan Example", "yan example", value="yan@example.com")
    assert _reconcile() == 2

    assert _merge_certain(owner, limit=1) == [("merged", {first[0].pk, first[1].pk}, first[0].pk)]
    assert _merge_certain(owner, limit=1) == [("merged", {second[0].pk, second[1].pk}, second[0].pk)]
    assert _merge_certain(owner) == []
