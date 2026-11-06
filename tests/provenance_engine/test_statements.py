"""Typed assertions run on a plain Django host with host-owned principal policy."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from angee.provenance.context import changeset, current_changeset
from angee.provenance.engine.policy import Policy
from angee.provenance.engine.statements import SourceKind
from tests.provenance_engine.statements_app.models import Link, Member, Principal, Subject
from tests.provenance_engine.storage.models import Changeset

pytestmark = pytest.mark.django_db(transaction=True)


class PrincipalPolicy(Policy):
    def authorize(self, actor, action, instance):
        if isinstance(actor, Principal):
            if not actor.admitted:
                raise PermissionDenied("Source admission denied.")
            return
        super().authorize(actor, action, instance)


@pytest.fixture(autouse=True)
def statement_policy(settings):
    settings.PROVENANCE_POLICY = PrincipalPolicy


@pytest.fixture
def subject(actor):
    with changeset(actor="bootstrap"):
        return Subject.objects.create(label="Unasserted", detail="Keep this")


def test_native_typed_generation_and_subject_scopes(subject):
    model = subject.statements.model
    assert model._meta.get_field("label").max_length == 100
    assert model._meta.get_field("member").remote_field.model is Member
    assert model._meta.get_field("subject").remote_field.model is Subject
    assert model.history.model._meta.get_field("history_changeset").remote_field.model is Changeset
    assert model in subject.history_scopes()
    assert model._meta.db_table != subject.same_as.model._meta.db_table


def test_precedence_preferred_and_retained_successor(actor, subject):
    directory = Principal.objects.create(name="Directory")
    with changeset(actor=actor):
        initial = subject.statements.set(directory, aspect="identity", values={"label": "Directory"})
        preferred = subject.statements.confirm(aspect="identity", values={"label": "Person"})
        successor = subject.statements.set(directory, aspect="identity", values={"label": "Directory again"})
    subject.refresh_from_db()
    assert subject.label == "Person"
    assert subject.detail == "Keep this"
    initial.refresh_from_db()
    assert initial.superseded_id == successor.pk
    assert initial.history.count() >= 2
    assert subject.provenance("label").get().pk == preferred.pk
    assert subject.statements.count() == 3


def test_withdraw_and_no_value_are_retained(actor, subject):
    directory = Principal.objects.create(name="Directory")
    with changeset(actor=actor):
        row = subject.statements.set(directory, aspect="identity", values={"label": "Directory"})
        subject.statements.confirm(aspect="identity", values={"label": "Person"})
        assert subject.statements.withdraw(actor) == 1
        assert subject.label == "Directory"
        absent = subject.statements.dismiss(aspect="identity")
    subject.refresh_from_db()
    assert subject.label == ""
    assert absent.snak == "no_value"
    assert subject.statements.filter(pk=row.pk).exists()


def test_source_and_actor_admission_fail_closed(actor, subject, django_user_model):
    denied = Principal.objects.create(name="Denied", admitted=False)
    ordinary = django_user_model.objects.create_user(username="ordinary")
    with changeset(actor=actor), pytest.raises(PermissionDenied):
        subject.statements.set(denied, aspect="identity", values={"label": "Denied"})
    with changeset(actor=ordinary), pytest.raises(PermissionDenied):
        subject.statements.set(actor, aspect="identity", values={"label": "Denied actor"})
    assert not subject.statements.exists()


@pytest.mark.parametrize("source", ["opaque", Principal(name="unsaved")])
def test_unpersisted_or_opaque_principals_are_rejected(actor, subject, source):
    with changeset(actor=actor), pytest.raises(ValidationError):
        subject.statements.set(source, aspect="identity", values={"label": "Invalid"})


def test_preferred_source_restriction_and_invalid_values_are_atomic(actor, subject):
    directory = Principal.objects.create(name="Directory")
    with changeset(actor=actor), pytest.raises(ValidationError):
        subject.statements.set(directory, aspect="identity", values={"label": "Invalid"}, rank="preferred")
    with changeset(actor=actor), pytest.raises(ValidationError):
        subject.statements.set(aspect="identity", values={"label": "x" * 101})
    assert not subject.statements.exists()
    subject.refresh_from_db()
    assert subject.label == "Unasserted"


def test_authoritative_link_precedes_normal_person_but_not_preferred(actor, subject):
    system = Principal.objects.create(name="System", kind=SourceKind.SYSTEM_OF_RECORD)
    link = Link.objects.create(is_authoritative=True)
    with changeset(actor=actor):
        subject.statements.set(aspect="identity", values={"label": "Person"})
        subject.statements.set(system, aspect="identity", values={"label": "System"}, link=link)
        assert subject.label == "System"
        subject.statements.confirm(aspect="identity", values={"label": "Preferred"})
    assert subject.label == "Preferred"


def test_member_identity_and_pair_symmetry_do_not_merge(actor, subject):
    member = Member.objects.create(name="Member")
    with changeset(actor=actor):
        other = Subject.objects.create(label="Other")
        row = subject.statements.set(aspect="membership", member=member)
        veto = other.same_as.for_pair(subject).dismiss()
    assert row.member_id == member.pk
    assert subject.same_as.kept_separate(subject, other)
    assert subject.same_as.kept_separate(other, subject)
    assert veto.subject_id == min(subject.pk, other.pk)
    assert Subject.objects.count() == 2


def test_public_mutation_cannot_bypass_owner(actor, subject):
    with changeset(actor=actor):
        row = subject.statements.set(aspect="identity", values={"label": "Retained"})
        for mutation in (
            lambda: subject.statements.update(status="withdrawn"),
            lambda: subject.statements.delete(),
            lambda: row.save(),
            lambda: row.delete(),
            lambda: subject.statements.bulk_create([]),
        ):
            with pytest.raises(ValidationError):
                mutation()


def test_revert_assertion_restores_subject_and_inverse(actor, subject):
    with changeset(actor=actor):
        subject.statements.set(aspect="identity", values={"label": "First"})
    with changeset(actor=actor, fresh=True):
        subject.statements.set(aspect="identity", values={"label": "Second"})
        receipt = current_changeset()
    inverse = receipt.revert(actor=actor)
    subject.refresh_from_db()
    assert subject.label == "First"
    assert subject.provenance("label").get().label == "First"
    inverse.revert(actor=actor)
    subject.refresh_from_db()
    assert subject.label == "Second"


def test_classification_uses_persisted_principal_fact(actor, subject):
    source = Principal.objects.create(name="Directory")
    source.kind = SourceKind.PERSON
    with changeset(actor=actor), pytest.raises(ValidationError):
        subject.statements.set(source, aspect="identity", values={"label": "Forged"}, rank="preferred")
    assert not subject.statements.exists()


def test_unknown_stance_preserves_value_and_seed_may_prefer(actor, subject):
    seed = Principal.objects.create(name="Bootstrap", kind=SourceKind.SEED)
    with changeset(actor=actor):
        unknown = subject.statements.set(seed, aspect="identity", rank="preferred", snak="some_value")
        assert subject.label == "Unasserted"
        assert unknown.applied
        asserted = subject.statements.set(seed, aspect="identity", rank="preferred", values={"label": "Seed"})
    assert asserted.is_confirmed
    assert subject.statements.provenance("label").get().pk == asserted.pk


def test_initial_assertion_revert_withdraws_and_preserves_unasserted_value(actor, subject):
    with changeset(actor=actor, fresh=True):
        row = subject.statements.set(aspect="identity", values={"label": "Changed"})
        receipt = current_changeset()
    receipt.revert(actor=actor)
    row.refresh_from_db()
    subject.refresh_from_db()
    assert row.status == "withdrawn"
    assert subject.label == "Unasserted"
    assert not subject.provenance("label").exists()


def test_evidence_facts_and_references_follow_host_read_policy(actor, subject, django_user_model):
    from angee.provenance.evidence import (
        RetainedEvidenceFact as EvidenceFact,
    )
    from angee.provenance.evidence import (
        RetainedEvidenceReference as EvidenceReference,
    )

    reference = EvidenceReference.from_record(subject)
    with changeset(actor=actor):
        row = subject.statements.set(
            aspect="identity",
            values={"label": "Evidence"},
            evidence=[reference, EvidenceFact(value={"score": 0.8}, source=reference)],
        )
    assert row.evidence[0]["reference"]["model"] == "statements_test.subject"
    assert reference.resolve(actor=actor).pk == subject.pk
    reader = django_user_model.objects.create_user(username="unprivileged")
    with pytest.raises(PermissionDenied):
        reference.resolve(actor=reader)
    with changeset(actor=actor), pytest.raises(ValidationError):
        subject.statements.set(aspect="identity", evidence=[object()])


@pytest.mark.postgresql
def test_concurrent_sources_serialize_selection_on_subject(actor, subject):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.contrib.auth import get_user_model
    from django.db import close_old_connections

    first = Principal.objects.create(name="First")
    second = Principal.objects.create(name="Second")
    barrier = Barrier(2)

    def assert_value(source_id, value):
        close_old_connections()
        try:
            principal = Principal.objects.get(pk=source_id)
            user = get_user_model().objects.get(pk=actor.pk)
            target = Subject.objects.get(pk=subject.pk)
            barrier.wait(timeout=10)
            with changeset(actor=user, fresh=True):
                target.statements.set(principal, aspect="identity", values={"label": value})
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(assert_value, first.pk, "First"),
            pool.submit(assert_value, second.pk, "Second"),
        ]
        for future in futures:
            future.result(timeout=20)
    assert subject.statements.current().count() == 2
    assert subject.provenance("label").count() == 1
    subject.refresh_from_db()
    assert subject.label == subject.provenance("label").get().label


def test_person_creation_and_edit_are_normal_assertions(actor):
    directory = Principal.objects.create(name="Directory")
    with changeset(actor=actor):
        target = Subject.objects.create(label="Person", detail="Original")
        target.detail = "Unsaved projection"
        target.label = "Person edit"
        target.save(update_fields=["label"])
        target.statements.set(directory, aspect="identity", values={"label": "Import"})
    target.refresh_from_db()
    assert target.label == "Person edit"
    assert target.detail == "Original"
    assert target.provenance("label").get().rank == "normal"
    assert target.provenance("label").get().asserted_by.endswith(str(actor.pk))
    assert target.statements.filter(aspect="detail").count() == 1


def test_replay_suppresses_automatic_person_assertions(actor, subject):
    with changeset(actor=actor):
        subject.label = "First edit"
        subject.save()
    with changeset(actor=actor, fresh=True):
        subject.label = "Second edit"
        subject.save()
        receipt = current_changeset()
    count = subject.statements.count()
    receipt.revert(actor=actor)
    subject.refresh_from_db()
    assert subject.label == "First edit"
    assert subject.statements.count() == count


def test_projection_context_suppresses_only_automatic_assertions(actor, subject):
    from angee.provenance.engine.statements import statement_application

    with changeset(actor=actor), statement_application():
        subject.label = "Computed"
        subject.save()
        with pytest.raises(ValidationError):
            subject.statements.update(status="withdrawn")
    assert not subject.statements.exists()


def test_created_subject_uses_host_retained_removal_and_inverse(actor):
    from tests.provenance_engine.statements_app.models import RetainedSubject

    with changeset(actor=actor, fresh=True):
        target = RetainedSubject.objects.create(label="Created")
        receipt = current_changeset()
    assertion = target.statements.get()
    inverse = receipt.revert(actor=actor)
    target.refresh_from_db()
    assertion.refresh_from_db()
    assert target.retired
    assert assertion.status == "withdrawn"
    inverse.revert(actor=actor)
    target.refresh_from_db()
    assertion.refresh_from_db()
    assert not target.retired
    assert target.label == "Created"
    assert assertion.status == "asserted"
    assert assertion.applied
    assert target.statements.count() == 1


def test_subject_capture_is_optional_while_assertions_keep_history(actor):
    from simple_history.exceptions import NotHistoricalModelError
    from simple_history.utils import get_history_manager_for_model

    from tests.provenance_engine.statements_app.models import StatementsOnly

    with changeset(actor=actor, fresh=True):
        target = StatementsOnly.objects.create(label="First")
    with pytest.raises(NotHistoricalModelError):
        get_history_manager_for_model(StatementsOnly)
    assert list(target.history_scopes()) == [target.statements.model]
    with changeset(actor=actor, fresh=True):
        target.statements.set(aspect="identity", values={"label": "Second"})
        receipt = current_changeset()
    receipt.revert(actor=actor)
    target.refresh_from_db()
    assert target.label == "First"
    assert target.statements.model.history.count() > 0


def test_default_creation_revert_refuses_retained_subject_deletion_atomically(actor):
    from django.db.models.deletion import ProtectedError

    with changeset(actor=actor, fresh=True):
        target = Subject.objects.create(label="Keep evidence")
        receipt = current_changeset()
    with pytest.raises(ProtectedError):
        receipt.revert(actor=actor)
    assert Subject.objects.filter(pk=target.pk).exists()
    assert target.statements.get().status == "asserted"


def test_pair_history_scopes_include_both_endpoints_and_replay(actor, subject):
    with changeset(actor="bootstrap"):
        other = Subject.objects.create(label="Other")
    with changeset(actor=actor, fresh=True):
        veto = other.same_as.for_pair(subject).dismiss()
        receipt = current_changeset()
    assert Changeset.objects.touching(subject).filter(pk=receipt.pk).exists()
    assert Changeset.objects.touching(other).filter(pk=receipt.pk).exists()
    inverse = receipt.revert(actor=actor)
    veto.refresh_from_db()
    assert veto.status == "withdrawn"
    assert not subject.same_as.kept_separate(subject, other)
    inverse.revert(actor=actor)
    assert subject.same_as.kept_separate(subject, other)


def test_identical_retry_is_a_no_op_including_history_and_receipt(actor, subject):
    with changeset(actor=actor, fresh=True):
        initial = subject.statements.set(aspect="identity", values={"label": "Same"}, evidence=[{"fact": "retained"}])
    histories = initial.history.count()
    receipts = Changeset.objects.count()
    with changeset(actor=actor, fresh=True):
        repeated = subject.statements.set(aspect="identity", values={"label": "Same"}, evidence=[{"fact": "retained"}])
    assert repeated.pk == initial.pk
    assert initial.history.count() == histories
    assert Changeset.objects.count() == receipts
    with changeset(actor=actor, fresh=True):
        changed = subject.statements.set(aspect="identity", values={"label": "Same"}, evidence=[{"fact": "changed"}])
    assert changed.pk != initial.pk


def test_normal_unknown_does_not_outrank_value_and_last_withdrawal_clears_owned_group(actor, subject):
    directory = Principal.objects.create(name="Directory")
    with changeset(actor=actor):
        subject.statements.set(directory, aspect="identity", values={"label": "Known"})
        subject.statements.set(aspect="identity", snak="some_value")
        assert subject.label == "Known"
        subject.statements.withdraw(directory)
        assert subject.label == "Known"  # A still-asserted unknown never erases a known value.
        subject.statements.withdraw(actor)
    subject.refresh_from_db()
    assert subject.label == ""
    assert subject.detail == "Keep this"  # This group has never had assertions.


def test_native_to_field_references_and_no_value_defaults(actor):
    from tests.provenance_engine.statements_app.models import Code, TypedReference

    code = Code.objects.create(code="human-key")
    with changeset(actor=actor):
        target = TypedReference.objects.create(code=code)
        assert target.statements.provenance("code").get().code_id == "human-key"
        row = target.statements.set(aspect="member", member=code)
        assert row.member_id == "human-key"
        target.statements.dismiss(aspect="count")
    target.refresh_from_db()
    assert target.count == 7


def test_withdraw_unsupported_selects_only_current_assertions_of_its_source(actor, subject, settings):
    source = Principal.objects.create(name="Limited")
    other_source = Principal.objects.create(name="Other")
    with changeset(actor="bootstrap"):
        unrelated = Subject.objects.create(label="Other")
    with changeset(actor=actor):
        owned = subject.statements.set(source, aspect="identity", values={"label": "Owned"})
        other = unrelated.statements.set(other_source, aspect="identity", values={"label": "Unrelated"})

    class ScopedPolicy(PrincipalPolicy):
        def authorize(self, principal, action, instance):
            if isinstance(principal, Principal) and principal.pk == source.pk and instance.pk == unrelated.pk:
                raise PermissionDenied("Source cannot write unrelated subject.")
            super().authorize(principal, action, instance)

    settings.PROVENANCE_POLICY = ScopedPolicy
    with changeset(actor=actor):
        assert subject.statements.model.objects.withdraw_unsupported(source, []) == 1
    owned.refresh_from_db()
    other.refresh_from_db()
    assert owned.status == "withdrawn"
    assert other.status == "asserted"


@pytest.mark.parametrize("unsupported", [False, True])
def test_collection_withdrawal_rolls_back_all_subjects_on_later_denial(actor, subject, settings, unsupported):
    source = Principal.objects.create(name="Limited")
    with changeset(actor="bootstrap"):
        denied_subject = Subject.objects.create(label="Later")
    with changeset(actor=actor):
        first = subject.statements.set(source, aspect="identity", values={"label": "First"})
        second = denied_subject.statements.set(source, aspect="identity", values={"label": "Second"})
    history_count = subject.statements.model.history.count()

    class ScopedPolicy(PrincipalPolicy):
        def authorize(self, principal, action, instance):
            if isinstance(principal, Principal) and principal.pk == source.pk and instance.pk == denied_subject.pk:
                raise PermissionDenied("Source cannot write later affected subject.")
            super().authorize(principal, action, instance)

    settings.PROVENANCE_POLICY = ScopedPolicy
    manager = subject.statements.model.objects
    with changeset(actor=actor), pytest.raises(PermissionDenied):
        if unsupported:
            manager.withdraw_unsupported(source, [])
        else:
            manager.withdraw(source, subjects=[subject, denied_subject])
    first.refresh_from_db()
    second.refresh_from_db()
    subject.refresh_from_db()
    assert first.status == second.status == "asserted"
    assert subject.label == "First"
    assert subject.statements.model.history.count() == history_count


@pytest.mark.parametrize("unsupported", [False, True])
def test_empty_collection_withdrawal_still_requires_persisted_source(actor, subject, unsupported):
    source = Principal(name="Not persisted")
    manager = subject.statements.model.objects
    with changeset(actor=actor), pytest.raises(ValidationError):
        if unsupported:
            manager.withdraw_unsupported(source, [])
        else:
            manager.withdraw(source, subjects=[])


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
@pytest.mark.parametrize("using", ["default", "other"])
@pytest.mark.parametrize("representation", ["field-value", "field-instance", "column-value"])
def test_shared_write_engine_canonicalizes_reference_content_for_host_storage(
    using,
    representation,
):
    from angee.provenance.context import actor_reference
    from angee.provenance.engine.statements import StatementManager
    from angee.provenance.querysets import model_history
    from tests.provenance_engine.statements_app.models import Code, TypedReference

    class CurrentRowManager(StatementManager):
        stored_content = None

        def _store_assertion(self, row, previous, *, content, using):
            self.stored_content = dict(content)
            return self.model._base_manager.db_manager(using).update_or_create(
                subject=row.subject,
                asserted_by=row.asserted_by,
                aspect=row.aspect,
                superseded=None,
                defaults=content,
            )[0]

    person = Principal.objects.using(using).create(name="Editor", kind=SourceKind.PERSON)
    first = Code.objects.using(using).create(code="first-key")
    later = Code.objects.using(using).create(code="later-key")
    with changeset(actor=person):
        # The ordinary save supplies ForeignKey.value_from_object() to statements.set().
        target = TypedReference.objects.using(using).create(code=first)
        assert target.statements.db_manager(using).provenance("code").get().code_id == first.code
        manager = CurrentRowManager(target.statements.model, target).db_manager(using)
        definition = manager.model.statement_aspects["reference"]
        key = "code_id" if representation == "column-value" else "code"
        value = later if representation == "field-instance" else later.code
        content = {key: value, "source_kind": SourceKind.PERSON}
        row = manager._write_assertion(
            target,
            actor_reference(person),
            definition,
            {"aspect": "reference"},
            content,
            using=using,
        )
        assert manager.stored_content == {
            "code_id": later.code,
            "source_kind": SourceKind.PERSON,
        }
        assert content == {key: value, "source_kind": SourceKind.PERSON}
        target.refresh_from_db(using=using)
        assert target.code_id == later.code
        assert row.code_id == later.code
        assert row._state.db == using
        history_using = model_history(type(row)).destination(type(row), using)
        history_count = row.history.using(history_using).count()
        assert history_count >= 2
        same = manager._write_assertion(
            target,
            actor_reference(person),
            definition,
            {"aspect": "reference"},
            content,
            using=using,
        )
        assert same.pk == row.pk
        assert same.history.using(history_using).count() == history_count

        # Existing public instance/scalar/null paths share the same normalization.
        instance = target.statements.db_manager(using).set(aspect="reference", values={"code": first})
        assert instance.code_id == first.code
        count = target.statements.db_manager(using).set(aspect="count", values={"count": "8"})
        assert count.count == 8
        target.code = None
        target.save(using=using, update_fields=["code"])
        target.refresh_from_db(using=using)
        assert target.code is None
        assert target.statements.db_manager(using).provenance("code").get().code_id is None


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
@pytest.mark.parametrize("invalid", ["missing", "unsaved", "wrong-database"])
def test_shared_write_engine_rejects_invalid_references_before_host_storage(invalid):
    from angee.provenance.context import actor_reference
    from tests.provenance_engine.statements_app.models import Code, TypedReference

    person = Principal.objects.create(name="Editor", kind=SourceKind.PERSON)
    with changeset(actor="bootstrap"):
        target = TypedReference.objects.create()
    manager = target.statements
    if invalid == "missing":
        value = "missing-key"
    elif invalid == "unsaved":
        value = Code(code="unsaved-key")
    else:
        value = Code.objects.using("other").create(code="other-key")
    with changeset(actor=person), pytest.raises(ValidationError):
        manager._write_assertion(
            target,
            actor_reference(person),
            manager.model.statement_aspects["reference"],
            {"aspect": "reference"},
            {"code": value, "source_kind": SourceKind.PERSON},
            using="default",
        )
    assert not manager.exists()
