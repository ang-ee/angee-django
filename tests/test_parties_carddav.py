"""CardDAV connection and parsing contracts; replica cases live in the sync suite."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
import vobject
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, transaction
from rebac import PermissionDenied, system_context

from angee.graphql.testing.documents import execute_schema
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClient
from angee.integrate.testing.http import mock_transport
from angee.messaging.testing.models import Directory, Handle, Party, Person
from angee.parties import schema as parties_schema
from angee.parties_integrate_carddav import apps as carddav_apps
from angee.parties_integrate_carddav import schema as carddav_schema
from angee.parties_integrate_carddav.apps import check_expat_version
from angee.parties_integrate_carddav.backend import (
    CardDavDirectoryBackend,
    CardDavError,
    ParsedCard,
    ParsedContact,
    ParsedPhoto,
    _parse_data_uri,
    _parse_date,
    _parse_vcard,
)
from angee.resources.testing.models import Resource
from tests import test_parties_graphql as parties_graphql
from tests.conftest import Credential, Integration, Vendor, addon_schema, make_integration

pytestmark = pytest.mark.django_db(transaction=True)

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


@pytest.mark.parametrize("version", [(2, 7, 1), (2, 7, 2), (3, 0, 0)])
def test_carddav_requires_expat_amplification_protections(
    monkeypatch: pytest.MonkeyPatch,
    version: tuple[int, int, int],
) -> None:
    monkeypatch.setattr(carddav_apps.pyexpat, "version_info", version)
    errors = check_expat_version()
    if version >= (2, 7, 2):
        assert errors == []
    else:
        [error] = errors
        assert isinstance(error, checks.Error)
        assert "Expat 2.7.2 or later" in error.msg
        assert ".".join(str(part) for part in version) in error.msg


def _backend_with_http(http: Any) -> CardDavDirectoryBackend:
    bridge = Directory(credential=None, config={"server_url": "https://dav.example/"})
    backend = CardDavDirectoryBackend(bridge)
    backend.__dict__["http"] = http
    return backend


def test_native_httpx2_multistatus_and_case_insensitive_redirect() -> None:
    """DAV accepts native 207 after HttpClient follows a mixed-case Location header."""

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/root":
            return httpx2.Response(302, headers={"LoCaTiOn": "/addressbooks/"})
        return httpx2.Response(207, content=b"<d:multistatus xmlns:d='DAV:'/>")

    with mock_transport(respond) as requests:
        response = _backend_with_http(HttpClient())._request("PROPFIND", "https://dav.example/root", "")

    assert response.status_code == 207
    assert [str(request.url) for request in requests] == ["https://dav.example/root", "https://dav.example/addressbooks/"]


def _unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("secret=private", request=request)


@pytest.mark.parametrize(
    ("answer", "message", "transient", "wait"),
    [
        pytest.param(
            lambda request: httpx2.Response(503, content=b"secret=private", headers={"Retry-After": "30"}),
            "CardDAV is temporarily unavailable (HTTP 503).",
            True,
            timedelta(seconds=30),
            id="outage-waits",
        ),
        pytest.param(
            lambda request: httpx2.Response(429, content=b"secret=private"),
            "CardDAV is rate limiting requests (HTTP 429).",
            True,
            None,
            id="rate-limit",
        ),
        pytest.param(
            lambda request: httpx2.Response(401, content=b"secret=private"),
            "CardDAV refused the request (HTTP 401).",
            False,
            None,
            id="refusal",
        ),
        pytest.param(_unreachable, "CardDAV could not be reached.", True, None, id="unreachable"),
        pytest.param(
            lambda request: httpx2.Response(207, content=b"secret=private", headers={"Content-Encoding": "gzip"}),
            "CardDAV returned an unreadable response.",
            False,
            None,
            id="undecodable-multistatus",
        ),
    ],
)
def test_a_dav_refusal_is_classified_by_integrates_rule_and_never_repeats_the_server(
    answer: Any, message: str, transient: bool, wait: timedelta | None
) -> None:
    with mock_transport(answer), pytest.raises(CardDavError) as refused:
        _backend_with_http(HttpClient())._request("PROPFIND", "https://dav.example/books/", "")

    assert isinstance(refused.value, IntegrationError)
    assert (refused.value.public_message, refused.value.transient, refused.value.retry_after) == (
        message,
        transient,
        wait,
    )


@pytest.mark.parametrize(
    ("answer", "transient", "wait"),
    [
        pytest.param(
            lambda request: httpx2.Response(503, headers={"Retry-After": "30"}),
            True,
            timedelta(seconds=30),
            id="outage-waits",
        ),
        pytest.param(lambda request: httpx2.Response(404), False, None, id="missing"),
        pytest.param(_unreachable, True, None, id="unreachable"),
    ],
)
def test_a_photo_refusal_is_the_photo_error_classified_by_integrates_rule(
    answer: Any, transient: bool, wait: timedelta | None
) -> None:
    contact = ParsedCard(photo=ParsedPhoto(uri="https://dav.example/books/photo.jpg", mime="image/jpeg"))

    with mock_transport(answer), pytest.raises(CardDavError) as refused:
        _backend_with_http(HttpClient())._resolve_photo(contact, collection="https://dav.example/books/")

    assert (refused.value.public_message, refused.value.transient, refused.value.retry_after) == (
        "The vCard photo could not be retrieved.",
        transient,
        wait,
    )


def test_photo_download_is_capped_and_uses_the_collection_origin(settings: Any) -> None:
    """Photo downloads preserve vCard MIME and use storage's image cap over the SSRF-safe client."""

    class FakeHttp:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            assert (method, url) == ("GET", "https://dav.example/books/photo.jpg")
            assert kwargs["max_bytes"] == settings.ANGEE_STORAGE_IMAGE_MAX_BYTES
            assert kwargs["allow_private"] is False
            assert not kwargs.get("follow_redirects") and not kwargs.get("same_origin_redirects")
            return httpx2.Response(200, content=b"photo")

    contact = ParsedContact(
        display_name="One",
        photo=ParsedPhoto(uri="https://dav.example/books/photo.jpg", mime="image/jpeg"),
    )
    resolved = _backend_with_http(FakeHttp())._resolve_photo(contact, collection="https://dav.example/books/")

    assert resolved.photo == ParsedPhoto(data=b"photo", mime="image/jpeg")


