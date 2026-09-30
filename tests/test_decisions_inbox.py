"""The inbox consumes permission and live-open facts owned by decisions."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.functions import Now
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionRequest
from angee.decisions.testing.drivers import Accept, Reject, seed_group
from tests.conftest import addon_schema, create_platform_admin, create_user, execute_schema, result_data, vault_for
from tests.decisions_models import Decision, DecisionGroup


@pytest.fixture
def inbox(composed_tables):
    """A non-issuer requester and separate assignee share one readable record."""
    issuer, requester, reviewer, outsider = (create_user(name) for name in (
        "inbox-issuer", "inbox-requester", "inbox-reviewer", "inbox-outsider",
    ))
    subject = vault_for(issuer, name="Shared reference")
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(person))
        for person in (requester, reviewer)
    ])
    group = seed_group(actor=issuer, assignees=(reviewer,), reference=subject, requester=requester)
    decision = system_queryset(Decision).get(group=group)
    return issuer, requester, reviewer, outsider, subject, group, decision


def query(user, text, variables=None):
    """Execute the public console resource as its real viewer."""
    schema = addon_schema(decision_schema.schemas, "console")
    return result_data(execute_schema(schema, text, variables, user=user))


def test_requester_can_read_own_requested_question_and_group_without_being_issuer(inbox):
    _issuer, requester, _reviewer, outsider, _subject, group, decision = inbox
    document = "query { decisions { id can_act is_open group { id } } }"
    assert query(requester, document) == {"decisions": [{
        "id": str(decision.sqid), "can_act": False, "is_open": True, "group": {"id": str(group.sqid)},
    }]}
    assert query(outsider, document) == {"decisions": []}


def test_kind_label_and_record_representation_share_the_model_owner(inbox):
    _issuer, _requester, reviewer, _outsider, _subject, _group, decision = inbox
    assert query(reviewer, "query { decisions { kind kind_label display_name } }") == {"decisions": [{
        "kind": decision.kind, "kind_label": decision.kind_label, "display_name": decision.kind_label,
    }]}
    decision.kind = "note_publication"
    assert decision.kind_label == str(decision) == "Note publication"


def test_group_and_evidence_display_names_use_public_identity(inbox):
    _issuer, _requester, reviewer, _outsider, _subject, group, _decision = inbox
    result = query(reviewer, "{ decision_groups { display_name } decision_evidence { id display_name } }")
    assert result["decision_groups"] == [{"display_name": f"Decision group {group.sqid}"}]
    assert result["decision_evidence"] == [{
        "id": row["id"], "display_name": f"Evidence {row['id']}",
    } for row in result["decision_evidence"]]
    assert len(result["decision_evidence"]) == 1


def test_can_act_is_the_permission_owners_current_active_person_rule(inbox):
    issuer, requester, reviewer, _outsider, _subject, _group, decision = inbox
    document = "query { decisions { can_act } }"
    assert query(issuer, document)["decisions"] == [{"can_act": False}]
    assert query(reviewer, document)["decisions"] == [{"can_act": True}]
    with system_context(reason="test inbox requester assignment"):
        decision.assignees.add(requester)
    assert query(requester, document)["decisions"] == [{"can_act": False}]
    admin = create_platform_admin("inbox-admin")
    assert query(admin, document)["decisions"] == [{"can_act": True}]
    with system_context(reason="test inbox inactive resolver"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(is_active=False)
    reviewer.refresh_from_db()
    assert query(reviewer, document)["decisions"] == [{"can_act": False}]
    with system_context(reason="test inbox non-person resolver"):
        type(admin).objects.filter(pk=admin.pk).update(kind="service")
    admin.refresh_from_db()
    assert query(admin, document)["decisions"] == [{"can_act": False}]


def test_live_open_state_excludes_unswept_expiry_without_losing_expiry_work(inbox):
    issuer, _requester, reviewer, _outsider, _subject, _group, _decision = inbox
    expired = Decision.objects.admit_group([DecisionRequest(
        kind="expired_reference", subject=None, assignees=(reviewer,), actions=(Reject,),
        expires_at=timezone.now() + timedelta(minutes=1),
    )], actor=issuer)
    with system_context(reason="test unswept deadline"):
        Decision.objects.filter(group=expired).owner_update(expires_at=Now() - timedelta(seconds=1))
    seat = system_queryset(Decision).get(group=expired)
    assert seat.is_pending and not seat.is_open
    assert system_queryset(Decision).pending().filter(pk=seat.pk).exists()
    assert system_queryset(Decision).due().filter(pk=seat.pk).exists()
    assert not system_queryset(Decision).open().filter(pk=seat.pk).exists()
    assert Decision.objects.expire_due() == 1
    expired.refresh_from_db()
    assert expired.settled_at is not None and expired.outcome == "expired"


def test_open_resource_projection_does_not_query_each_seat(inbox):
    issuer, _requester, reviewer, _outsider, _subject, _group, _decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    document = "query { decisions { is_open can_act } }"
    with CaptureQueriesContext(connection) as one:
        assert result_data(execute_schema(schema, document, user=reviewer)) == {
            "decisions": [{"is_open": True, "can_act": True}],
        }
    Decision.objects.admit_group([
        DecisionRequest(kind=f"reference_{index}", subject=None, assignees=(reviewer,), actions=(Reject,))
        for index in range(7)
    ], actor=issuer, policy="all")
    with CaptureQueriesContext(connection) as many:
        assert result_data(execute_schema(schema, document, user=reviewer)) == {
            "decisions": [{"is_open": True, "can_act": True}] * 8,
        }
    assert len(many) == len(one)


def test_nested_decision_open_and_permission_facts_are_batched(inbox):
    """Optimizer annotations follow a decision through a prefetched relation."""
    issuer, _requester, reviewer, _outsider, subject, _group, _decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    document = "{ decision_evidence { decision { is_open can_act } } }"
    with CaptureQueriesContext(connection) as one:
        assert result_data(execute_schema(schema, document, user=reviewer)) == {
            "decision_evidence": [{"decision": {"is_open": True, "can_act": True}}],
        }
    for _ in range(7):
        seed_group(actor=issuer, assignees=(reviewer,), reference=subject)
    with CaptureQueriesContext(connection) as many:
        assert result_data(execute_schema(schema, document, user=reviewer)) == {
            "decision_evidence": [{"decision": {"is_open": True, "can_act": True}}] * 8,
        }
    assert len(many) == len(one)


def test_is_open_filter_and_field_share_deadlines_and_terminal_state(inbox):
    issuer, _requester, reviewer, _outsider, _subject, group, decision = inbox
    expired = Decision.objects.admit_group([DecisionRequest(
        kind="unswept_reference", subject=None, assignees=(reviewer,), actions=(Reject,),
        expires_at=timezone.now() + timedelta(minutes=1),
    )], actor=issuer)
    with system_context(reason="test unswept deadline"):
        Decision.objects.filter(group=expired).owner_update(expires_at=Now() - timedelta(seconds=1))
    expired_seat = system_queryset(Decision).get(group=expired)
    document = """query {
      open: decisions(where: {is_open: {_eq: true}}) { id is_open }
      closed: decisions(where: {is_open: {_eq: false}}) { id is_open }
      open_count: decisions_aggregate(where: {is_open: {_eq: true}}) { aggregate { count } }
    }"""
    assert query(reviewer, document) == {
        "open": [{"id": str(decision.sqid), "is_open": True}],
        "closed": [{"id": str(expired_seat.sqid), "is_open": False}],
        "open_count": {"aggregate": {"count": 1}},
    }
    Decision.objects.cancel_group(group.pk)
    result = query(reviewer, document)
    assert result["open"] == [] and result["open_count"] == {"aggregate": {"count": 0}}
    assert all(row["is_open"] is False for row in result["closed"])


def test_seed_helper_restores_registry_and_freezes_default_readonly_and_minlength_contract(inbox):
    issuer, _requester, reviewer, _outsider, subject, group, decision = inbox
    registrations = dict(settings.ANGEE_DECISION_ACTION_CLASSES)
    with pytest.raises(ValidationError, match="reference"):
        Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision,
                                action="accept", values={"reference": "different"})
    decision.refresh_from_db()
    accepted = Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision,
                                       action="accept", values={})
    assert accepted.resolution == {"action": "accept", "note": "Reviewed", "reference": str(subject.sqid)}
    resolved = Decision.objects.resolutions(group.pk, actor=issuer, actions=(Accept, Reject))
    assert resolved[0].action.note == "Reviewed"
    second = seed_group(actor=issuer, assignees=(reviewer,), reference=subject)
    assert settings.ANGEE_DECISION_ACTION_CLASSES == registrations
    another = system_queryset(Decision).get(group=second)
    with pytest.raises(ValidationError, match="reason"):
        Decision.objects.decide(another.pk, actor=reviewer, revision=another.revision,
                                action="reject", values={"reason": ""})
    assert system_queryset(DecisionGroup).count() == 2


def test_inbox_sdl_and_resource_metadata_publish_backend_owned_facts():
    schema = addon_schema(decision_schema.schemas, "console")
    resource = next(item for item in schema.angee_resources if item.model_label == "decisions.Decision")
    node = schema._schema.get_type(resource.type_names.node)
    assert str(node.fields["can_act"].type) == "Boolean!"
    assert str(node.fields["is_open"].type) == "Boolean!"
    assert str(node.fields["errors"].type) == "JSON!"
    assert "is_open" in schema._schema.get_type(resource.type_names.filter).fields
    assert {"can_act", "is_open", "errors"} <= {field.name for field in resource.fields}
    assert resource.query.fields["is_open"].filter is not None
    assert resource.query.fields["can_act"].filter is None


def test_reasked_field_errors_are_returned_on_the_new_question(inbox):
    issuer, _requester, reviewer, _outsider, _subject, group, decision = inbox
    Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision, action="accept", values={})
    errors = {"note": ["The reference changed; review it again."]}
    repeated = Decision.objects.reask(group.pk, actor=issuer, actions=(Accept, Reject), errors=errors)
    question = system_queryset(Decision).get(group=repeated)
    result = query(reviewer, "query($id: String!) { decisions(where: {id: {_eq: $id}}) { errors is_open } }",
                   {"id": str(question.sqid)})
    assert result == {"decisions": [{"errors": errors, "is_open": True}]}
    assert system_queryset(Decision).get(pk=decision.pk).errors == {}
