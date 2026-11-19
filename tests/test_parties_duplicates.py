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
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

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


def test_a_set_the_database_fails_is_its_own_outcome_and_the_next_set_still_merges(
    db: None, caplog: pytest.LogCaptureFixture,
) -> None:
    """Any other database failure (here a statement timeout) rolls that set back and reports it; the batch goes on."""

    del db
    owner = create_user("duplicates-failed")
    first = _people(owner, "Zoë Example", "Zoe Example", value="zoe@example.com")
    second = _people(owner, "Yan Example", "yan example", value="yan@example.com")
    failed: list[str] = []

    def time_out_once(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if not failed and sql.lstrip().upper().startswith("UPDATE") and '"merged_into_id"' in sql:
            failed.append(sql)
            raise _lock_conflict("57014")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(time_out_once), caplog.at_level("ERROR", logger="angee.parties.managers"):
        outcomes = _merge(owner, first, second)

    assert outcomes[0] == ("failed", None, Party.objects.FAILED_REASON)
    assert [outcome for outcome, _survivor, _reason in outcomes] == ["failed", "merged"]
    assert _merged_into(first[1], second[1]) == [None, second[0].pk]
    # The reason is for the person; the error itself is for an operator, in the log.
    assert "deadlock detected" not in outcomes[0][2]
    assert any(record.exc_info for record in caplog.records)


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


# The person's fields each ``read__<field>`` gate hides from a reader without ``read_private``, with two values each.
PRIVATE_FIELDS = {
    "birthday": (date(1990, 1, 2), date(1985, 3, 4)),
    "anniversary": (date(2010, 6, 1), date(2012, 7, 2)),
    "nickname": ("Zo", "Zed"),
    "notes": ("Met at the fair.", "Supplier contact."),
    "first_met_note": ("At the fair", "On a call"),
}


def _private_pair(owner: Any, viewer: Any) -> list[Any]:
    """Two people ``owner`` owns that differ in every private field and in a public one; ``viewer`` reads them."""

    pair = _people(owner, "Zoe Example", "Zoe Example", value="private@example.com")
    with system_context(reason="duplicates private values"):
        for index, person in enumerate(pair):
            Person._base_manager.filter(pk=person.pk).update(
                given_name=("Zoe", "Zoey")[index], **{field: values[index] for field, values in PRIVATE_FIELDS.items()},
            )
    with actor_context(owner):
        for party in Party.objects.filter(pk__in=[person.pk for person in pair]):
            party.grant_record_access("identity_reader", viewer)
    return pair


def _direct_read(viewer: Any, person: Any) -> dict[str, Any]:
    """What ``viewer`` reads of ``person`` through the schema's own record read."""

    from angee.graphql.testing.documents import execute_schema, result_data
    from tests.test_parties_graphql import _schema

    fields = " ".join(["given_name", *PRIVATE_FIELDS])
    query = f"query($id: String!) {{ people_by_pk(id: $id) {{ {fields} }} }}"
    return result_data(execute_schema(_schema("console"), query, {"id": person.sqid}, user=viewer))["people_by_pk"]


def test_a_party_editor_without_the_persons_gates_merges_without_seeing_their_values(db: None) -> None:
    """Each field's own ``read__<field>`` gate decides per member, as its record read does: no second list.

    The party's ``editor`` may merge (``write`` on the party) while holding none of the person's own grants.
    Each column is read through the resource that declares it, as its record read does: the person's columns
    through the person (``people_by_pk``), whose gates hide them from this editor, and the party's columns
    through the party (``parties_by_pk``), whose ``read_private`` the editor holds. In the preview of a set the
    editor may merge, a field its record read shows as null has neither values nor a disagreement, and every
    field it shows is compared.
    """

    del db
    from angee.graphql.testing.documents import execute_schema, result_data
    from tests.test_parties_graphql import _schema

    owner, editor = create_user("duplicates-gate-owner"), create_user("duplicates-gate-editor")
    pair = _private_pair(owner, editor)
    with system_context(reason="duplicates party editor"):
        write_relationships([
            RelationshipTuple(
                resource=to_object_ref(Party._base_manager.get(pk=person.pk)), relation="editor",
                subject=to_subject_ref(editor),
            )
            for person in pair
        ])

    (preview,) = Party.objects.preview_duplicates(actor=editor, sets=[[person.public_id for person in pair]])

    person_read = _direct_read(editor, pair[0])
    party_read = result_data(execute_schema(
        _schema("console"), "query($id: String!) { parties_by_pk(id: $id) { notes first_met_note } }",
        {"id": pair[0].sqid}, user=editor,
    ))["parties_by_pk"]
    party_columns = {"notes", "first_met_note"}
    hidden = {field for field in PRIVATE_FIELDS.keys() - party_columns if person_read[field] is None}
    hidden |= {field for field in party_columns if party_read[field] is None}
    assert {"birthday", "anniversary"} <= hidden
    assert preview.outcome is None
    assert {row.field for row in preview.disagreements} == {"given_name", *PRIVATE_FIELDS} - hidden
    (owned,) = Party.objects.preview_duplicates(actor=owner, sets=[[person.public_id for person in pair]])
    assert {row.field for row in owned.disagreements} == {"given_name", *PRIVATE_FIELDS}


def test_a_refused_set_discloses_no_value_of_its_members(db: None) -> None:
    """An identity reader may not merge these records, so the preview shows nothing of their values."""

    del db
    from angee.graphql.testing.documents import execute_schema, result_data
    from tests.test_parties_graphql import _schema

    owner, viewer = create_user("duplicates-refused-owner"), create_user("duplicates-refused-viewer")
    pair = _private_pair(owner, viewer)

    (preview,) = Party.objects.preview_duplicates(actor=viewer, sets=[[person.public_id for person in pair]])
    document = """query($sets: [[ID!]!]!) {
      duplicate_set_previews(sets: $sets) { outcome members { id first_met_note } disagreements { field values } }
    }"""
    (row,) = result_data(execute_schema(_schema("console"), document, {"sets": [[p.sqid for p in pair]]}, user=viewer))[
        "duplicate_set_previews"
    ]

    assert (preview.outcome, preview.disagreements) == ("refused", ())
    assert (row["outcome"], row["disagreements"]) == ("REFUSED", [])
    assert [member["first_met_note"] for member in row["members"]] == [None, None]


def test_an_oversized_or_unreadable_set_is_refused_before_anything_is_locked(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any signed-in account may name any public ids: size and readability are checked before a lock is taken."""

    del db
    owner, stranger = create_user("duplicates-prelock-owner"), create_user("duplicates-prelock-stranger")
    many = _people(owner, *(f"Zoe Example {index}" for index in range(30)), value="prelock@example.com")
    locked: list[int] = []
    original = Party.lock_merge_records.__func__  # type: ignore[attr-defined]

    def spy(cls: Any, records: Any) -> None:
        locked.append(len(records))
        original(cls, records)

    monkeypatch.setattr(Party, "lock_merge_records", classmethod(spy))

    assert _merge(owner, many) == [("refused", None, "Choose two to 10 records to merge as one.")]
    assert _merge(stranger, many[:10]) == [("refused", None, "A record is gone or you may not read it.")]
    assert locked == []
    assert [outcome for outcome, _survivor, _reason in _merge(owner, many[:2])] == ["merged"]
    # The set that passes locks its two records (the verb's pass, then the merge's own, already held).
    assert locked and set(locked) == {2}


def test_certain_pairs_come_in_batches_each_after_the_last(db: None) -> None:
    """The batch after a cursor starts past the last pair of the previous batch: every certain pair, once."""

    del db
    owner = create_user("duplicates-batches")
    made = [
        frozenset(person.pk for person in _people(owner, f"Zoe Example {index}", f"Zoe Example {index}",
                                                   value=f"batch{index}@example.com"))
        for index in range(5)
    ]
    _reconcile()

    batches, after = [], None
    while batch := Party.objects.certain_same_as_pairs(actor=owner, limit=2, after=after):
        batches.append([frozenset({pair.left.pk, pair.right.pk}) for pair in batch])
        after = batch[-1].id

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert sorted(pair for batch in batches for pair in batch) == sorted(made)


def test_the_review_counts_say_how_many_pairs_and_how_many_certain_the_reader_may_review(db: None) -> None:
    """Totals for the Review page, scoped as its reads are: every reviewable pair, and the certain among them."""

    del db
    from angee.graphql.testing.documents import execute_schema, result_data
    from tests.test_parties_graphql import _schema

    owner, other = create_user("duplicates-counts"), create_user("duplicates-counts-other")
    for index in range(3):
        _people(owner, f"Zoe Example {index}", f"Zoe Example {index}", value=f"sure{index}@example.com")
    for index in range(2):
        _people(owner, f"Zoe Example {index}", f"Zoe B. Example {index}", value=f"unsure{index}@example.com")
    # A pair whose cited address the reader cannot read is no pair of theirs to review.
    handle = _address(other, "concealed@example.com")
    with system_context(reason="duplicates concealed pair"):
        hidden = [Person._base_manager.create(display_name="Zoe Hidden", created_by=owner) for _ in range(2)]
        Party.objects.assert_handle(hidden[0], handle, asserted_by=owner)
        Party.objects.propose_claimed_handle_as_system(hidden[1], handle, evidence=handle, reason="tests.claim")
    _reconcile()

    counts = Party.objects.same_as_review_counts(actor=owner)
    document = "query { same_as_pair_counts { pairs certain } }"
    schema = result_data(execute_schema(_schema("console"), document, user=owner))["same_as_pair_counts"]

    assert (counts.pairs, counts.certain) == (5, 3)
    assert schema == {"pairs": 5, "certain": 3}


def _renamed(owner: Any, pair: list[Any]) -> None:
    del owner
    with system_context(reason="duplicates digest rename"):
        Person._base_manager.filter(pk=pair[1].pk).update(display_name="Zoe Renamed")


def _readdressed(owner: Any, pair: list[Any]) -> None:
    del owner, pair
    with system_context(reason="duplicates digest readdress"):
        Handle._base_manager.filter(value="digest@example.com").update(
            value="moved@example.com", normalized_value="moved@example.com",
        )


def _restated(owner: Any, pair: list[Any]) -> None:
    with system_context(reason="duplicates digest restate"):
        handle = Handle._base_manager.get(value="digest@example.com")
        Party.objects.assert_handle(pair[1], handle, asserted_by=owner)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(_renamed, id="a-name"),
        pytest.param(_readdressed, id="a-handle"),
        pytest.param(_restated, id="a-source"),
    ],
)
def test_a_set_whose_shown_facts_changed_since_the_preview_is_refused(db: None, change: Any) -> None:
    """The mutation echoes each set's digest of what the dialog showed; a changed set asks the person to look again."""

    del db
    owner = create_user("duplicates-digest-changed")
    pair = _people(owner, "Zoe Example", "Zoe Example", value="digest@example.com")
    ids = [[person.public_id for person in pair]]
    (preview,) = Party.objects.preview_duplicates(actor=owner, sets=ids)

    change(owner, pair)
    (outcome,) = Party.objects.merge_duplicates(actor=owner, sets=ids, digests=[preview.digest])

    assert (str(outcome.outcome), outcome.reason) == ("refused", Party.objects.CHANGED_REASON)
    assert _merged_into(pair[1]) == [None]


def test_an_unchanged_set_merges_with_the_digest_of_its_preview(db: None) -> None:
    del db
    owner = create_user("duplicates-digest-same")
    pair = _people(owner, "Zoe Example", "Zoe Example", value="digest@example.com")
    ids = [[person.public_id for person in pair]]
    (preview,) = Party.objects.preview_duplicates(actor=owner, sets=ids)
    (again,) = Party.objects.preview_duplicates(actor=owner, sets=ids)

    (outcome,) = Party.objects.merge_duplicates(actor=owner, sets=ids, digests=[preview.digest])

    assert preview.digest and preview.digest == again.digest
    assert str(outcome.outcome) == "merged"
    assert _merged_into(pair[1]) == [pair[0].pk]


PINNED_KEY = "angee-parties-pinned-digest-key"
PINNED_BASIS = {
    "name": "Ada Example",
    "address": [{"street": "1 Example Road", "locality": "Exampleton", "country": "GB"}],
    "handles": [{"id": "hdl_1", "platform": "email", "value": "ada@example.com", "status": "asserted"}],
}
# IdentityBasis.facts_hash of PINNED_BASIS under PINNED_KEY, computed by running the IdentityBasis class of the
# commit before the digest function was shared (5247a71d): a review issued before it still matches after it.
PINNED_IDENTITY_DIGEST = "f440c984d5c797c47ddad240250e7a665b422f0ebc6918971fb208dee470b192"


@override_settings(SECRET_KEY=PINNED_KEY)
def test_an_identity_digest_issued_before_the_shared_digest_function_still_matches() -> None:
    from angee.parties.managers import IdentityBasis

    assert IdentityBasis(party=None, current={}, complete=PINNED_BASIS).facts_hash == PINNED_IDENTITY_DIGEST


@override_settings(SECRET_KEY=PINNED_KEY)
def test_an_identity_digest_and_a_duplicate_set_digest_never_stand_for_each_other(db: None) -> None:
    """Each purpose keys its own digest: over the same facts they differ, and each is refused where the other is due."""

    del db
    from angee.parties.managers import DUPLICATE_SET_DIGEST, IDENTITY_BASIS_DIGEST, keyed_facts_digest

    assert keyed_facts_digest(IDENTITY_BASIS_DIGEST, PINNED_BASIS) == PINNED_IDENTITY_DIGEST
    assert keyed_facts_digest(DUPLICATE_SET_DIGEST, PINNED_BASIS) != PINNED_IDENTITY_DIGEST
    owner = create_user("duplicates-digest-purposes")
    pair = _people(owner, "Zoe Example", "Zoe Example", value="purpose@example.com")
    ids = [[person.public_id for person in pair]]
    (preview,) = Party.objects.preview_duplicates(actor=owner, sets=ids)
    identity = Party.objects.identity_basis(pair[0].public_id, actor=owner)
    keep = {"name_action": "keep", "address_action": "keep", "handle_action": "keep"}

    with actor_context(owner):
        replayed, _results = pair[0].with_actor(owner).apply_identity(
            expected_facts_hash=preview.digest, proposed={"name": "Zoe Example"}, choices=keep,
        )
    (outcome,) = Party.objects.merge_duplicates(actor=owner, sets=ids, digests=[identity.facts_hash])
    with actor_context(owner):
        matched, _results = pair[0].with_actor(owner).apply_identity(
            expected_facts_hash=identity.facts_hash, proposed={"name": "Zoe Example"}, choices=keep,
        )

    assert replayed == "conflict"
    assert (str(outcome.outcome), outcome.reason) == ("refused", Party.objects.CHANGED_REASON)
    assert matched != "conflict"


def _set_with(owner: Any, field: str, *values: Any, tag: str = "") -> list[Any]:
    """A set of people with one name, each stating ``values`` (``""`` or ``None``: left empty) for ``field``."""

    members = _people(owner, *("Zoe Example" for _ in values), value=f"{field}{tag}-{len(values)}@example.com")
    with system_context(reason="duplicates one-sided values"):
        for member, value in zip(members, values, strict=True):
            Person._base_manager.filter(pk=member.pk).update(**{field: value})
    return members


def _preview_of(actor: Any, members: list[Any]) -> Any:
    (preview,) = Party.objects.preview_duplicates(actor=actor, sets=[[member.public_id for member in members]])
    return preview


def _fields(rows: Any) -> dict[str, tuple[Any, ...]]:
    return {row.field: row.values for row in rows}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("notes", "Met at the fair.", id="a-party-field"),
        pytest.param("nickname", "Zo", id="a-person-field"),
    ],
)
def test_a_field_only_one_member_states_is_a_fill_and_no_disagreement(db: None, field: str, value: str) -> None:
    """Nothing is chosen and nothing is lost: the merge fills the survivor's empty field (``Party.merge_values``)."""

    del db
    owner = create_user(f"duplicates-one-sided-{field}")
    for tag, values in (("-survivor-empty", ("", value)), ("-other-empty", (value, ""))):
        preview = _preview_of(owner, _set_with(owner, field, *values, tag=tag))
        assert (preview.outcome, preview.disagreements) == (None, ())
        assert _fields(preview.fills) == {field: tuple(item or None for item in values)}