def _load_vendor() -> Any:
    """Load the addon's master tier, its ``carddav`` vendor, as provisioning does."""

    return Resource.objects.load_addons(
        (apps.get_app_config("parties_integrate_carddav"),), tiers=[Resource.Tier.MASTER], allow_non_dev=True
    )


def test_the_carddav_vendor_is_seeded_before_any_directory_and_adopted_by_slug() -> None:
    with system_context(reason="test.parties.carddav.vendor"):
        Vendor.objects.create(slug="carddav", display_name="carddav")
    _load_vendor()
    _load_vendor()
    with system_context(reason="test.parties.carddav.vendor"):
        assert list(Vendor.objects.values_list("slug", "display_name", "icon")) == [
            ("carddav", "CardDAV", "address-book")
        ]


def test_connecting_before_the_vendor_is_seeded_says_to_load_resources_and_keeps_nothing() -> None:
    """An operator-readable refusal, reaching the connect form, that says what to load."""

    admin = parties_graphql._platform_admin("carddav-unseeded-admin")
    with pytest.raises(ValidationError, match=r"vendor is not loaded.*`manage\.py resources load`"):
        _connect(admin)
    with system_context(reason="test.parties.carddav.unseeded"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_a_caller_who_may_not_create_a_directory_is_refused_before_the_connection_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: pytest.fail("tested"))
    with system_context(reason="test.parties.carddav.member"):
        member = get_user_model().objects.create_user(username="carddav-member", email="member@example.com")
    with pytest.raises(PermissionDenied):
        _connect(member)
    with system_context(reason="test.parties.carddav.member.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_each_connection_of_one_name_gets_a_credential_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential's name carries its directory's id, so a second connection, or a reconnect, never reuses one."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: "Connected.")
    admin = parties_graphql._platform_admin("carddav-twice-admin")
    first = _connect(admin)

    second = _connect(admin, material={"username": "grace@example.com", "password": "another-password"})

    with system_context(reason="test.parties.carddav.twice.verify"):
        assert second.credential_id != first.credential_id
        names = dict(Credential._base_manager.values_list("pk", "name"))
        assert names == {
            first.credential_id: f"CardDAV — Ada Contacts ({first.sqid})",
            second.credential_id: f"CardDAV — Ada Contacts ({second.sqid})",
        }
        credential = Credential._base_manager.get(pk=first.credential_id)
        assert credential.reveal() == {"username": "ada@example.com", "password": "carddav-password"}


