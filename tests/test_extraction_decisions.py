"""Reviewed revisions compose the canonical decision owner and immutable retention."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from rebac import (
    RelationshipTuple,
    actor_context,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.identity import public_id_of
from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from tests.conftest import create_platform_admin, create_user
from tests.decisions_models import Decision
from tests.extraction_models import Extraction
from tests.mtidemo.models import MtiChild, MtiParent
from tests.test_extraction_models import evidence as evidence
from tests.test_storage import drive as drive


class CorrectNote(Action, key="correct", label="Correct note", verdict=Verdict.COMPLETED):
    """The submitted correction is interpreted by its consumer."""

    title: str


@pytest.fixture
def correction(evidence, request):
    retain, values = evidence
    return _correction(
        retain(), values, create_platform_admin("evidence-reviewer"),
        binding_overrides=getattr(request, "param", {}),
    )


def _correction(original, values, reviewer, *, subject=None, binding_overrides=None):
    """Admit and answer corrections through the same owners for every target shape."""
    binding, _parent = Extraction.objects.prepare_correction_binding(original, actor=values["actor"])
    raw_binding = {**binding.payload(), **(binding_overrides or {})}
    group = Decision.objects.admit_group(
        [
            DecisionRequest(
                kind="correct-note",
                subject=values["target"] if subject is None else subject,
                assignees=(reviewer,),
                actions=(CorrectNote,),
                basis={
                    "extraction_id": str(original.sqid),
                    "extraction_revision": original.revision,
                    "correction_binding": raw_binding,
                },
                context=DecisionContext(
                    references=(
                        DecisionRecordReference(
                            model=original._meta.label,
                            id=str(original.sqid),
                        ),
                    )
                ),
            )
        ],
        actor=values["actor"],
    )
    with system_context(reason="tests extraction decision seat"):
        decision = Decision.objects.get(group=group)
    result = deepcopy(original.result)
    result["documents"][0]["title"] = "Reviewed"

    def revise(**changes):
        return Extraction.objects.revise_from_decision(
            decision.pk,
            **{
                "actor": values["actor"],
                "result": result,
                "actions": (CorrectNote,),
                "expected_action": "correct-note",
                "expected_resolution_action": "correct",
                **changes,
            },
        )

    def resolve():
        return Decision.objects.decide(
            decision.pk, actor=reviewer, revision=decision.revision, action="correct", values={"title": "Reviewed"}
        )

    return original, values, decision, reviewer, revise, resolve


@pytest.fixture
def mti_correction(evidence, request):
    """Keep a concrete target while admitting a canonical subject for a plain reviewer."""
    retain, values = evidence
    actor = create_platform_admin("mti-evidence-author")
    reviewer = create_user("mti-evidence-reviewer")
    source = values["target"]
    with system_context(reason="tests extraction concrete target setup"):
        child = MtiChild.objects.create(pk=source.pk, title="Shared target", detail="Concrete target")
        parent = MtiParent.objects.get(pk=child.pk)
        subject = parent
        kind = getattr(request, "param", "parent")
        if kind == "other_parent":
            subject = MtiChild.objects.create(title="Unrelated target").mtiparent_ptr
        elif kind == "source":
            subject = source
    with actor_context(actor):
        original = retain(target=child, actor=actor)
        original.grant_record_access("viewer", reviewer)
        source.grant_record_access("viewer", reviewer)
        write_relationships([
            RelationshipTuple(resource=to_object_ref(record), relation="reader", subject=to_subject_ref(reviewer))
            for record in (parent, child, *([subject] if kind == "other_parent" else []))
        ])
        yield _correction(original, {**values, "actor": actor, "target": child}, reviewer, subject=subject)


def test_mti_correction_matches_parent_and_preserves_concrete_target(mti_correction):
    original, values, decision, reviewer, revise, resolve = mti_correction
    child = values["target"]
    parent = MtiParent.objects.get(pk=child.pk)
    assert decision.subject_content_type.model_class() is MtiParent
    assert str(decision.subject_object_id) == str(parent.pk)
    resolve()
    revised = revise()
    assert revised.result["documents"][0]["title"] == "Reviewed"
    assert revise().pk == revised.pk
    for row in (original, revised):
        assert row.content_type.model_class() is MtiChild
        assert str(row.object_id) == str(child.pk)
        assert isinstance(row.target, MtiChild)
        row.require_target(child)
        with pytest.raises(ValidationError, match="different target"):
            row.require_target(parent)
    authority = {
        "actor": values["actor"], "actions": (CorrectNote,),
        "expected_action": "correct-note", "expected_resolution_action": "correct",
    }
    source, retained_decision = Extraction.objects.reviewed_correction_authority(revised, **authority)
    assert source.pk == original.pk and retained_decision.pk == decision.pk
    delete_relationship(RelationshipTuple(
        resource=to_object_ref(child), relation="reader", subject=to_subject_ref(reviewer),
    ))
    assert parent.with_actor(reviewer).has_access("read")
    assert not child.with_actor(reviewer).has_access("read")
    with pytest.raises(PermissionDenied, match="target must remain readable"):
        Extraction.objects.reviewed_correction_authority(revised, **authority)


@pytest.mark.parametrize("mti_correction", ["other_parent", "source"], indirect=True)
def test_mti_correction_rejects_unrelated_subject(mti_correction):
    original, values, decision, _reviewer, revise, resolve = mti_correction
    if decision.subject_content_type.model_class() is MtiParent:
        assert str(decision.subject_object_id) != str(values["target"].pk)
    else:
        assert str(decision.subject_object_id) == str(values["target"].pk)
    resolve()
    with pytest.raises(ValidationError, match="another target"):
        revise()
    assert Extraction.objects.get().pk == original.pk


@pytest.mark.parametrize("principal", ["actor", "resolver"])
def test_mti_correction_requires_concrete_access(mti_correction, principal):
    original, values, decision, reviewer, revise, resolve = mti_correction
    resolve()
    child = values["target"]
    parent = MtiParent.objects.get(pk=child.pk)
    grant = RelationshipTuple(resource=to_object_ref(child), relation="reader", subject=to_subject_ref(reviewer))
    delete_relationship(grant)
    assert parent.with_actor(reviewer).has_access("read")
    assert original.with_actor(reviewer).has_access("read")
    assert original.parts.get().with_actor(reviewer).has_access("read")
    assert not child.with_actor(reviewer).has_access("read")
    actor = reviewer if principal == "actor" else values["actor"]
    assert Decision.objects.resolution(decision.pk, actor=actor, actions=(CorrectNote,)).resolver.pk == reviewer.pk
    with pytest.raises(PermissionDenied, match="target must remain readable"):
        revise(actor=actor)
    assert Extraction.objects.count() == 1
    write_relationships([grant])
    assert child.with_actor(reviewer).has_access("read")
    assert not child.with_actor(reviewer).has_access("write")
    with pytest.raises(PermissionDenied, match="Write access"):
        revise(actor=reviewer)
    assert Extraction.objects.count() == 1
    assert revise().revision == 2


def test_decision_correction_is_exact_reusable_and_retains_its_authority(correction):
    original, values, decision, reviewer, revise, resolve = correction
    resolve()
    revised = revise()
    assert revised.result["documents"][0]["title"] == "Reviewed"
    assert original.result["documents"][0]["title"] == "Note"
    assert revised.document_refs == original.document_refs
    assert revised.fact_authority("/documents/0/title").kind == "correction"
    assert revised.claims == {}
    assert revised.correction_decision_id == decision.pk
    assert revised.provenance["corrections"][-1]["decision_resolved_by"] == public_id_of(reviewer)
    assert revise().pk == revised.pk
    source, retained_decision = Extraction.objects.reviewed_correction_authority(
        revised,
        actor=values["actor"],
        actions=(CorrectNote,),
        expected_action="correct-note",
        expected_resolution_action="correct",
    )
    assert source.pk == original.pk and retained_decision.pk == decision.pk
    with actor_context(values["actor"]), pytest.raises(ProtectedError):
        decision.group.delete()


def test_correction_requires_target_write_even_when_evidence_stays_readable(correction):
    original, values, _decision, _reviewer, revise, resolve = correction
    resolve()
    target, actor = values["target"], values["actor"]
    owner = create_platform_admin("new-target-owner")
    with system_context(reason="tests extraction revoke target write"):
        type(target).objects.filter(pk=target.pk).update(created_by=owner)
        type(target.drive).objects.filter(pk=target.drive_id).update(created_by=owner)
    with actor_context(owner):
        target.grant_record_access("viewer", actor)
    assert target.with_actor(actor).has_access("read")
    assert not target.with_actor(actor).has_access("write")
    assert original.with_actor(actor).has_access("read")
    with pytest.raises(PermissionDenied, match="Write access"):
        revise()
    assert Extraction.objects.count() == 1


def test_correction_requires_settlement_and_the_exact_declared_action(correction):
    _original, _values, _decision, _reviewer, revise, resolve = correction
    with pytest.raises(ValidationError, match="still open"):
        revise()
    resolve()
    with pytest.raises(ValidationError, match="another resolution action"):
        revise(expected_resolution_action="unoffered")
    with pytest.raises(ValidationError, match="another resolution action"):
        revise(expected_action="another-kind")


def test_correction_revalidates_the_resolver_and_rejects_invalid_schema(correction):
    original, _values, _decision, reviewer, revise, resolve = correction
    resolve()
    invalid = deepcopy(original.result)
    invalid["documents"][0]["title"] = 17
    with pytest.raises(ValidationError, match="retained schema"):
        revise(result=invalid)
    with system_context(reason="tests revoke correction resolver activity"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        revise()
    assert Extraction.objects.count() == 1


def test_correction_nul_fails_without_retaining_a_successor(correction):
    original, _values, _decision, _reviewer, revise, resolve = correction
    resolve()
    invalid = deepcopy(original.result)
    invalid["documents"][0]["title"] = "Rejected\x00text"
    with pytest.raises(ValidationError, match="null characters"):
        revise(result=invalid)
    assert Extraction.objects.count() == 1


def test_correction_rejects_conflicting_replay_and_indirect_successor(correction, evidence):
    _original, _values, _decision, _reviewer, revise, resolve = correction
    retain, _values = evidence
    resolve()
    revised = revise()
    changed = deepcopy(revised.result)
    changed["documents"][0]["title"] = "Different"
    with pytest.raises(ValidationError, match="different evidence"):
        revise(result=changed)
    later = retain(
        base=revised,
        result=replace(
            _values["result"],
            value=revised.result,
        ),
        request_key="after-correction",
    )
    assert revise().pk == revised.pk
    with pytest.raises(ValidationError, match="direct retained successor"):
        Extraction.objects.reviewed_correction_authority(
            later,
            actor=_values["actor"],
            actions=(CorrectNote,),
            expected_action="correct-note",
            expected_resolution_action="correct",
        )


@pytest.mark.parametrize("correction", [
    {"authority_extraction_revision": True},
    {"revision_parent_extraction_revision": 1.0},
    {"authority_extraction_id": 7},
    {"revision_parent_extraction_id": 7},
], indirect=True)
def test_correction_binding_rejects_coerced_ids_and_revisions(correction):
    _original, _values, _decision, _reviewer, revise, resolve = correction
    resolve()
    with pytest.raises(ValidationError, match="invalid correction binding"):
        revise()
    assert Extraction.objects.count() == 1
