"""Social connection imports land through the party and relationship managers."""

from django.contrib.auth import get_user_model
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- declare the native fixture model graph
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.backends import ManualChannelBackend, ParsedHandle
from angee.messaging.testing.models import Channel, Handle, Party, Person, Relationship, RelationshipKind
from angee.parties.connections import ParsedConnection
from angee.provenance.statements import SourceKind
from tests.conftest import current_stream, make_integration


def test_connection_replay_keeps_one_person_and_its_source_relationships(db, monkeypatch):
    """A replayed export record lands on the same Person, link and edges."""

    del db
    monkeypatch.setattr(ManualChannelBackend, "source_kind", SourceKind.IMPORT)
    with system_context(reason="parties.connection.replay"):
        user = get_user_model().objects.create_user(username="connection-owner")
        channel = make_integration("connection-replay", model=Channel, backend_class="manual")
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
            [(parsed, link)], created_by_id=user.pk, asserted_by=channel.integration_ptr,
        )
        (second,) = Party.objects.ingest_connections(
            [(parsed, link)], created_by_id=user.pk, asserted_by=channel.integration_ptr,
        )
        anchor = Party.objects.for_user(user)

        assert first.pk == second.pk
        assert Person.objects.filter(pk=first.pk).count() == 1
        link.refresh_from_db()
        assert link.target_object_id == str(first.pk)
        assert Relationship.objects.filter(party_id=anchor.pk, other_party_id=first.pk).count() == 1
        assert Relationship.objects.filter(party_id=first.pk, other_name="Example employer").count() == 1
        first.refresh_from_db()
        assert first.handle_count == 1
        assert Handle.objects.get(platform="facebook", value="friend42").party_id == first.pk


def test_a_connection_handle_is_identified_by_an_identifier_or_its_typed_confidence():
    """A name alone never identifies an account; an adapter's confidence overrides the inference."""

    named = ParsedHandle(platform="facebook", value="Friend", display_name="Friend")
    assert not Handle.identified_connection(named)
    assert Handle.identified_connection(ParsedHandle(platform="facebook", value="friend42", external_id="42"))
    assert Handle.identified_connection(ParsedHandle(platform="email", value="friend@example.test"))
    assert Handle.identified_connection(ParsedHandle(platform="facebook", value="Friend", confidence=1.0))
