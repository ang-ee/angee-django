"""Follower identity is reachable through record read, without private contact access."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context
from rebac.backends import backend

from tests.chatterdemo.models import ChatterDoc
from tests.conftest import execute_schema, result_data
from tests.messaging_campaign import grant
from tests.messaging_models import Party, Person, ThreadFollower
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.t3_campaign import relationship_snapshot
from tests.test_nexus import Cadence, Tie
from tests.test_parties_follower_identity import identity_graphql as identity_graphql


@pytest.fixture
def record_identity(campaign_access, campaign_user, settings):
    # Match the existing follower identity fixture's supported redaction mode.
    settings.REBAC_FIELD_READ_MODE = "redact"
    owner, reader, outsider = (campaign_user(role) for role in ("owner", "reader", "outsider"))
    with system_context(reason="tests.t3.record_identity"):
        record = ChatterDoc.objects.create(title="Followed record")
        person = Person.objects.create(display_name="Accountless follower", notes="Private", created_by=owner)
        stranger = Person.objects.create(display_name="Unfollowed person", created_by=owner)
    grant(record, "reader", reader)
    with system_context(reason="tests.t3.follow"):
        record.message_subscribe(party=person)
    return record, person, stranger, reader, outsider


@pytest.mark.parametrize("actor_role", ("reader", "outsider", "anonymous"))
def test_record_arm_exposes_accountless_follower_identity_and_no_private_fields(record_identity, actor_role):
    record, person, stranger, reader, outsider = record_identity
    actor = {"reader": reader, "outsider": outsider, "anonymous": AnonymousUser()}[actor_role]
    allowed = actor_role == "reader"
    assert record.message_thread(create=False).with_actor(actor).has_access("read") is allowed
    for model in (Party, Person):
        row = model._base_manager.get(pk=person.pk)
        assert row.with_actor(actor).has_access("read") is allowed
        assert not row.with_actor(actor).has_access("read_private")
        assert model.objects.with_actor(actor).filter(pk=person.pk).exists() is allowed
        assert not model.objects.with_actor(actor).filter(pk=stranger.pk).exists()
        if allowed:
            readable = model.objects.with_actor(actor).get(pk=person.pk)
            assert readable.display_name == "Accountless follower" and readable.notes is None


def test_record_follower_sql_is_one_query_and_unfollow_revokes_parent_and_child(record_identity, monkeypatch):
    record, person, _, reader, _ = record_identity
    local = backend()
    monkeypatch.setattr(type(local), "accessible", lambda *args, **kwargs: pytest.fail("Enumerated identity IDs"))
    for model in (Party, Person):
        query = model.objects.with_actor(reader).scoped().filter(pk=person.pk)
        # Compile once first: Django resolves content types while compiling, and
        # those cold lookups are not part of the scoped read being counted.
        str(query.query)
        with CaptureQueriesContext(connection) as queries:
            assert list(query.values_list("pk", flat=True)) == [person.pk]
        assert len(queries) == 1
    before = relationship_snapshot()
    with system_context(reason="tests.t3.unfollow"):
        record.message_unsubscribe(party=person)
    assert relationship_snapshot() == before
    for model in (Party, Person):
        assert not model.objects.with_actor(reader).filter(pk=person.pk).exists()
    assert not ThreadFollower._base_manager.filter(party=person).exists()


def test_hidden_introducer_is_null_in_record_derived_follower_projection(record_identity, identity_graphql):
    record, person, stranger, reader, _ = record_identity
    with system_context(reason="tests.t3.introducer"):
        person.introduced_by = stranger
        person.save(update_fields=("introduced_by",))
    query = """query($id: ID!) {
      record_thread(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id}) {
        error_code followers { party { id display_name notes introduced_by { id } } user { id } }
      }
    }"""
    with actor_context(reader):
        result = result_data(execute_schema(identity_graphql, query, {"id": record.sqid}, user=reader))["record_thread"]
    assert result["error_code"] is None
    assert result["followers"] == [
        {
            "party": {"id": person.sqid, "display_name": person.display_name, "notes": None, "introduced_by": None},
            "user": None,
        }
    ]


def test_record_identity_never_exposes_private_ties_or_even_the_viewers_own_cadence(record_identity):
    record, person, _, reader, _ = record_identity
    with system_context(reason="tests.t3.private_network"):
        viewer = Person.objects.for_user(reader)
        first, second = sorted((person, viewer), key=lambda row: row.pk)
        tie = Tie.objects.create(party_a=first, party_b=second)
        cadence = Cadence.objects.create(user=reader, party=person, cadence_days=14)
    assert record.with_actor(reader).has_access("read")
    assert person.with_actor(reader).has_access("read")
    for row in (tie, cadence):
        assert not row.with_actor(reader).has_access("read")
        assert not type(row).objects.with_actor(reader).filter(pk=row.pk).exists()