def test_two_stated_values_are_a_disagreement_that_keeps_every_members_value(db: None) -> None:
    del db
    owner = create_user("duplicates-two-stated")
    preview = _preview_of(owner, _set_with(owner, "nickname", "Zo", "Zed", ""))

    assert (_fields(preview.disagreements), preview.fills) == ({"nickname": ("Zo", "Zed", None)}, ())


def test_two_members_stating_one_value_and_a_third_leaving_it_empty_is_a_fill(db: None) -> None:
    del db
    owner = create_user("duplicates-one-value-and-empty")
    preview = _preview_of(owner, _set_with(owner, "nickname", "Zo", "Zo", ""))

    assert (preview.disagreements, _fields(preview.fills)) == ((), {"nickname": ("Zo", "Zo", None)})


def test_a_field_hidden_from_the_actor_on_any_member_is_neither_a_fill_nor_a_disagreement(db: None) -> None:
    """The party's editor holds none of the person's grants: the person's birthday is hidden from them."""

    del db
    owner, editor = create_user("duplicates-hidden-fill-owner"), create_user("duplicates-hidden-fill-editor")
    members = _set_with(owner, "birthday", date(1990, 1, 2), None)
    with system_context(reason="duplicates party editor"):
        write_relationships([
            RelationshipTuple(
                resource=to_object_ref(Party._base_manager.get(pk=member.pk)), relation="editor",
                subject=to_subject_ref(editor),
            )
            for member in members
        ])

    seen, owned = _preview_of(editor, members), _preview_of(owner, members)

    assert (seen.outcome, seen.disagreements, seen.fills) == (None, (), ())
    assert _fields(owned.fills) == {"birthday": ("1990-01-02", None)}


def test_the_digest_changes_when_a_fill_appears_or_changes(db: None) -> None:
    del db
    owner = create_user("duplicates-fill-digest")
    members = _set_with(owner, "nickname", "", "")
    plain = _preview_of(owner, members).digest

    def nickname(value: str) -> str:
        with system_context(reason="duplicates fill digest"):
            Person._base_manager.filter(pk=members[1].pk).update(nickname=value)
        return _preview_of(owner, members).digest

    appeared = nickname("Zo")
    changed = nickname("Zed")

    assert len({plain, appeared, changed}) == 3
