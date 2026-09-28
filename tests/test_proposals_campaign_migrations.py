"""Historical disclosure upgrades and reconstructable state declarations."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.base.fields import StateField
from angee.proposals.runtime_migrations import derived_disclosure, legacy_disclosure
from tests.proposals_models import Round
from tests.test_runtime_migrations import (
    _proposal_disclosure_history,
)
from tests.test_runtime_migrations import (
    isolated_upgrade_database as isolated_upgrade_database,
)


class DisclosureHistory:
    """Populate the repository's existing historical migration floor."""

    def __init__(self, prefix: str) -> None:
        self.stamp = datetime(2028, 2, 3, tzinfo=UTC)
        self.state = _proposal_disclosure_history()
        with connection.schema_editor() as editor:
            for model in self.state.apps.get_models():
                editor.create_model(model)
        users = self.state.apps.get_model("iam", "User").objects
        for pk in range(1, 7):
            users.create(pk=pk, username=f"{prefix}-person-{pk}")

    def model(self, label: str, name: str) -> Any:
        return self.state.apps.get_model(label, name)

    def round(self, **values: Any) -> Any:
        return self.model("proposals", "Round").objects.create(
            **{
                "opened_at": self.stamp,
                "updated_at": self.stamp,
                "opening_policy": "answers",
                "facilitator_id": 6,
                **values,
            }
        )

    def grant(
        self,
        store: str,
        resource: tuple[str, Any],
        relation: str,
        subject: tuple[str, Any],
        subject_relation: str = "",
        **values: Any,
    ) -> Any:
        attrs = {"relation": relation, "optional_subject_relation": subject_relation, **values}
        if store == "Relationship":
            attrs.update(
                resource_type=resource[0],
                resource_id=str(resource[1]),
                subject_type=subject[0],
                subject_id=str(subject[1]),
            )
        else:
            registry = self.model("rebac", "RebacResource").objects
            attrs["resource_fk"], _ = registry.get_or_create(resource_type=resource[0], resource_id=str(resource[1]))
            attrs["subject_fk"], _ = registry.get_or_create(resource_type=subject[0], resource_id=str(subject[1]))
        return self.model("rebac", store).objects.create(**attrs)

    def receipts(self) -> None:
        with connection.schema_editor() as editor:
            self.state = derived_disclosure.Migration("campaign_receipts", "proposals").apply(self.state, editor)

    def apply(self) -> None:
        with connection.schema_editor() as editor:
            legacy_disclosure.forwards(self.state.apps, editor)


@pytest.fixture
def history(isolated_upgrade_database: None, request: pytest.FixtureRequest) -> DisclosureHistory:
    del isolated_upgrade_database
    prefix = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    return DisclosureHistory(prefix)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("policy", ("facilitator_only", "answers", "answers_and_tracks"))
def test_receipt_migration_distinguishes_withdrawal_before_at_and_after_opening(
    history: DisclosureHistory,
    policy: str,
) -> None:
    h = history
    round = h.round(opening_policy=policy)
    proposals = h.model("proposals", "Proposal").objects
    expected = {}
    for index, (state, decided, eligible) in enumerate(
        (
            ("draft", None, False),
            ("submitted", None, True),
            ("accepted", h.stamp, True),
            ("partially_accepted", h.stamp, True),
            ("declined", h.stamp, True),
            ("withdrawn", h.stamp - timedelta(seconds=1), False),
            ("withdrawn", h.stamp, True),
            ("withdrawn", h.stamp + timedelta(seconds=1), True),
        )
    ):
        row = proposals.create(round=round, responder_id=index + 10, state=state, decided_at=decided)
        expected[row.pk] = h.stamp if eligible and policy != "facilitator_only" else None
    h.receipts()
    rows = h.model("proposals", "Proposal").objects
    assert dict(rows.values_list("pk", "disclosed_at")) == expected
    with connection.schema_editor() as editor:
        derived_disclosure.forwards(h.state.apps, editor)
    assert dict(rows.values_list("pk", "disclosed_at")) == expected
    assert not rows.exclude(track_published_at=None).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", ("Relationship", "RelationshipRegistry"))
def test_group_invitations_convert_once_and_preview_writes_nothing(history: DisclosureHistory, store: str) -> None:
    h = history
    round = h.round()
    h.model("proposals", "Proposal").objects.create(round=round, responder_id=1)
    h.grant(store, ("proposals/round", round.pk), "responder", ("auth/group", 12), "member")
    for user in (1, 2, 3):
        h.grant(store, ("auth/group", 12), "member", ("auth/user", user))
    h.receipts()
    grants = h.model("rebac", store).objects
    before = list(grants.order_by("pk").values())
    preview = list(legacy_disclosure.transition(h.state.apps, "default"))
    assert {row[2] for row in preview if row[0] == "create shell"} == {"2", "3"}
    assert list(grants.order_by("pk").values()) == before
    assert h.model("proposals", "Proposal").objects.count() == 1
    h.apply()
    first = list(h.model("proposals", "Proposal").objects.order_by("pk").values())
    h.apply()
    assert list(h.model("proposals", "Proposal").objects.order_by("pk").values()) == first
    assert {row["responder_id"] for row in first} == {1, 2, 3}
    assert set(grants.values_list("relation", flat=True)) == {"member"}


@pytest.mark.django_db(transaction=True)
def test_duplicate_cross_store_invitations_create_one_shell(history: DisclosureHistory) -> None:
    h = history
    round = h.round()
    for store in ("Relationship", "RelationshipRegistry"):
        h.grant(store, ("proposals/round", round.pk), "responder", ("auth/user", 2))
    h.receipts()
    h.apply()
    h.apply()
    assert list(h.model("proposals", "Proposal").objects.values_list("responder_id", flat=True)) == [2]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", ("Relationship", "RelationshipRegistry"))
