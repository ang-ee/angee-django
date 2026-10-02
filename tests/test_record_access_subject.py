"""C45: every base holder grant validates the subject before writing."""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import SubjectRef, actor_context, system_context, to_subject_ref
from rebac.backends import reset_backend
from rebac.errors import PermissionDenied
from rebac.models import PermissionAuditEvent, active_relationship_model

from angee.base.errors import DomainError, RecordAccessSubjectRefused
from tests.conftest import create_user
from tests.core_persistence import OwnedRow, ownership_tables  # noqa: F401
from tests.core_seam_models import SubjectGuardedRow, subject_tables  # noqa: F401


def _non_audit_writes(queries):
    """Permission bypass receipts are allowed; record, history and grant writes are not."""

    audit_insert = f"INSERT INTO {connection.ops.quote_name(PermissionAuditEvent._meta.db_table)} "
    return [
        query["sql"] for query in queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        and not query["sql"].lstrip().startswith(audit_insert)
    ]


@pytest.fixture(params=["denormalized", "registry"])
def relationship_store(request, settings):
    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def grant_case(relationship_store, subject_tables):  # noqa: F811 -- imported shared fixture
    owner = create_user("grant-owner")
    recipient = create_user("grant-recipient")
    with actor_context(owner):
        row = SubjectGuardedRow.objects.create(id=11, child_id=71)
    return row, owner, recipient


def test_subject_refusal_has_one_stable_domain_and_validation_code():
    error = RecordAccessSubjectRefused()
    assert isinstance(error, DomainError)
    assert isinstance(error, ValidationError)
    assert error.code == "RECORD_ACCESS_SUBJECT_REFUSED"
    assert error.messages == ["RECORD_ACCESS_SUBJECT_REFUSED"]
    assert error.error_list == [error]
    assert error.error_list[0].code == error.code


@pytest.mark.parametrize("subject", [
    SubjectRef.of("auth/user", "17"),
    SubjectRef.of("auth/group", "29", "member"),
    SubjectRef.of("auth/user", "*"),
])
def test_default_subject_hook_accepts_subjects_without_queries(subject):
    # No db fixture: even an unsaved row can ask this default validation hook.
    assert OwnedRow().validate_record_access_subject("reader", subject) is None


@pytest.mark.parametrize("relation", ["reader", "editor"])
@pytest.mark.parametrize("entrypoint", ["public", "declared"])
@pytest.mark.parametrize("subject_kind", ["model", "reference", "group", "wildcard"])
def test_refused_grant_writes_no_relationship_or_row(grant_case, relation, entrypoint, subject_kind):
    row, owner, recipient = grant_case
    subject = {
        "model": recipient,
        "reference": to_subject_ref(recipient),
        "group": SubjectRef.of("auth/group", "29", "member"),
        "wildcard": SubjectRef.of("auth/user", "*"),
    }[subject_kind]
    row.refused_subject = str(to_subject_ref(subject))
    row.with_actor(owner).save()
    history_before = list(OwnedRow.history.filter(id=row.id).values())
    with CaptureQueriesContext(connection) as queries, pytest.raises(RecordAccessSubjectRefused):
        if entrypoint == "public":
            row.grant_record_access(relation, subject)
        else:
            row._grant_declared_record_access(SubjectGuardedRow, relation, subject)
    assert not _non_audit_writes(queries)
    assert not active_relationship_model().objects.exists()
    assert list(OwnedRow.history.filter(id=row.id).values()) == history_before
    assert row.with_actor(owner).direct_record_access() == ()


@pytest.mark.parametrize("relation", ["reader", "editor"])
def test_allowed_grants_are_idempotent_but_revalidate_every_attempt(grant_case, relation, monkeypatch):
    row, owner, recipient = grant_case
    calls = []
    validate = SubjectGuardedRow.validate_record_access_subject

    def observe(self, relation, subject):
        calls.append((relation, subject, self.direct_record_access()))
        return validate(self, relation, subject)

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", observe)
    for _ in range(2):
        row.with_actor(owner).grant_record_access(relation, recipient)
    assert [call[:2] for call in calls] == [(relation, recipient), (relation, recipient)]
    assert calls[0][2] == ()
    assert len(calls[1][2]) == 1
    assert active_relationship_model().objects.count() == 1
    assert row.with_actor(recipient).has_access("read")

    row.refused_subject = str(to_subject_ref(recipient))
    row.with_actor(owner).save()
    with pytest.raises(RecordAccessSubjectRefused):
        row.grant_record_access(relation, recipient)
    assert len(calls) == 3
    assert active_relationship_model().objects.count() == 1


@pytest.mark.parametrize("entrypoint", ["public", "declared"])
def test_revoke_does_not_ask_subject_validation_even_if_holder_is_now_refused(grant_case, entrypoint, monkeypatch):
    row, owner, recipient = grant_case
    row.with_actor(owner).grant_record_access("reader", recipient)
    assert row.with_actor(recipient).has_access("read")

    def refuse(*args, **kwargs):
        pytest.fail("Revocation must not validate the departing holder.")

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", refuse)
    for _ in range(2):
        row.with_actor(owner)
        if entrypoint == "public":
            row.revoke_record_access("reader", recipient)
        else:
            row._revoke_declared_record_access(SubjectGuardedRow, "reader", recipient)
    assert not active_relationship_model().objects.exists()
    assert not row.with_actor(recipient).has_access("read")