def test_connecting_inside_a_callers_transaction_is_refused_before_the_connection_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connection test reaches the network, so it never runs inside a transaction a caller holds open."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: pytest.fail("tested"))
    admin = parties_graphql._platform_admin("carddav-atomic-admin")
    with transaction.atomic(), pytest.raises(RuntimeError, match="outside any transaction"):
        _connect(admin)
    with system_context(reason="test.parties.carddav.atomic.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_a_configuration_error_other_than_an_unloaded_vendor_reaches_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a missing vendor row reads as "load resources"; any other configuration error stays itself."""

    _load_vendor()

    def broken(directory: Any, *args: Any, **kwargs: Any) -> Any:
        raise ImproperlyConfigured("A broken default.")

    monkeypatch.setattr(Directory, "prepare_impl_defaults", broken)
    admin = parties_graphql._platform_admin("carddav-broken-admin")
    with pytest.raises(ImproperlyConfigured, match="A broken default"):
        _connect(admin)


@pytest.mark.parametrize(
    ("config", "unknown"),
    [
        ({"server_url": "https://dav.example.com/", "sever_url": "https://dav.example.com/"}, "sever_url"),
        ({"server_url": "https://dav.example.com/", "streams": {"events": {}}}, "streams.events"),
        (
            {"server_url": "https://dav.example.com/", "streams": {"contacts": {"people": {"local_delet": "x"}}}},
            "streams.contacts.people.local_delet",
        ),
    ],
)
def test_connecting_refuses_a_setting_its_backend_does_not_know_before_the_connection_test(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any], unknown: str
) -> None:
    """Connect is where a person's configuration is edited: a mistyped key is refused there, nothing saved."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: pytest.fail("tested"))
    admin = parties_graphql._platform_admin("carddav-typo-admin")

    with pytest.raises(ValidationError, match=rf"{unknown}\b"):
        _connect(admin, config=config)

    with system_context(reason="test.parties.carddav.typo.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_a_directory_that_writes_back_refuses_to_adopt_existing_people(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a pull-only directory may bind a contact to an existing person: CardDAV refuses the option on every edit."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: pytest.fail("tested"))
    admin = parties_graphql._platform_admin("carddav-adopt-admin")
    adopting = {"server_url": "https://dav.example.com/", "adopt_existing_people": True}

    with pytest.raises(ValidationError, match="writes back"):
        _connect(admin, config=adopting)

    with system_context(reason="test.parties.carddav.adopt.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()
        directory = make_integration("carddav-adopting", model=Directory, backend_class="carddav", owner=admin)
        directory.config = adopting
        with pytest.raises(ValidationError, match="writes back"):
            directory.save(update_fields=["config"])


def test_connecting_with_an_account_address_someone_else_holds_connects_and_leaves_a_claim(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The connection is valid; the owner's person only claims the account address, for review.

    The address is another account's sign-in address and that account has no
    person yet, so nobody may take it.
    """

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: "Connected.")
    with system_context(reason="test.parties.carddav.held.account"):
        get_user_model().objects.create_user(username="carddav-colleague", email="ada@example.com")
    admin = parties_graphql._platform_admin("carddav-held-admin")

    directory = _connect(admin)

    assert not [entry for entry in caplog.records if entry.levelno >= logging.ERROR]
    with system_context(reason="test.parties.carddav.held.verify"):
        assert Directory._base_manager.filter(pk=directory.pk).exists()
        handle = Handle._base_manager.get(platform="email", value="ada@example.com")
        assert (handle.party_id, handle.controller_id) == (None, None)
        person = Person.objects.for_user(admin)
        claim = Party.statements.filter(subject_id=person.pk, handle=handle).current().get()
        assert (claim.status, claim.source_kind, claim.confidence) == ("asserted", "person", Party.CLAIM_CONFIDENCE)


