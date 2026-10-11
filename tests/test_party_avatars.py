"""Party and handle avatars: one write verb, a derived display avatar, and the record's readers read it."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.identity import public_id_of
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging import session as messaging_session
from angee.messaging.session import LiveChannelSession
from angee.messaging.testing.models import Handle, Organization, Party, PartyHandle, Person
from angee.parties.backends import AvatarReport, ParsedPhoto
from angee.parties.mixins import LinkSource
from tests.conftest import File, SchemaAddon, execute_schema, result_data
from tests.test_storage import PNG_BYTES, SVG_BYTES, drive, image_drive  # noqa: F401 -- the image drive avatars land on

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("image_drive", "composed_permissions")]
parties_schema = importlib.import_module("angee.parties.schema")

GIF_BYTES = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
_PHONE = "+18583421935"
_JID = "18583421935@s.whatsapp.net"


def _user(username: str) -> Any:
    return get_user_model().objects.create_user(username=username)


def _grant_reader(resource: Any, user: Any) -> None:
    write_relationships([RelationshipTuple(to_object_ref(resource), "reader", to_subject_ref(user))])


def _avatar(data: bytes) -> Any:
    with system_context(reason="test party avatar image"):
        return File.objects.ingest_image(data)


def _report(avatar: bytes | None, *, external_id: str = _JID, value: str = _PHONE) -> AvatarReport:
    return AvatarReport(platform="whatsapp", value=value, external_id=external_id, avatar=avatar)


def test_a_report_sets_its_handles_avatar_and_never_creates_or_renames_a_handle() -> None:
    """The same avatar again changes nothing; refused or empty bytes keep it; a new one replaces it; none clears it."""

    owner = _user("avatar-reporter")
    with system_context(reason="test reported avatar"):
        handle = Handle.objects.upsert(
            platform="whatsapp", value=_PHONE, external_id=_JID, display_name="Ada", created_by_id=owner.pk
        )

    def avatar_id() -> Any:
        with system_context(reason="test reported avatar read"):
            return Handle._base_manager.get(pk=handle.pk).avatar_id

    assert Handle.objects.fill_avatar(_report(PNG_BYTES)) is True
    first = avatar_id()
    assert first is not None
    for unchanged in (PNG_BYTES, SVG_BYTES, b""):
        assert Handle.objects.fill_avatar(_report(unchanged)) is False
        assert avatar_id() == first
    # Reported under the value alone, the report lands on the same handle.
    assert Handle.objects.fill_avatar(_report(GIF_BYTES, external_id="")) is True
    assert avatar_id() not in (None, first)
    assert Handle.objects.fill_avatar(_report(None)) is True
    assert avatar_id() is None

    assert Handle.objects.fill_avatar(_report(PNG_BYTES, external_id="1999@s.whatsapp.net", value="+1999")) is False
    with system_context(reason="test reported avatar identity"):
        assert Handle._base_manager.filter(platform="whatsapp").count() == 1
        assert Handle._base_manager.get(pk=handle.pk).display_name == "Ada"


def test_a_party_shows_its_own_avatar_else_its_strongest_readable_handles() -> None:
    owner = _user("avatar-display-owner")
    reader = _user("avatar-display-reader")
    with system_context(reason="test display avatar"):
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        weaker = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=owner.pk)
        stronger = Handle.objects.upsert(platform="whatsapp", value=_PHONE, created_by_id=owner.pk)
        weaker_link = PartyHandle.objects.link(
            party, weaker, confidence=0.6, source=LinkSource.IMPORT, created_by_id=owner.pk
        )
        PartyHandle.objects.link(party, stronger, source=LinkSource.IMPORT, is_confirmed=True, created_by_id=owner.pk)
    weaker_avatar, stronger_avatar, own_avatar = _avatar(PNG_BYTES), _avatar(GIF_BYTES), _avatar(PNG_BYTES + b"\x00")
    Handle.objects.set_avatar(weaker, weaker_avatar)
    Handle.objects.set_avatar(stronger, stronger_avatar)
    for readable in (party, weaker, weaker_link):
        _grant_reader(readable, reader)

    with system_context(reason="test display avatar read"):
        party = Party._base_manager.get(pk=party.pk)
    # The confirmed link ranks first; a reader who cannot read that handle sees the next one.
    assert Party.objects.display_avatar(party, actor=owner).pk == stronger_avatar.pk
    assert Party.objects.display_avatar(party, actor=reader).pk == weaker_avatar.pk

    with system_context(reason="test display avatar own"):
        party.avatar = own_avatar
        party.save(update_fields=["avatar", "updated_at"])
        party = Party._base_manager.get(pk=party.pk)
    assert Party.objects.display_avatar(party, actor=owner).pk == own_avatar.pk
    assert Party.objects.display_avatar(party, actor=reader).pk == own_avatar.pk


def test_a_contact_photo_resolves_to_its_avatar_never_to_a_users_file_with_the_same_bytes() -> None:
    owner = _user("avatar-resolve-owner")
    with system_context(reason="test contact photo resolve"):
        private = File.objects.ingest_bytes(PNG_BYTES, filename="me.png", owner_id=owner.pk)
        avatar = File.objects.ingest_image(PNG_BYTES)
        with transaction.atomic():
            resolved = Party.objects.resolve_contact_photo(
                ParsedPhoto(content_hash=avatar.content_hash, mime="image/png")
            )
    assert resolved.pk == avatar.pk != private.pk


def test_the_display_avatar_never_reaches_the_address_book() -> None:
    """A synced contact with no avatar of its own projects none, whatever its handles show."""

    owner = _user("avatar-projection-owner")
    with system_context(reason="test avatar projection"):
        person = Person._base_manager.create(display_name="Ada", source_uid="ada", created_by=owner)
        handle = Handle.objects.upsert(platform="whatsapp", value=_PHONE, created_by_id=owner.pk)
        PartyHandle.objects.link(person, handle, source=LinkSource.IMPORT, created_by_id=owner.pk)
    Handle.objects.set_avatar(handle, _avatar(PNG_BYTES))

    with system_context(reason="test avatar projection read"):
        person = Person._base_manager.get(pk=person.pk)
        assert Party.objects.display_avatar(person, actor=owner) is not None
        assert Party.objects.project_contact(person).photo is None


def test_readers_of_a_party_or_handle_read_its_avatar_and_a_stranger_does_not() -> None:
    owner = _user("avatar-read-owner")
    reader = _user("avatar-read-reader")
    stranger = _user("avatar-read-stranger")
    avatars = [_avatar(PNG_BYTES + bytes([index])) for index in range(4)]
    with system_context(reason="test avatar read"):
        records = [
            Party._base_manager.create(display_name="Ada", avatar=avatars[0], created_by=owner),
            Person._base_manager.create(display_name="Grace", avatar=avatars[1], created_by=owner),
            Organization._base_manager.create(display_name="Analytical", avatar=avatars[2], created_by=owner),
            Handle.objects.upsert(platform="whatsapp", value=_PHONE, created_by_id=owner.pk),
        ]
    Handle.objects.set_avatar(records[3], avatars[3])
    for record in records:
        _grant_reader(record, reader)

    ids = {avatar.pk for avatar in avatars}
    for actor, readable in ((reader, ids), (stranger, set())):
        with actor_context(actor):
            assert set(File.objects.filter(pk__in=ids).values_list("pk", flat=True)) == readable


def test_the_people_list_shows_avatars_with_nested_fields_in_a_bounded_number_of_queries() -> None:
    """Selecting a party's own avatar beside its display avatar, with nested fields, costs the same per page."""

    owner = _user("avatar-graphql-owner")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"public": {key: tuple(parties_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("public")
    nested = "{ id filename mime_type { mime_type } }"
    query = f"query People {{ people {{ id avatar {nested} display_avatar {nested} handles {{ avatar {{ id }} }} }} }}"
    shown: dict[str, str] = {}

    def count_for(people: int, start: int) -> tuple[int, list[Any]]:
        with system_context(reason="test avatar graphql seed"):
            for index in range(start, start + people):
                # Every other person, the first included, carries an avatar of its own, which the
                # display prefers; so both pages read own and handle avatars.
                own = _avatar(GIF_BYTES + bytes([index])) if index % 2 == 0 else None
                person = Person._base_manager.create(display_name=f"Person {index}", avatar=own, created_by=owner)
                handle = Handle.objects.upsert(
                    platform="whatsapp", value=f"+1858342{index:04d}", created_by_id=owner.pk
                )
                PartyHandle.objects.link(person, handle, source=LinkSource.IMPORT, created_by_id=owner.pk)
                handle_avatar = _avatar(PNG_BYTES + bytes([index]))
                Handle.objects.set_avatar(handle, handle_avatar)
                shown[public_id_of(person)] = public_id_of(own or handle_avatar)
        with CaptureQueriesContext(connection) as queries:
            rows = result_data(execute_schema(schema, query, user=owner))["people"]
        return len(queries.captured_queries), rows

    one, rows = count_for(1, 0)
    three, rows = count_for(2, 1)
    assert three == one
    assert {row["id"]: row["display_avatar"]["id"] for row in rows} == shown
    for row in rows:
        assert row["display_avatar"]["mime_type"]["mime_type"] in {"image/png", "image/gif", "application/octet-stream"}
        assert row["display_avatar"]["id"] == (row["avatar"] or row["handles"][0]["avatar"])["id"]
    with system_context(reason="test avatar graphql single"):
        people = list(Person._base_manager.all())
    # The single verb reads what the batched page shows.
    assert {
        public_id_of(person): public_id_of(Party.objects.display_avatar(person, actor=owner)) for person in people
    } == shown


def test_a_live_sessions_reports_land_one_by_one_and_stop_between_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing report is logged and the rest land; a session that is no longer wanted stops between chunks."""

    owner = _user("avatar-session-owner")
    with system_context(reason="test session avatar handle"):
        Handle.objects.upsert(platform="whatsapp", value=_PHONE, external_id=_JID, created_by_id=owner.pk)
    wanted = [True]
    session = SimpleNamespace(bridge=SimpleNamespace(pk=1), _still_wanted=lambda: wanted[0])
    real_fill = type(Handle.objects).fill_avatar

    def fill(manager: Any, report: AvatarReport) -> bool:
        if report.platform == "broken":
            raise RuntimeError("storage unavailable")
        return real_fill(manager, report)

    monkeypatch.setattr(type(Handle.objects), "fill_avatar", fill)
    broken = AvatarReport(platform="broken", value="x", external_id="", avatar=PNG_BYTES)

    assert LiveChannelSession._fill_avatars(session, [broken, _report(PNG_BYTES)]) is True
    with system_context(reason="test session avatar read"):
        assert Handle._base_manager.get(external_id=_JID).avatar_id is not None

    monkeypatch.setattr(messaging_session, "INGEST_CHUNK", 1)
    wanted[0] = False
    assert LiveChannelSession._fill_avatars(session, [_report(None), _report(GIF_BYTES)]) is False
    with system_context(reason="test session avatar stop"):
        # The first chunk landed before the stop check; the second never ran.
        assert Handle._base_manager.get(external_id=_JID).avatar_id is None