@pytest.mark.parametrize("failure", ["target", "undeclared", "unauthorized"])
def test_invalid_or_unauthorized_grants_fail_before_subject_policy(grant_case, failure, monkeypatch):
    row, owner, recipient = grant_case

    def refuse_subject(*args, **kwargs):
        pytest.fail("Admission must precede subject validation.")

    def refuse_target(self):
        raise ValidationError("Target refuses sharing.")

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", refuse_subject)
    relation = "reader"
    expected = PermissionDenied
    row.with_actor(owner)
    if failure == "target":
        monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_target", refuse_target)
        expected = ValidationError
    elif failure == "undeclared":
        relation, expected = "owner", ValueError
    else:
        row.with_actor(recipient)
    with pytest.raises(expected):
        row.grant_record_access(relation, recipient)
    assert not active_relationship_model().objects.exists()


@pytest.mark.usefixtures("subject_tables")
@pytest.mark.parametrize("recipient_is_owner", [False, True])
def test_transfer_validates_the_locked_actor_bound_child_before_any_write(recipient_is_owner, monkeypatch):
    owner, recipient = create_user("owner"), create_user("recipient")
    recipient = owner if recipient_is_owner else recipient
    with actor_context(owner):
        stale = SubjectGuardedRow.objects.create(id=11, child_id=71)
        SubjectGuardedRow.objects.filter(pk=stale.pk).update(refused_subject=str(to_subject_ref(recipient)))
    calls = []
    validate = SubjectGuardedRow.validate_record_access_subject

    def observe(self, relation, subject):
        calls.append((self, self.actor(), relation, subject))
        return validate(self, relation, subject)

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", observe)
    history_before = list(OwnedRow.history.filter(id=stale.id).values())
    with CaptureQueriesContext(connection) as queries, pytest.raises(RecordAccessSubjectRefused):
        stale.with_actor(owner).transfer_ownership(recipient)
    [(locked, actor, relation, subject)] = calls
    assert locked is not stale
    assert (locked.pk, actor, relation, subject) == (stale.pk, to_subject_ref(owner), "owner", recipient)
    assert not _non_audit_writes(queries)
    assert list(OwnedRow.history.filter(id=stale.id).values()) == history_before
    with system_context(reason="test.subject.inspect"):
        stale.refresh_from_db()
    assert stale.owner_id == stale.created_by_id == owner.pk


@pytest.mark.usefixtures("subject_tables")
def test_unauthorized_transfer_is_refused_before_subject_policy(monkeypatch):
    owner, outsider = create_user("owner"), create_user("outsider")
    with actor_context(owner):
        row = SubjectGuardedRow.objects.create(id=11, child_id=71)

    def forbidden(*args, **kwargs):
        pytest.fail("Transfer authorization must precede holder validation.")

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", forbidden)
    with pytest.raises(PermissionDenied):
        row.with_actor(outsider).transfer_ownership(outsider)
    with system_context(reason="test.subject.inspect"):
        row.refresh_from_db()
    assert row.owner_id == row.created_by_id == owner.pk


@pytest.mark.usefixtures("subject_tables")
def test_accepted_same_owner_transfer_validates_once_without_saving(monkeypatch):
    owner = create_user("owner")
    with actor_context(owner):
        row = SubjectGuardedRow.objects.create(id=11, child_id=71)
    calls = []
    validate = SubjectGuardedRow.validate_record_access_subject

    def observe(self, relation, subject):
        calls.append((relation, subject))
        return validate(self, relation, subject)

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", observe)
    history_before = list(OwnedRow.history.filter(id=row.id).values())
    with CaptureQueriesContext(connection) as queries:
        assert row.with_actor(owner).transfer_ownership(owner) is row
    assert calls == [("owner", owner)]
    assert not _non_audit_writes(queries)
    assert list(OwnedRow.history.filter(id=row.id).values()) == history_before


@pytest.mark.usefixtures("subject_tables")
@pytest.mark.parametrize("operation", ["clear", "release"])
def test_removing_ownership_never_asks_subject_validation(operation, monkeypatch):
    owner = create_user("owner")
    with actor_context(owner):
        row = SubjectGuardedRow.objects.create(id=11, child_id=71)

    def refuse(*args, **kwargs):
        pytest.fail("Removing ownership must not validate a new holder.")

    monkeypatch.setattr(SubjectGuardedRow, "validate_record_access_subject", refuse)
    if operation == "clear":
        row.with_actor(owner).transfer_ownership(None)
    else:
        assert SubjectGuardedRow.objects.filter(pk=row.pk).release(owner) == 1
    with system_context(reason="test.subject.inspect"):
        row.refresh_from_db()
    assert (row.owner_id, row.created_by_id) == (None, owner.pk)
