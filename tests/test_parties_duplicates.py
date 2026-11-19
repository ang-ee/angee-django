"""A person's batch merge of duplicate sets: the certain pairs of the review, and a selection from a list."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context, to_subject_ref

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Organization, Party, Person
from tests.conftest import create_user
from tests.test_parties_same_as import _address, _kept_separate, _pairs, _reconcile


def _people(owner: Any, *names: str, value: str = "zoe@example.com") -> list[Any]:
    """People ``owner`` owns, oldest first, on one address: the first states it, each other's source claims it."""

    handle = _address(owner, value)
    with system_context(reason="duplicates people"):
        people = [Person._base_manager.create(display_name=name, created_by=owner) for name in names]
        Party.objects.assert_handle(people[0], handle, asserted_by=owner)
        for person in people[1:]:
            Party.objects.propose_claimed_handle_as_system(person, handle, evidence=handle, reason="tests.claim")
    return people


def _merge(actor: Any, *sets: list[Any], certain: bool = False) -> list[tuple[str, Any, str]]:
    outcomes = Party.objects.merge_duplicates(
        actor=actor, sets=[[party.public_id for party in members] for members in sets], certain=certain,
    )
    return [(str(row.outcome), row.survivor.pk if row.survivor else None, row.reason) for row in outcomes]


def _certain(actor: Any) -> list[tuple[set[Any], Any]]:
    return [
        ({pair.left.pk, pair.right.pk}, pair.survivor.pk)
        for pair in Party.objects.certain_same_as_pairs(actor=actor)
    ]


def _merge_certain(actor: Any) -> list[tuple[str, Any, str]]:
    pairs = Party.objects.certain_same_as_pairs(actor=actor)
    return _merge(actor, *([pair.left, pair.right] for pair in pairs), certain=True)


def _merged_into(*parties: Any) -> list[Any]:
    with system_context(reason="duplicates readback"):
        return [Party._base_manager.get(pk=party.pk).merged_into_id for party in parties]


def _person_choices(owner: Any, survivor: Any) -> list[str]:
    """The aspects the batch recorded as ``owner``'s own choice on ``survivor``'s values."""

    with system_context(reason="duplicates readback"):
        return sorted(
            row.aspect
            for statements in (Party.statements, Person.statements)
            for row in statements.model._base_manager.filter(
                subject_id=survivor.pk, rank="preferred", asserted_by=str(to_subject_ref(owner)),
            )
        )


def test_a_certain_pair_merges_into_the_older_record_and_pins_nothing(db: None) -> None:
    """The person confirms the pair; what shows afterwards is the sources', no value is pinned as theirs."""

    del db
    owner = create_user("duplicates-certain")
    older, newer = _people(owner, "Zoë Example-Smith", "zoe example smith")
    assert _reconcile() == 1
    assert _certain(owner) == [({older.pk, newer.pk}, older.pk)]

    assert _merge_certain(owner) == [("merged", older.pk, "")]

    assert _merged_into(newer) == [older.pk]
    assert _person_choices(owner, older) == []
    with system_context(reason="duplicates readback"):
        assert Party._base_manager.get(pk=older.pk).display_name == "Zoë Example-Smith"
    assert _pairs() == []


def _organizations(owner: Any) -> list[Any]:
    handle = _address(owner, "office@example.com")
    with system_context(reason="duplicates organizations"):
        rows = [Organization._base_manager.create(display_name="Example Ltd", created_by=owner) for _ in range(2)]
        Party.objects.assert_handle(rows[0], handle, asserted_by=owner)
        Party.objects.propose_claimed_handle_as_system(rows[1], handle, evidence=handle, reason="tests.claim")
    return rows


def _an_account_person(owner: Any) -> list[Any]:
    handle = _address(owner, "zoe@example.com")
    with system_context(reason="duplicates an account's person"):
        person = Person.objects.for_user(owner)
        Party.objects.assert_handle(person, handle, asserted_by=owner)
        contact = Person._base_manager.create(display_name=person.display_name, created_by=owner)
        Party.objects.propose_claimed_handle_as_system(contact, handle, evidence=handle, reason="tests.claim")
    return [person, contact]


