"""A question's readers are its read_granted audience; an outside audience joins there and reads nothing else."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision, DecisionRecord
from tests.conftest import Vault, create_platform_admin, create_user, vault_for


@pytest.fixture
def asked(db):
    requester, reviewer, outsider = (create_user(name) for name in ("rg-requester", "rg-reviewer", "rg-outsider"))
    subject = vault_for(requester, name="Seating plan")
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])

    def ask():
        return Decision.objects.ask(
            DecisionRequest(
                kind="plan_review",
                records=(subject,),
                assignees=(reviewer,),
                requester=requester,
                proposal=DecisionProposal(alternatives=[{"key": "approve", "label": "Approve", "outcome": "approved"}]),
            ),
            actor=requester,
        )

    return requester, reviewer, outsider, subject, ask(), ask()


@pytest.mark.parametrize(
    ("seat", "reads"), [("requester", True), ("reviewer", True), ("admin", True), ("outsider", False)]
)
def test_the_owner_audience_reads_through_read_granted(asked, seat, reads):
    requester, reviewer, outsider, _subject, question, _other = asked
    actor = {"requester": requester, "reviewer": reviewer, "outsider": outsider}.get(seat) or create_platform_admin(
        "rg-admin"
    )
    assert question.with_actor(actor).has_access("read_granted") is reads
    assert question.with_actor(actor).has_access("read") is reads


def test_an_outside_audience_arm_on_read_granted_reads_only_its_question(asked, composed_permissions):
    """A consumer fragment's arm on read_granted reaches read, the concern links and nothing else."""

    del composed_permissions
    _requester, _reviewer, outsider, subject, question, other = asked
    schema = Path(apps.get_app_config("decisions").rebac_schema)
    source = schema.read_text(encoding="utf-8")
    granted = "permission read_granted = ((assignee + requester) + admin->member)"
    assert source.count(granted) == 1
    schema.write_text(
        source.replace(
            granted,
            "relation outside_reader: auth/user\n"
            "    permission read_granted = (((assignee + requester) + admin->member) + outside_reader)",
        )
    )
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
    write_relationships([RelationshipTuple(to_object_ref(question), "outside_reader", to_subject_ref(outsider))])

    with actor_context(outsider):
        assert list(Decision.objects.values_list("pk", flat=True)) == [question.pk]
        assert list(DecisionRecord.objects.values_list("decision_id", flat=True)) == [question.pk]
        assert not Vault.objects.filter(pk=subject.pk).exists()
    assert not question.with_actor(outsider).has_access("act")
    assert not other.with_actor(outsider).has_access("read")
