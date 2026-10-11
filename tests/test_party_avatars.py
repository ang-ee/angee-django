"""Party and handle pictures: one write verb, a derived display picture, and the record's readers read it."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.identity import public_id_of
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging.backends import ParsedHandle
from angee.messaging.session import LiveChannelSession
from angee.messaging.testing.models import Handle, Party, PartyHandle, Person
from angee.parties.backends import ParsedPhoto
from angee.parties.mixins import LinkSource
from tests.conftest import File, SchemaAddon, execute_schema, result_data
from tests.test_storage import PNG_BYTES, drive  # noqa: F401 -- the assets drive pictures land in

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("drive", "composed_permissions")]
parties_schema = importlib.import_module("angee.parties.schema")

GIF_BYTES = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _user(username: str) -> Any:
    return get_user_model().objects.create_user(username=username)


def _grant_reader(resource: Any, user: Any) -> None:
    write_relationships([RelationshipTuple(to_object_ref(resource), "reader", to_subject_ref(user))])


def _picture(data: bytes, owner: Any) -> Any:
    with system_context(reason="test party avatar picture"):
        return File.objects.ingest_image(data, filename="avatar", owner_id=owner.pk)


def _report(data: bytes | None, mime: str = "") -> ParsedHandle:
    return ParsedHandle(
        platform="whatsapp",
        value="+18583421935",
        external_id="18583421935@s.whatsapp.net",
        photo=ParsedPhoto(data=data, mime=mime) if data is not None else None,
    )


def test_a_reported_picture_lands_on_its_handle_through_the_one_write_verb() -> None:
    """The same picture again changes nothing, a refused one is skipped, a new one replaces it, none clears it."""

    owner = _user("avatar-reporter")
    with system_context(reason="test reported avatar"):
        handle = Handle.objects.upsert(
            platform="whatsapp",
            value="+18583421935",
            external_id="18583421935@s.whatsapp.net",
            created_by_id=owner.pk,
        )

    def avatar_id() -> Any:
        with system_context(reason="test reported avatar read"):
            return Handle._base_manager.get(pk=handle.pk).avatar_id

    assert Handle.objects.fill_avatars([_report(PNG_BYTES, "image/png")], created_by_id=owner.pk) == 1
    first = avatar_id()
    assert first is not None
    assert Handle.objects.fill_avatars([_report(PNG_BYTES, "image/png")], created_by_id=owner.pk) == 0
    assert Handle.objects.fill_avatars([_report(SVG_BYTES, "image/svg+xml")], created_by_id=owner.pk) == 0
    assert avatar_id() == first
    assert Handle.objects.fill_avatars([_report(GIF_BYTES, "image/gif")], created_by_id=owner.pk) == 1
    assert avatar_id() not in (None, first)
    assert Handle.objects.fill_avatars([_report(None)], created_by_id=owner.pk) == 1
    assert avatar_id() is None


def test_a_party_shows_its_own_photo_else_its_strongest_readable_handles_picture() -> None:
    owner = _user("avatar-display-owner")
    reader = _user("avatar-display-reader")
    with system_context(reason="test display avatar"):
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        weaker = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=owner.pk)
        stronger = Handle.objects.upsert(platform="whatsapp", value="+18583421935", created_by_id=owner.pk)
        PartyHandle.objects.link(party, weaker, confidence=0.6, source=LinkSource.IMPORT, created_by_id=owner.pk)
        PartyHandle.objects.link(party, stronger, source=LinkSource.IMPORT, is_confirmed=True, created_by_id=owner.pk)
    weaker_picture, stronger_picture, own_photo = (
        _picture(PNG_BYTES, owner),
        _picture(GIF_BYTES, owner),
        _picture(PNG_BYTES + b"\x00", owner),
    )
    Handle.objects.set_avatar(weaker, weaker_picture)
    Handle.objects.set_avatar(stronger, stronger_picture)
    _grant_reader(party, reader)
    _grant_reader(weaker, reader)

    with system_context(reason="test display avatar read"):
        party = Party._base_manager.get(pk=party.pk)
    # The confirmed link ranks first; a reader who cannot read that handle sees the next one.
    assert Party.objects.display_avatar(party, actor=owner).pk == stronger_picture.pk
    assert Party.objects.display_avatar(party, actor=reader).pk == weaker_picture.pk

    with system_context(reason="test display avatar own photo"):
        party.avatar = own_photo
        party.save(update_fields=["avatar", "updated_at"])
        party = Party._base_manager.get(pk=party.pk)
    assert Party.objects.display_avatar(party, actor=owner).pk == own_photo.pk
    assert Party.objects.display_avatar(party, actor=reader).pk == own_photo.pk


def test_the_display_picture_never_reaches_the_address_book() -> None:
    """A synced contact with no photo of its own projects none, whatever its handles show."""

    owner = _user("avatar-projection-owner")
    with system_context(reason="test avatar projection"):
        person = Person._base_manager.create(display_name="Ada", source_uid="ada", created_by=owner)
        handle = Handle.objects.upsert(platform="whatsapp", value="+18583421935", created_by_id=owner.pk)
        PartyHandle.objects.link(person, handle, source=LinkSource.IMPORT, created_by_id=owner.pk)
    Handle.objects.set_avatar(handle, _picture(PNG_BYTES, owner))

    with system_context(reason="test avatar projection read"):
        person = Person._base_manager.get(pk=person.pk)
        assert Party.objects.display_avatar(person, actor=owner) is not None
        assert Party.objects.project_contact(person).photo is None


def test_readers_of_a_party_or_handle_read_its_picture_and_a_stranger_does_not() -> None:
    owner = _user("avatar-read-owner")
    reader = _user("avatar-read-reader")
    stranger = _user("avatar-read-stranger")
    party_photo, person_photo, handle_picture = (
        _picture(PNG_BYTES, owner),
        _picture(GIF_BYTES, owner),
        _picture(PNG_BYTES + b"\x00", owner),
    )
    with system_context(reason="test avatar read"):
        party = Party._base_manager.create(display_name="Ada", avatar=party_photo, created_by=owner)
        person = Person._base_manager.create(display_name="Grace", avatar=person_photo, created_by=owner)
        handle = Handle.objects.upsert(platform="whatsapp", value="+18583421935", created_by_id=owner.pk)
    Handle.objects.set_avatar(handle, handle_picture)
    for record in (party, person, handle):
        _grant_reader(record, reader)

    for actor, readable in ((reader, True), (stranger, False)):
        with actor_context(actor):
            seen = set(
                File.objects.filter(pk__in=(party_photo.pk, person_photo.pk, handle_picture.pk)).values_list(
                    "pk", flat=True
                )
            )
        assert seen == ({party_photo.pk, person_photo.pk, handle_picture.pk} if readable else set())


def test_the_people_list_shows_each_display_picture_in_a_bounded_number_of_queries() -> None:
    owner = _user("avatar-graphql-owner")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"public": {key: tuple(parties_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("public")
    query = "query People { people { id display_avatar { id } handles { avatar { id } } } }"
    pictures: dict[str, str] = {}

    def count_for(people: int, start: int) -> tuple[int, list[Any]]:
        with system_context(reason="test avatar graphql seed"):
            for index in range(start, start + people):
                person = Person._base_manager.create(display_name=f"Person {index}", created_by=owner)
                handle = Handle.objects.upsert(
                    platform="whatsapp", value=f"+1858342{index:04d}", created_by_id=owner.pk
                )
                PartyHandle.objects.link(person, handle, source=LinkSource.IMPORT, created_by_id=owner.pk)
                picture = _picture(PNG_BYTES + bytes([index]), owner)
                Handle.objects.set_avatar(handle, picture)
                pictures[public_id_of(person)] = public_id_of(picture)
        with CaptureQueriesContext(connection) as queries:
            rows = result_data(execute_schema(schema, query, user=owner))["people"]
        return len(queries.captured_queries), rows

    one, rows = count_for(1, 0)
    assert {row["id"]: row["display_avatar"]["id"] for row in rows} == pictures
    assert rows[0]["handles"][0]["avatar"]["id"] == rows[0]["display_avatar"]["id"]
    three, rows = count_for(2, 1)
    assert {row["id"]: row["display_avatar"]["id"] for row in rows} == pictures
    assert three == one


def test_a_live_sessions_reported_pictures_land_and_a_failure_never_ends_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _user("avatar-session-owner")
    session = SimpleNamespace(bridge=SimpleNamespace(pk=1, owner_id=owner.pk), _still_wanted=lambda: True)

    assert LiveChannelSession._handle(session, "handle_avatars", [_report(PNG_BYTES, "image/png")]) is True
    with system_context(reason="test session avatar read"):
        assert Handle._base_manager.get(external_id="18583421935@s.whatsapp.net").avatar_id is not None

    def fail(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(type(Handle.objects), "fill_avatars", fail)
    assert LiveChannelSession._handle(session, "handle_avatars", [_report(GIF_BYTES, "image/gif")]) is True