def _a_third_party_linked(owner: Any) -> list[Any]:
    older, newer, _third = _people(owner, "Zoë Example", "Zoe Example", "Zoe Example")
    return [older, newer]


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(_organizations, id="not-people"),
        pytest.param(_an_account_person, id="an-accounts-own-person"),
        pytest.param(_a_third_party_linked, id="a-third-party-on-the-address"),
        pytest.param(lambda owner: _people(owner, "Zoë Example", "Zoe B. Example"), id="names-differ"),
        pytest.param(lambda owner: _people(owner, "Zoë Example", Party.PLACEHOLDER_NAME), id="a-name-missing"),
        pytest.param(lambda owner: _people(owner, "zoe@example.com", "Zoe@Example.com"), id="named-after-the-address"),
    ],
)
def test_a_pair_that_is_not_certain_is_not_listed_and_its_certain_merge_refuses(db: None, arrange: Any) -> None:
    del db
    owner = create_user("duplicates-not-certain")
    left, right = arrange(owner)
    _reconcile()
    assert frozenset({left.pk, right.pk}) in {frozenset(pair) for pair, _handles in _pairs()}

    assert _certain(owner) == []
    ((outcome, survivor, _reason),) = _merge(owner, [left, right], certain=True)
    assert (outcome, survivor) in {("not_certain", None), ("refused", None)}
    assert _merged_into(left, right) == [None, None]


def test_a_pair_that_stops_being_certain_after_the_list_is_reported_not_merged(db: None) -> None:
    del db
    owner = create_user("duplicates-stops-certain")
    older, newer = _people(owner, "Zoë Example", "Zoe Example")
    assert _reconcile() == 1
    listed = Party.objects.certain_same_as_pairs(actor=owner)
    with system_context(reason="duplicates a third party claims the address"):
        third = Person._base_manager.create(display_name="Zoe Example", created_by=owner)
        handle = Handle._base_manager.get(value="zoe@example.com")
        Party.objects.propose_claimed_handle_as_system(third, handle, evidence=handle, reason="tests.claim")

    assert _merge(owner, *([pair.left, pair.right] for pair in listed), certain=True) == [
        ("not_certain", None, "No longer a certain pair."),
    ]
    assert _merged_into(newer) == [None]


def test_a_set_of_three_folds_into_the_oldest_and_loses_no_value_unpinned(db: None) -> None:
    """The oldest survives; its values stand, an empty one takes the first merged record's that has one, unpinned."""

    del db
    owner = create_user("duplicates-three")
    oldest, middle, newest = _people(owner, "Ada Lovelace", "A. Lovelace", "Ada King")
    with system_context(reason="duplicates values on the records"):
        Person._base_manager.filter(pk=oldest.pk).update(nickname="Ada")
        Person._base_manager.filter(pk=middle.pk).update(nickname="Countess", given_name="Augusta", notes="Analyst")
        Person._base_manager.filter(pk=newest.pk).update(given_name="Ada", birthday=date(1815, 12, 10))

    assert _merge(owner, [newest, oldest, middle]) == [("merged", oldest.pk, "")]

    assert _merged_into(middle, newest) == [oldest.pk, oldest.pk]
    with system_context(reason="duplicates readback"):
        survivor = Person._base_manager.get(pk=oldest.pk)
        for statements in survivor.statement_managers():
            statements.resolve()
        survivor.refresh_from_db()
    assert (survivor.display_name, survivor.nickname, survivor.given_name, survivor.notes, survivor.birthday) == (
        "Ada Lovelace", "Ada", "Augusta", "Analyst", date(1815, 12, 10),
    )
    assert _person_choices(owner, oldest) == []


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        pytest.param(
            _an_account_person, "An account's own person is merged by hand, on the compare page.", id="account",
        ),
        pytest.param(
            lambda owner: [*_organizations(owner), *_people(owner, "Example Ltd", value="ltd@example.com")],
            "Only people, or only organizations, merge together.", id="mixed-kinds",
        ),
    ],
)
def test_a_refused_set_leaves_its_records_and_the_next_set_still_merges(db: None, arrange: Any, reason: str) -> None:
    del db
    owner = create_user("duplicates-refused-set")
    refused = arrange(owner)
    older, newer = _people(owner, "Yan Example", "Yan Example", value="yan@example.com")

    assert _merge(owner, refused, [older, newer]) == [("refused", None, reason), ("merged", older.pk, "")]
    assert _merged_into(*refused[1:]) == [None] * len(refused[1:])


