"""Filtering concern links cannot reveal an unreadable record identity."""

import pytest
from rebac import actor_context

from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import DecisionRecord
from tests.conftest import Vault, addon_schema, create_user, execute_schema, result_data

pytestmark = pytest.mark.django_db(transaction=True)


def test_assignee_cannot_filter_on_an_unreadable_concern_identity(composed_tables):
    owner, assignee = create_user("oracle-owner"), create_user("oracle-assignee")
    with actor_context(owner):
        vault = Vault.objects.create_for(owner, name="Private")
        other = Vault.objects.create_for(owner, name="Other")
    relation = next(iter(Vault.rebac_grantable))
    with actor_context(owner):
        vault.grant_record_access(relation, assignee)
    decision = seed_decision(actor=owner, assignees=(owner, assignee), reference=vault, requester=None)
    with actor_context(owner):
        vault.revoke_record_access(relation, assignee)
    DecisionRecord.system_queryset().get(decision=decision)
    assert not Vault.objects.with_actor(assignee).filter(pk=vault.pk).exists()
    from angee.decisions.testing.models import Decision
    assert Decision.objects.with_actor(assignee).filter(pk=decision.pk).exists()
    schema = addon_schema(decision_schema.schemas, "console")
    q = """query($model: String!, $id: String!) {
      decision_records(where: {record_model: {_eq: $model}, record_id: {_eq: $id}}) {
        id record_id record_model
      }
    }"""
    hit = result_data(execute_schema(schema, q, {"model": Vault._meta.label, "id": vault.sqid}, user=assignee))
    miss = result_data(execute_schema(schema, q, {"model": Vault._meta.label, "id": other.sqid}, user=assignee))
    assert hit == {"decision_records": []}, ("oracle: unreadable concern identity matched", hit, miss)
    assert miss == {"decision_records": []}