def test_ambiguous_requesters_are_logged_without_guessing_a_party(
    history: DisclosureHistory,
    store: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = history
    round = h.round()
    for user in (2, 3):
        h.grant(store, ("proposals/round", round.pk), "requester", ("auth/user", user))
    h.receipts()
    with caplog.at_level(logging.WARNING):
        h.apply()
    assert "Unresolved proposal requesters" in caplog.text
    assert "users=['2', '3']" in caplog.text
    assert h.model("proposals", "Round").objects.get(pk=round.pk).requester_party_id is None
    assert h.model("parties", "Person").objects.count() == 0
    assert not h.model("rebac", store).objects.filter(relation="requester").exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", ("Relationship", "RelationshipRegistry"))
def test_one_requester_is_identified_once_but_an_existing_different_party_is_preserved(
    history: DisclosureHistory,
    store: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = history
    identified = h.round()
    person = h.model("parties", "Person").objects.create(user_id=4, display_name="Existing requester")
    retained = h.round(requester_party_id=person.pk)
    for round in (identified, retained):
        h.grant(store, ("proposals/round", round.pk), "requester", ("auth/user", 2))
    h.receipts()
    with caplog.at_level(logging.WARNING):
        h.apply()
        h.apply()
    rows = h.model("proposals", "Round").objects
    assigned = rows.get(pk=identified.pk).requester_party_id
    assert h.model("parties", "Person").objects.get(pk=assigned).user_id == 2
    assert h.model("parties", "Person").objects.filter(user_id=2).count() == 1
    assert rows.get(pk=retained.pk).requester_party_id == person.pk
    assert "Unresolved proposal requesters" in caplog.text


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", ("Relationship", "RelationshipRegistry"))
def test_ceremony_conversion_preserves_manual_shares_and_publication_time(
    history: DisclosureHistory, store: str
) -> None:
    h = history
    round = h.round()
    proposal = h.model("proposals", "Proposal").objects.create(
        round=round,
        responder_id=1,
        state="submitted",
        submitted_at=h.stamp - timedelta(days=1),
        track_id=14,
    )
    h.model("proposals", "Proposal").objects.create(
        round=round,
        responder_id=2,
        state="submitted",
        submitted_at=h.stamp - timedelta(days=1),
    )
    for relation, subject in (("editor", 1), ("reader", 2)):
        h.grant(store, ("proposals/proposal", proposal.pk), relation, ("auth/user", subject))
    for relation, subject in (("editor", 1), ("editor", 6), ("reader", 2)):
        h.grant(store, ("projects/project", 14), relation, ("auth/user", subject))
    manual = [
        h.grant(store, ("proposals/proposal", proposal.pk), "reader", ("auth/user", 5)).pk,
        h.grant(store, ("projects/project", 14), "editor", ("auth/user", 5)).pk,
        h.grant(store, ("projects/project", 14), "reader", ("auth/group", 7), "member").pk,
    ]
    h.receipts()
    h.apply()
    h.apply()
    assert set(h.model("rebac", store).objects.values_list("pk", flat=True)) == set(manual)
    row = h.model("proposals", "Proposal").objects.get(pk=proposal.pk)
    assert row.disclosed_at == h.stamp
    assert row.track_published_at == h.stamp


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", ("Relationship", "RelationshipRegistry"))
def test_conditional_missing_and_wildcard_invitations_are_logged_not_expanded(
    history: DisclosureHistory,
    store: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = history
    round = h.round()
    for user, attrs in ((1, {"caveat_name": "conditional"}), (2, {"expires_at": h.stamp}), ("*", {}), (99, {})):
        h.grant(store, ("proposals/round", round.pk), "responder", ("auth/user", user), **attrs)
    h.receipts()
    with caplog.at_level(logging.WARNING):
        h.apply()
    assert h.model("proposals", "Proposal").objects.count() == 0
    assert len([record for record in caplog.records if "Unresolved proposal invitation" in record.message]) == 4


@pytest.mark.parametrize("present", ("disclosed_at", "track_published_at"))
def test_receipt_migration_refuses_a_partial_upgrade_before_writes(present: str) -> None:
    state = _proposal_disclosure_history()
    state.models["proposals", "proposal"].fields[present] = models.DateTimeField(null=True)
    with pytest.raises(ValueError, match="partial"):
        derived_disclosure.applies(state)


def test_state_field_reports_all_reserved_wire_values_in_one_diagnostic() -> None:
    enum = models.TextChoices(
        "CollisionStates",
        {
            "FIRST": ("names", "Names"),
            "SECOND": ("values", "Values"),
            "THIRD": ("labels", "Labels"),
            "FOURTH": ("choices", "Choices"),
        },
    )
    field = StateField(choices_enum=enum)
    errors = [error for error in field.check() if error.id == "angee.E028"]
    assert len(errors) == 1
    assert all(value in errors[0].msg for value in ("names", "values", "labels", "choices"))
    assert errors[0].obj is field


def test_named_roster_state_round_trips_through_model_state_without_losing_values() -> None:
    state = ModelState.from_model(Round)
    field = state.fields["roster_visibility"]
    assert list(field.choices) == [("hidden", "Hidden"), ("named", "Named"), ("status", "Status")]
    assert not [error for error in field.check() if error.id == "angee.E028"]
    isolated = ProjectState()
    isolated.add_model(ModelState("proposals", "RosterProbe", {"roster_visibility": field}))
    rendered = isolated.apps.get_model("proposals", "RosterProbe")
    assert rendered._meta.get_field("roster_visibility").clone().choices == field.choices