def test_a_set_holding_a_pair_a_person_kept_apart_refuses(db: None) -> None:
    """A batch never overrules a person's earlier decision, even between two records that are not the survivor."""

    del db
    owner = create_user("duplicates-kept-apart")
    oldest, middle, newest = _people(owner, "Zoë Example", "Zoe Example", "Zoe Example")
    _kept_separate(owner, middle, newest)

    assert _merge(owner, [oldest, middle, newest]) == [("refused", None, "A person kept two of these records apart.")]
    assert _merged_into(middle, newest) == [None, None]


def test_a_set_whose_member_changed_since_the_read_refuses_rather_than_shrinks(db: None) -> None:
    del db
    owner = create_user("duplicates-changed")
    oldest, middle, newest = _people(owner, "Zoë Example", "Zoe Example", "Zoe Example")
    with actor_context(owner):
        Party.objects.get(pk=oldest.pk).merge(records=[newest.public_id])

    assert _merge(owner, [oldest, middle, newest]) == [
        ("refused", None, "A record is trashed or already merged into another."),
    ]
    assert _merged_into(middle) == [None]


def test_the_owner_refuses_to_merge_a_person_with_an_organization(db: None) -> None:
    """Every merge entry refuses a mixed set, the compare page's included."""

    del db
    owner = create_user("duplicates-mixed-owner")
    with system_context(reason="duplicates mixed"):
        person = Person._base_manager.create(display_name="Example", created_by=owner)
        organization = Organization._base_manager.create(display_name="Example", created_by=owner)

    with actor_context(owner), pytest.raises(ValidationError, match="Only people, or only organizations"):
        Party.objects.get(pk=person.pk).merge(records=[organization.public_id])


def _account(kind: str) -> Any:
    if kind == "person":
        return create_user("duplicates-person")
    if kind == "service":
        return get_user_model().objects.create_user(username="duplicates-service", kind="service")
    with system_context(reason="duplicates an agent's account"):
        agent = apps.get_model("agents", "Agent").objects.create(
            name="Reviewer", owner=create_user("duplicates-agent-owner"), runtime_class="pydantic",
        )
    return agent.user


def _owned_people(account: Any) -> list[Any]:
    """Two people ``account`` owns, each claimed for one address by a source: a certain pair ``account`` reviews."""

    handle = _address(account, "zoe@example.com")
    with system_context(reason="duplicates an account's two people"):
        people = [
            Person._base_manager.create(display_name=name, created_by=account)
            for name in ("Zoë Example", "Zoe Example")
        ]
        for person in people:
            Party.objects.propose_claimed_handle_as_system(person, handle, evidence=handle, reason="tests.claim")
    return people


@pytest.mark.parametrize(
    "kind",
    [pytest.param("person", id="an-active-person"), pytest.param("service", id="a-service-account"),
     pytest.param("agent", id="an-agents-account")],
)
def test_only_an_active_person_merges_in_a_batch_whatever_manager_is_asked(db: None, kind: str) -> None:
    """The gate is the party's ``batch_merge``, read on the party even through the person model's manager."""

    del db
    account = _account(kind)
    older, newer = _owned_people(account)
    assert _reconcile() == 1

    assert bool(_certain(account)) is (kind == "person")
    outcomes = Person.objects.merge_duplicates(actor=account, sets=[[older.public_id, newer.public_id]])
    assert [(str(row.outcome), row.survivor.pk if row.survivor else None) for row in outcomes] == [
        ("merged", older.pk) if kind == "person" else ("refused", None),
    ]


def test_a_reader_who_may_not_change_the_records_merges_nothing(db: None) -> None:
    del db
    owner, reviewer = create_user("duplicates-writer"), create_user("duplicates-reader")
    older, newer = _people(owner, "Zoë Example", "Zoe Example")
    assert _reconcile() == 1
    with actor_context(owner):
        handle = Handle.objects.get(value="zoe@example.com")
        for record in (*Party.objects.filter(pk__in=(older.pk, newer.pk)), handle):
            record.grant_record_access("reader", reviewer)

    assert _certain(reviewer) == []
    assert _merge(reviewer, [older, newer]) == [
        ("refused", None, "Only an active person who may change every one of these records merges them in a batch."),
    ]


