"""Reviewed revisions compose the canonical decision owner and immutable retention."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from rebac import actor_context, system_context

from angee.base.identity import public_id_of
from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.decisions.testing.models import Decision
from tests.conftest import create_platform_admin
from tests.extraction_models import Extraction
from tests.mtidemo.models import MtiChild
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


def test_extraction_rejects_targets_outside_file_and_message(evidence):
    """Explicit target fields cannot reinterpret an unrelated row with the same PK."""
    retain, values = evidence
    with system_context(reason="tests extraction unsupported target"):
        child = MtiChild.objects.create(pk=values["target"].pk, title="Unrelated target")
    with pytest.raises(ValidationError, match="file or message"):
        retain(target=child)
    assert Extraction.objects.count() == 0


def test_decision_correction_is_exact_reusable_and_retains_its_authority(correction):
    original, values, decision, reviewer, revise, resolve = correction
    resolve()
    revised = revise()
    assert revised.result["documents"][0]["title"] == "Reviewed"
    assert original.result["documents"][0]["title"] == "Note"
    assert revised.document_refs == original.document_refs
    assert revised.fact_authority("/documents/0/title") == "correction"
    assert revised.fact_correction("/documents/0/title").decision_id == public_id_of(decision)
    assert revised.claims == {}
    assert revised.correction_decision_id == decision.pk
    assert revised.outcome["corrections"][-1]["decision_resolved_by"] == public_id_of(reviewer)
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
        type(target).objects.filter(pk=target.pk).update(owner=owner)
        type(target.drive).objects.filter(pk=target.drive_id).update(owner=owner)
    with actor_context(owner):
        target.with_actor(owner).grant_record_access("viewer", actor)
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
