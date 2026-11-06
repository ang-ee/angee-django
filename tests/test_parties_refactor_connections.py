"""Connection imports retain native identity and source-owned statement policy."""

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- declare the native fixture model graph
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.backends import ManualChannelBackend, ParsedHandle
from angee.messaging.testing.models import Channel, Party, Person, Relationship, RelationshipKind
from angee.parties.connections import ParsedConnection
from angee.provenance.statements import SourceKind
from tests.conftest import current_stream, make_integration


@pytest.mark.django_db(transaction=True)
def test_connection_replay_keeps_person_and_source_relationships(composed_tables, monkeypatch):
    del composed_tables
    monkeypatch.setattr(ManualChannelBackend, "source_kind", SourceKind.IMPORT)
    with system_context(reason="parties.connection.native_owner"):
        user = get_user_model().objects.create_user(username="connection-owner")
        channel = make_integration("connection-native-owner", model=Channel, backend_class="manual")
        for slug in ("acquaintance", "employee"):
            RelationshipKind.objects.get_or_create(slug=slug, defaults={"name": slug.title()})
        stream = current_stream(channel, "connections", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "friend:42")
        parsed = ParsedConnection(
            handle=ParsedHandle(platform="facebook", value="friend42", external_id="42", display_name="Friend"),
            organization="Example employer",
            title="Researcher",
        )
        (first,) = Party.objects.ingest_connections(
            [(parsed, link)], created_by_id=user.pk, asserted_by=channel.integration_ptr
        )
        (second,) = Party.objects.ingest_connections(
            [(parsed, link)], created_by_id=user.pk, asserted_by=channel.integration_ptr
        )
        assert first.pk == second.pk
        assert Person.objects.filter(pk=first.pk).count() == 1
        link.refresh_from_db()
        assert link.target_object_id == str(first.pk)
        assert Relationship.objects.filter(party_id=first.pk, other_name="Example employer").count() == 1
        first.refresh_from_db()
        assert first.handle_count == 1