def refuse_merges(record: Any) -> str:
    """A merge guard an addon contributes to hold its records."""

    del record
    return "Held by a test addon."


def test_a_merge_the_owner_refuses_reports_its_reason(db: None) -> None:
    del db
    owner = create_user("duplicates-guarded")
    older, newer = _people(owner, "Zoë Example", "Zoe Example")

    with override_settings(ANGEE_MERGE_GUARDS=[f"{__name__}.refuse_merges"]):
        assert _merge(owner, [older, newer]) == [("refused", None, "Held by a test addon.")]


def _lock_conflict(sqlstate: str) -> OperationalError:
    cause = Exception("deadlock detected")
    cause.sqlstate = sqlstate  # type: ignore[attr-defined]  # the driver error psycopg raises carries its SQLSTATE
    error = OperationalError(str(cause))
    error.__cause__ = cause
    return error


def test_a_set_a_lock_conflict_stops_is_reported_and_the_next_set_still_merges(db: None) -> None:
    """A deadlock rolls back that set alone; the batch goes on."""

    del db
    owner = create_user("duplicates-stopped")
    first = _people(owner, "Zoë Example", "Zoe Example", value="zoe@example.com")
    second = _people(owner, "Yan Example", "yan example", value="yan@example.com")
    stopped: list[str] = []

    def deadlock_once(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if not stopped and sql.lstrip().upper().startswith("UPDATE") and '"merged_into_id"' in sql:
            stopped.append(sql)
            raise _lock_conflict("40P01")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(deadlock_once):
        outcomes = _merge(owner, first, second)

    assert [outcome for outcome, _survivor, _reason in outcomes] == ["stopped", "merged"]
    assert _merged_into(first[1], second[1]) == [None, second[0].pk]


def test_a_database_error_other_than_a_lock_conflict_stops_the_call(db: None) -> None:
    """A lost connection is no set's outcome: it reaches the caller."""

    del db
    owner = create_user("duplicates-lost")
    pair = _people(owner, "Zoë Example", "Zoe Example")

    def lost(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if sql.lstrip().upper().startswith("UPDATE") and '"merged_into_id"' in sql:
            raise _lock_conflict("08006")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(lost), pytest.raises(OperationalError):
        _merge(owner, pair)


def test_a_batch_takes_at_most_fifty_sets_and_a_set_at_most_ten_records(db: None) -> None:
    del db
    owner = create_user("duplicates-caps")
    assert (Party.objects.BATCH_SETS, Party.objects.SET_MEMBERS) == (50, 10)
    with pytest.raises(ValidationError, match="at most 50"):
        Party.objects.merge_duplicates(actor=owner, sets=[["a", "b"]] * 51)
    many = _people(owner, *(f"Zoe Example {index}" for index in range(11)))

    assert _merge(owner, many) == [("refused", None, "Choose two to 10 records to merge as one.")]


def test_the_dialog_read_shows_disagreements_in_a_fixed_number_of_queries(db: None) -> None:
    """Each set's disagreeing fields, with what will show; the read costs the same for one set as for five."""

    del db
    owner = create_user("duplicates-preview")
    sets = [_people(owner, f"Zoë Example {index}", f"Zoe Example {index}", value=f"z{index}@example.com")
            for index in range(5)]

    def read(count: int) -> tuple[int, Any]:
        with CaptureQueriesContext(connection) as queries:
            previews = Party.objects.preview_duplicates(
                actor=owner, sets=[[party.public_id for party in members] for members in sets[:count]],
            )
        return len(queries.captured_queries), previews

    one, _previews = read(1)
    five, previews = read(5)
    assert one == five
    first = previews[0]
    assert (first.outcome, first.survivor.pk) == (None, sets[0][0].pk)
    assert [(row.field, row.values, row.shown) for row in first.disagreements] == [
        ("display_name", ("Zoë Example 0", "Zoe Example 0"), "Zoë Example 0"),
    ]