def test_an_unknown_backend_is_refused_as_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend is resolved before anything else, never inside the handling of another error."""

    _load_vendor()
    admin = parties_graphql._platform_admin("carddav-unknown-admin")

    with pytest.raises(ImproperlyConfigured, match="no_such_backend") as raised:
        _connect(admin, backend_class="no_such_backend")

    chain, error = [], raised.value
    while error is not None:
        chain.append(error)
        error = error.__context__
    assert [type(row) for row in chain if isinstance(row, ImproperlyConfigured)] == [ImproperlyConfigured]
    with system_context(reason="test.parties.carddav.unknown.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_a_backend_that_requires_discovery_leaves_the_first_sync_to_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Discovery schedules the first sync once it finishes; connect requests none it would refuse."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: "Connected.")
    monkeypatch.setattr(CardDavDirectoryBackend, "requires_connection_discovery", True)
    admin = parties_graphql._platform_admin("carddav-discovery-admin")

    with caplog.at_level(logging.WARNING, logger="angee.parties.managers"):
        directory = _connect(admin)

    assert "first sync" not in caplog.text
    with system_context(reason="test.parties.carddav.discovery.verify"):
        row = Directory._base_manager.get(pk=directory.pk)
        assert row.discovery_pending and not row.discovery_ready
        assert row.sync_stage != Directory.SyncStage.QUEUED


def test_a_first_sync_that_cannot_be_queued_is_logged_and_the_connected_directory_stays(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first sync is requested once the rows commit; a failure to queue it is logged, never raised."""

    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: "Connected.")
    queued: list[bool] = []

    def refuse(directory: Any, **kwargs: Any) -> Any:
        queued.append(connection.in_atomic_block)
        raise RuntimeError("The task broker is down.")

    monkeypatch.setattr(Directory, "request_sync", refuse)
    admin = parties_graphql._platform_admin("carddav-unqueued-admin")

    with caplog.at_level(logging.WARNING, logger="angee.parties.managers"):
        directory = _connect(admin)

    assert queued == [False]
    assert "first sync" in caplog.text
    with system_context(reason="test.parties.carddav.unqueued.verify"):
        assert Directory._base_manager.filter(pk=directory.pk).exists()
        assert Credential._base_manager.filter(pk=directory.credential_id).exists()


def test_a_refused_connection_test_writes_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend's refusal runs before any transaction and reaches the operator as its own message."""

    _load_vendor()
    test_atomic_states: list[bool] = []

    def refuse(backend: CardDavDirectoryBackend) -> str:
        del backend
        test_atomic_states.append(connection.in_atomic_block)
        raise CardDavError("CardDAV PROPFIND returned HTTP 401.")

    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", refuse)
    admin = parties_graphql._platform_admin("carddav-test-failure-admin")

    result = _connect_carddav(admin)

    assert result.errors is not None
    assert result.errors[0].extensions["code"] == "VALIDATION"
    assert result.errors[0].extensions["formErrors"] == ["CardDAV PROPFIND returned HTTP 401."]
    assert test_atomic_states == [False]
    with system_context(reason="test.parties.carddav.test_failure.verify"):
        for model in (Credential, Integration, Directory, Handle, Party, Person, Party.statements.model):
            assert not model._base_manager.exists(), model._meta.label
        assert Vendor.objects.count() == 1


