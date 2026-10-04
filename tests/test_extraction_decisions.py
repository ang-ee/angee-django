"""Reviewed revisions compose the canonical decision owner and immutable retention."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import actor_context, system_context

from angee.base.identity import public_id_of
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
from tests.conftest import create_platform_admin
from tests.extraction_models import Extraction
from tests.mtidemo.models import MtiChild
from tests.test_extraction_models import evidence as evidence
from tests.test_storage import drive as drive


@pytest.fixture
def correction(evidence, request):
    retain, values = evidence
    return _correction(
        retain(),
        values,
        create_platform_admin("evidence-reviewer"),
        binding_overrides=getattr(request, "param", {}),
    )


def _correction(original, values, reviewer, *, subject=None, binding_overrides=None):
    """Admit and answer corrections through the same owners for every target shape."""
    binding, _parent = Extraction.objects.prepare_correction_binding(original, actor=values["actor"])
    if binding_overrides:
        from angee.extraction.contracts import CorrectionBinding, ExtractionRef

        raw = {**binding.payload(), **binding_overrides}
        binding = CorrectionBinding(
            ExtractionRef(original.lineage_id, raw["authority_extraction_id"], raw["authority_extraction_revision"]),
            ExtractionRef(
                original.lineage_id, raw["revision_parent_extraction_id"], raw["revision_parent_extraction_revision"]
            ),
        )
    decision = Decision.objects.ask(
        DecisionRequest(
            kind="correct-note",
            records=(values["target"] if subject is None else subject,),
            assignees=(reviewer,),
            proposal=DecisionProposal(
                alternatives=[{"key": "correct", "label": "Confirm correction", "outcome": "corrected"}]
            ),
            context=DecisionContext(
                references=(
                    DecisionRecordReference(
                        model=original._meta.label,
                        id=str(original.sqid),
                    ),
                )
            ),
        ),
        actor=values["actor"],
    )
    result = deepcopy(original.result)
    result["documents"][0]["title"] = "Reviewed"

    def revise(**changes):
        return Extraction.objects.revise_from_decision(
            decision.pk,
            **{
                "actor": values["actor"],
                "result": result,
                "binding": binding,
                "expected_action": "correct-note",
                "expected_choice": "correct",
                **changes,
            },
        )

    def resolve():
        return Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision, chosen=["correct"])

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
    assert revised.outcome["corrections"][-1]["decision_answered_by"] == public_id_of(reviewer)
    assert revise().pk == revised.pk
    source, retained_decision = Extraction.objects.reviewed_correction_authority(
        revised,
        actor=values["actor"],
        expected_action="correct-note",
        expected_choice="correct",
    )
    assert source.pk == original.pk and retained_decision.pk == decision.pk
    with actor_context(values["actor"]), pytest.raises(ValidationError, match="cannot be deleted"):
        decision.with_actor(values["actor"]).delete()


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
    with pytest.raises(ValidationError, match="another chosen alternative"):
        revise()
    resolve()
    with pytest.raises(ValidationError, match="another chosen alternative"):
        revise(expected_choice="unoffered")
    with pytest.raises(ValidationError, match="another chosen alternative"):
        revise(expected_action="another-kind")


def test_correction_retains_the_answer_and_rejects_invalid_schema(correction):
    original, _values, _decision, reviewer, revise, resolve = correction
    resolve()
    invalid = deepcopy(original.result)
    invalid["documents"][0]["title"] = 17
    with pytest.raises(ValidationError, match="retained schema"):
        revise(result=invalid)
    with system_context(reason="tests revoke correction resolver activity"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(is_active=False)
    assert revise().correction_decision_id == _decision.pk
    assert Extraction.objects.count() == 2


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
            expected_action="correct-note",
            expected_choice="correct",
        )


@pytest.mark.parametrize(
    "correction",
    [
        {"authority_extraction_revision": True},
        {"revision_parent_extraction_revision": 1.0},
        {"authority_extraction_id": 7},
        {"revision_parent_extraction_id": 7},
    ],
    indirect=True,
)
def test_correction_binding_rejects_coerced_ids_and_revisions(correction):
    _original, _values, _decision, _reviewer, revise, resolve = correction
    resolve()
    with pytest.raises(ValidationError, match="invalid correction binding"):
        revise()
    assert Extraction.objects.count() == 1
