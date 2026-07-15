"""Tests for the CardDAV connection boundary and vCard parser.

The directory-sync map and `purge_missing` are exercised live against the example
database; these cover the transport-parse boundary the live run can't assert
deterministically — full field mapping and the UID fallback whose empty result the
sync deliberately skips (an empty key would collapse keyless cards onto one row).
The connect cases pin the probe-before-write boundary against concrete test models.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
import vobject
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.parties_integrate_carddav.backend import (
    CardDavDirectoryBackend,
    CardDavError,
    _parse_data_uri,
    _parse_date,
    _parse_vcard,
)
from tests import test_parties_graphql as parties_graphql
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    Integration,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
)
from tests.test_messaging import Directory, Handle, Party, PartyHandle, Person

_CARDDAV_CONNECT_MODELS = (
    *IAM_CONNECTION_TEST_MODELS,
    *INTEGRATE_TEST_MODELS,
    Directory,
    Party,
    Person,
    Handle,
    PartyHandle,
)

_CONNECT_CARDDAV_MUTATION = """
mutation ConnectCardDav(
  $name: String!
  $serverUrl: String!
  $username: String!
  $password: String!
) {
  connect_card_dav_directory(
    name: $name
    server_url: $serverUrl
    username: $username
    password: $password
  ) {
    id
    display_name
    backend_class
    lifecycle
    runtime_status
    config
  }
}
"""

_FULL_VCARD = """BEGIN:VCARD
VERSION:3.0
UID:abc-123
FN:Ada Lovelace
N:Lovelace;Ada;Augusta;Ms.;PhD
NICKNAME:Countess
EMAIL;TYPE=HOME:ada@example.com
EMAIL;TYPE=WORK,PREF:ada@work.example.com
TEL;TYPE=CELL:+15550100
ADR;TYPE=HOME:;;12 Analytical St;London;;EC1;UK
ORG:Analytical Engines;Research
TITLE:Mathematician
ROLE:Programmer
BDAY:1815-12-10
ANNIVERSARY:18350101
PHOTO;ENCODING=b;TYPE=PNG:QUJD
NOTE:First programmer.
END:VCARD"""


@pytest.fixture()
def carddav_connect_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete integration and parties rows the connect flow owns."""

    del transactional_db
    created_models = _create_missing_tables(_CARDDAV_CONNECT_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(_CARDDAV_CONNECT_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def test_connect_probe_failure_writes_no_rows(
    carddav_connect_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected external probe runs before atomic and leaves no partial rows."""

    del carddav_connect_tables
    probe_atomic_states: list[bool] = []

    def reject_probe(backend: CardDavDirectoryBackend) -> None:
        del backend
        probe_atomic_states.append(connection.in_atomic_block)
        raise CardDavError("CardDAV probe rejected")

    monkeypatch.setattr(CardDavDirectoryBackend, "probe", reject_probe)
    admin = parties_graphql._platform_admin("carddav-probe-failure-admin")

    result = _connect_carddav(admin)

    assert result.errors is not None
    assert "CardDAV probe rejected" in str(result.errors[0])
    assert probe_atomic_states == [False]
    with system_context(reason="test.parties.carddav.probe_failure.verify"):
        for model in (Credential, Vendor, Integration, Directory, Handle, Party, Person, PartyHandle):
            assert not model._base_manager.exists(), model._meta.label


def test_connect_probe_success_commits_every_owned_row_atomically(
    carddav_connect_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful external probe precedes one complete atomic write phase."""

    del carddav_connect_tables
    probe_atomic_states: list[bool] = []
    write_atomic_states: list[bool] = []

    def accept_probe(backend: CardDavDirectoryBackend) -> None:
        del backend
        probe_atomic_states.append(connection.in_atomic_block)

    claim_own = Handle.objects.claim_own

    def atomic_claim_own(user: Any, **kwargs: Any) -> Any:
        write_atomic_states.append(connection.in_atomic_block)
        return claim_own(user, **kwargs)

    monkeypatch.setattr(CardDavDirectoryBackend, "probe", accept_probe)
    monkeypatch.setattr(Handle.objects, "claim_own", atomic_claim_own)
    admin = parties_graphql._platform_admin("carddav-probe-success-admin")

    result = _connect_carddav(admin)

    assert result.errors is None
    assert result.data is not None
    directory_data = result.data["connect_card_dav_directory"]
    assert directory_data == {
        "id": directory_data["id"],
        "display_name": "Ada Contacts",
        "backend_class": "CARDDAV",
        "lifecycle": "ACTIVE",
        "runtime_status": "OK",
        "config": {"server_url": "https://dav.example.com/"},
    }
    assert probe_atomic_states == [False]
    assert write_atomic_states == [True]

    with system_context(reason="test.parties.carddav.probe_success.verify"):
        credential = Credential.objects.get()
        vendor = Vendor.objects.get()
        directory = Directory.objects.get()
        integration = Integration.objects.get(pk=directory.pk)
        handle = Handle.objects.get()
        person = Person.objects.get()
        link = PartyHandle.objects.get()

        assert credential.user_id == admin.pk
        assert credential.name == "CardDAV — Ada Contacts"
        assert credential.reveal() == {
            "username": "ada@example.com",
            "password": "carddav-password",
        }
        assert (vendor.slug, vendor.display_name) == ("carddav", "CardDAV")
        assert integration.credential_id == credential.pk
        assert integration.owner_id == admin.pk
        assert directory.backend_class == "carddav"
        assert directory.lifecycle == "active"
        assert directory.config == {"server_url": "https://dav.example.com/"}
        assert handle.owner_id == admin.pk
        assert (handle.platform, handle.value) == ("email", "ada@example.com")
        assert person.user_id == admin.pk
        assert link.party_id == person.pk
        assert link.handle_id == handle.pk
        assert link.source == "carddav"
        assert link.confidence == 1.0
        assert link.is_confirmed is True


def _connect_carddav(admin: Any) -> Any:
    """Execute the CardDAV connect mutation with one stable account payload."""

    return execute_schema(
        parties_graphql._schema("console"),
        _CONNECT_CARDDAV_MUTATION,
        {
            "name": "Ada Contacts",
            "serverUrl": "https://dav.example.com/",
            "username": "ada@example.com",
            "password": "carddav-password",
        },
        user=admin,
    )


def _parse(text: str, *, href: str = "/ab/ada.vcf") -> object:
    return _parse_vcard(vobject.readOne(text), etag="v1", href=href, raw=text)


def test_parse_full_vcard_maps_every_field() -> None:
    """A complete vCard maps to all neutral ParsedContact fields."""

    contact = _parse(_FULL_VCARD)
    assert contact.uid == "abc-123"
    assert contact.etag == "v1"
    assert contact.display_name == "Ada Lovelace"
    assert contact.name_prefix == "Ms."
    assert contact.given_name == "Ada"
    assert contact.additional_name == "Augusta"
    assert contact.family_name == "Lovelace"
    assert contact.name_suffix == "PhD"
    assert contact.nickname == "Countess"
    assert contact.notes == "First programmer."
    assert contact.organization == "Analytical Engines"
    assert contact.department == "Research"
    assert contact.title == "Mathematician"
    assert contact.role == "Programmer"
    assert contact.birthday == date(1815, 12, 10)
    assert contact.anniversary == date(1835, 1, 1)
    # Emails/phones are (value, label, is_preferred); TYPE=PREF marks the work address.
    assert contact.emails == (
        ("ada@example.com", "home", False),
        ("ada@work.example.com", "work", True),
    )
    assert contact.phones == (("+15550100", "cell", False),)
    assert contact.photo is not None
    assert contact.photo.data == b"ABC"  # QUJD base64-decoded
    assert contact.photo.mime == "image/png"
    assert len(contact.addresses) == 1
    address = contact.addresses[0]
    assert (address.street, address.city, address.postal_code, address.country) == (
        "12 Analytical St",
        "London",
        "EC1",
        "UK",
    )
    assert contact.raw_vcard == _FULL_VCARD


def test_parse_date_accepts_the_common_vcard_formats() -> None:
    """BDAY/ANNIVERSARY parse across ISO, basic, and year-omitted forms."""

    assert _parse_date("1990-05-15") == date(1990, 5, 15)
    assert _parse_date("19900515") == date(1990, 5, 15)
    assert _parse_date("--0515") == date(1604, 5, 15)  # year-omitted sentinel
    assert _parse_date("--05-15") == date(1604, 5, 15)
    assert _parse_date("") is None
    assert _parse_date("not-a-date") is None


def test_parse_data_uri_decodes_inline_base64() -> None:
    """A vCard 4.0 ``data:`` PHOTO URI decodes to bytes with its MIME type."""

    photo = _parse_data_uri("data:image/jpeg;base64,QUJD")
    assert photo is not None
    assert photo.data == b"ABC"
    assert photo.mime == "image/jpeg"


def test_remote_photo_uri_is_left_for_the_transport_to_fetch() -> None:
    """A remote PHOTO URI is recorded but not fetched by the pure parser."""

    vcard = (
        "BEGIN:VCARD\nVERSION:3.0\nUID:x\nFN:X\n"
        "PHOTO;VALUE=uri:https://example.com/a.jpg\nEND:VCARD"
    )
    contact = _parse(vcard)
    assert contact.photo is not None
    assert contact.photo.data is None
    assert contact.photo.uri == "https://example.com/a.jpg"


def test_uid_falls_back_to_fn_then_href() -> None:
    """A card without UID keys on FN; without UID and FN, on the resource href."""

    no_uid = "BEGIN:VCARD\nVERSION:3.0\nFN:Grace Hopper\nEMAIL:grace@example.com\nEND:VCARD"
    assert _parse(no_uid, href="/ab/grace.vcf").uid == "Grace Hopper"

    bare = "BEGIN:VCARD\nVERSION:3.0\nEMAIL:anon@example.com\nEND:VCARD"
    assert _parse(bare, href="/ab/anon.vcf").uid == "/ab/anon.vcf"


def test_no_stable_key_yields_empty_uid() -> None:
    """With no UID, FN, or href the uid is empty — the sync skips these (no collapse)."""

    bare = "BEGIN:VCARD\nVERSION:3.0\nEMAIL:anon@example.com\nEND:VCARD"
    assert _parse(bare, href="").uid == ""