def test_a_credential_the_backend_refuses_is_refused_before_the_connection_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_vendor()
    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", lambda backend: pytest.fail("tested"))
    admin = parties_graphql._platform_admin("carddav-token-admin")
    with pytest.raises(ValidationError, match="username and password"):
        _connect(admin, credential_kind="static_token", material={"api_key": "secret"})
    with system_context(reason="test.parties.carddav.token.verify"):
        assert not Credential._base_manager.exists() and not Directory._base_manager.exists()


def test_a_passed_connection_test_commits_every_owned_row_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passed connection test precedes one complete atomic write phase, which queues the first sync."""

    _load_vendor()
    test_atomic_states: list[bool] = []
    write_atomic_states: list[bool] = []

    def accept(backend: CardDavDirectoryBackend) -> str:
        del backend
        test_atomic_states.append(connection.in_atomic_block)
        return "Connected."

    claim_control = Handle.objects.claim_control

    def atomic_claim_control(user: Any, **kwargs: Any) -> Any:
        write_atomic_states.append(connection.in_atomic_block)
        return claim_control(user, **kwargs)

    monkeypatch.setattr(CardDavDirectoryBackend, "test_connection", accept)
    monkeypatch.setattr(Handle.objects, "claim_control", atomic_claim_control)
    admin = parties_graphql._platform_admin("carddav-test-success-admin")

    result = _connect_carddav(admin)

    assert result.errors is None
    assert result.data is not None
    directory_data = result.data["connect_card_dav_directory"]
    assert directory_data == {
        "id": directory_data["id"],
        "display_name": "Ada Contacts",
        "backend_class": "CARDDAV",
        "lifecycle": "CONNECTED",
        "runtime_status": "OK",
        "config": {"server_url": "https://dav.example.com/"},
    }
    assert test_atomic_states == [False]
    assert write_atomic_states == [True]

    with system_context(reason="test.parties.carddav.test_success.verify"):
        credential = Credential.objects.get()
        vendor = Vendor.objects.get()
        directory = Directory.objects.get()
        integration = Integration.objects.get(pk=directory.pk)
        handle = Handle.objects.get()
        person = Person.objects.get()
        link = Party.statements.handles().get()

        assert credential.user_id == admin.pk
        assert credential.name == f"CardDAV — Ada Contacts ({directory.sqid})"
        assert credential.reveal() == {
            "username": "ada@example.com",
            "password": "carddav-password",
        }
        assert (vendor.slug, vendor.display_name) == ("carddav", "CardDAV")
        assert directory.vendor_id == vendor.pk
        assert directory.sync_stage == Directory.SyncStage.QUEUED
        assert integration.credential_id == credential.pk
        assert integration.owner_id == admin.pk
        assert directory.backend_class == "carddav"
        assert directory.lifecycle == "connected"
        assert directory.config == {"server_url": "https://dav.example.com/"}
        assert handle.controller_id == admin.pk
        assert (handle.platform, handle.value) == ("email", "ada@example.com")
        assert person.user_id == admin.pk
        assert link.subject_id == person.pk
        assert link.handle_id == handle.pk
        assert link.source_kind == "person"
        assert link.asserted_by == f"auth/user:{admin.pk}"
        assert link.rank == link.StatementRank.NORMAL
        assert link.link_id is None
        assert link.evidence == [{"model": integration._meta.label, "id": integration.public_id, "pointer": ""}]
        assert link.confidence == 1.0
        assert link.is_confirmed is False


def test_the_connection_message_names_the_host_and_port_never_the_credentials_in_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server URL may carry a user and password; the message keeps only the host name and port."""

    monkeypatch.setattr(CardDavDirectoryBackend, "discover", lambda backend: [])
    url = "https://ada:secret@dav.example:8443/dav/"
    directory = Directory(credential=None, backend_class="carddav", config={"server_url": url})
    assert directory.test_connection() == "Connected to dav.example:8443."


@pytest.mark.parametrize(("kind", "admitted"), [("basic_auth", True), ("static_token", False)])
def test_carddav_admits_only_basic_auth_credentials(kind: str, admitted: bool) -> None:
    """The backend signs in with a username and password only: one rule, the picker's answer and the attach refusal."""

    directory = Directory(credential=None, backend_class="carddav", config={"server_url": "https://dav.example/"})
    credential = Credential(kind=kind)
    refusal = "A CardDAV directory signs in with a username and password."
    assert directory.credential_blocker(credential) == (None if admitted else refusal)
    if admitted:
        directory.admit_credential(credential)
    else:
        with pytest.raises(ValidationError, match="username and password"):
            directory.admit_credential(credential)


def _connect(admin: Any, **overrides: Any) -> Any:
    """Connect one stable account through the parties verb, as a vendor's connect mutation does."""

    values = {
        "backend_class": "carddav",
        "display_name": "Ada Contacts",
        "credential_kind": "basic_auth",
        "material": {"username": "ada@example.com", "password": "carddav-password"},
        "config": {"server_url": "https://dav.example.com/"},
    }
    return Directory.objects.connect(admin, **{**values, **overrides})


def _connect_carddav(admin: Any) -> Any:
    """Execute the CardDAV connect mutation with one stable account payload."""

    return execute_schema(
        addon_schema(parties_schema.schemas, "console", carddav_schema.schemas),
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
    retained = vobject.readOne(contact.raw_vcard)
    assert "photo" not in retained.contents
    assert retained.org.value == ["Analytical Engines", "Research"]


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

    vcard = "BEGIN:VCARD\nVERSION:3.0\nUID:x\nFN:X\nPHOTO;VALUE=uri:https://example.com/a.jpg\nEND:VCARD"
    contact = _parse(vcard)
    assert contact.photo is not None
    assert contact.photo.data is None
    assert contact.photo.uri == "https://example.com/a.jpg"


def test_uid_falls_back_to_href_without_using_the_display_name() -> None:
    """Cards without UID remain distinct even when their display names match."""

    no_uid = "BEGIN:VCARD\nVERSION:3.0\nFN:Grace Hopper\nEMAIL:grace@example.com\nEND:VCARD"
    assert _parse(no_uid, href="/ab/grace.vcf").uid == "/ab/grace.vcf"
    assert _parse(no_uid, href="/ab/another-grace.vcf").uid == "/ab/another-grace.vcf"

    bare = "BEGIN:VCARD\nVERSION:3.0\nEMAIL:anon@example.com\nEND:VCARD"
    assert _parse(bare, href="/ab/anon.vcf").uid == "/ab/anon.vcf"


def test_no_stable_key_yields_empty_uid() -> None:
    """A display name alone cannot supply a missing UID and resource href."""

    bare = "BEGIN:VCARD\nVERSION:3.0\nFN:Anonymous\nEMAIL:anon@example.com\nEND:VCARD"
    assert _parse(bare, href="").uid == ""


@pytest.mark.parametrize("method", ["PROPFIND", "REPORT", "GET", "PUT", "DELETE"])
def test_authenticated_dav_destinations_require_configured_origin(method):
    class NoRequests:
        def request(self, *args, **kwargs):
            raise AssertionError("Cross-origin request must be refused before transport")
    backend = _backend_with_http(NoRequests())
    with pytest.raises(CardDavError, match="configured server origin"):
        backend._request(method, "https://attacker.example/collection/card.vcf", "")
