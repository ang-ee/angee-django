"""Connecting a HubSpot account through the console schema, against the scripted account.

The mutation runs ``Directory.objects.connect``: the service key is tested with
HubSpot before anything is saved, the credential and the directory are created
together, and connection discovery learns the account and arms the first sync,
which the scheduler then requests. The scripted HubSpot answers at the transport
seam; the task broker's send is captured, and a captured task runs as a worker
would run it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from rebac import system_context

from angee.graphql.testing.documents import execute_schema
from angee.integrate import scheduler
from angee.integrate.constants import DISCOVERY_TASK
from angee.integrate.tasks import discover_connection
from angee.jobs.celery import app as celery_app
from angee.messaging.testing.models import Directory
from angee.parties import schema as parties_schema
from angee.parties_integrate_hubspot import schema as hubspot_schema
from angee.parties_integrate_hubspot.testing.server import HUB_ID, TOKEN, HubSpotServer, serve, service_key
from angee.resources.testing.models import Resource
from tests.conftest import Credential, Vendor, addon_schema, create_platform_admin

pytestmark = pytest.mark.django_db(transaction=True)

SYNC_TASK = "integrate.sync_bridge_now"
REJECTED = "HubSpot rejected the access token. Enter a valid HubSpot service key."
CHECK_THE_KEY = "This key lacks a service key's usual shape: check it for extra or missing characters."
NAME_TAKEN = "An account is already connected under that name; choose another name."
CONNECT = """
mutation ConnectHubSpot($name: String!, $token: String!) {
  connect_hubspot_directory(name: $name, token: $token) {
    id
    display_name
    backend_class
    lifecycle
    config
  }
}
"""


@pytest.fixture
def hubspot() -> Iterator[HubSpotServer]:
    """The scripted account, served, with the addon's vendor row loaded as provisioning loads it."""

    Resource.objects.load_addons(
        (apps.get_app_config("parties_integrate_hubspot"),), tiers=[Resource.Tier.MASTER], allow_non_dev=True
    )
    with serve(HubSpotServer()) as server:
        yield server


@pytest.fixture
def sent_tasks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Each task the broker was asked to send, by name, with its keyword arguments."""

    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: sent.append((name, options["kwargs"])))
    return sent


def connect(user: Any, *, name: str = "Example contacts", token: str = TOKEN) -> Any:
    """Run the console's HubSpot connect mutation as ``user``."""

    schema = addon_schema(parties_schema.schemas, "console", hubspot_schema.schemas)
    return execute_schema(schema, CONNECT, {"name": name, "token": token}, user=user)


def saved_rows() -> tuple[int, int]:
    """Count the credentials and directories that exist, whoever may read them."""

    with system_context(reason="test.parties.hubspot.connect.rows"):
        return Credential._base_manager.count(), Directory._base_manager.count()


def test_an_admin_connects_an_account_tested_first_then_discovered_and_queued_for_its_first_sync(
    hubspot: HubSpotServer, sent_tasks: list[tuple[str, dict[str, Any]]]
) -> None:
    admin = create_platform_admin("hubspot-connect-admin")

    result = connect(admin)

    assert result.errors is None
    assert result.data is not None
    connected = result.data["connect_hubspot_directory"]
    assert {key: connected[key] for key in ("display_name", "backend_class", "lifecycle", "config")} == {
        "display_name": "Example contacts",
        "backend_class": "HUBSPOT",
        "lifecycle": "CONNECTED",
        "config": {},
    }
    # The connection test is the one request before the rows are written: one contact, no body.
    (tested,) = hubspot.requests
    assert (tested.method, tested.url.path, tested.url.params["limit"]) == ("GET", "/crm/objects/2026-09/contacts", "1")
    with system_context(reason="test.parties.hubspot.connect.verify"):
        directory = Directory.objects.get()
        credential = Credential.objects.get()
        assert (credential.user_id, credential.kind, credential.name) == (
            admin.pk,
            "static_token",
            "HubSpot — Example contacts",
        )
        assert credential.reveal() == {"api_key": TOKEN}
        assert (directory.owner_id, directory.credential_id, directory.backend_class) == (
            admin.pk,
            credential.pk,
            "hubspot",
        )
        assert directory.vendor_id == Vendor.objects.get(slug="hubspot").pk
        assert not directory.discovery_ready
    # A directory that discovers its connection syncs once discovery has learned the account.
    ((name, discovery),) = sent_tasks
    assert (name, discovery["integration_pk"]) == (DISCOVERY_TASK, directory.pk)

    assert discover_connection(**discovery) == {"ok": True, "reason": "applied"}

    with system_context(reason="test.parties.hubspot.connect.discovered"):
        directory.refresh_from_db()
    assert (directory.subscription_state["hub_id"], directory.subscription_state["ui_domain"]) == (
        HUB_ID,
        "app.hubspot.com",
    )
    assert directory.discovery_ready
    # Discovery arms the first poll, and the scheduler's next tick requests the first sync.
    assert directory.next_sync_at is not None and directory.next_sync_at <= timezone.now()
    sent_tasks.clear()
    scheduler.enqueue_due_bridges()
    assert [(name, kwargs["pk"]) for name, kwargs in sent_tasks] == [(SYNC_TASK, directory.pk)]


def test_a_caller_who_is_no_platform_admin_is_refused_before_anything_is_tested_or_saved(
    hubspot: HubSpotServer, sent_tasks: list[tuple[str, dict[str, Any]]]
) -> None:
    with system_context(reason="test.parties.hubspot.connect.member"):
        member = get_user_model().objects.create_user(username="hubspot-member", email="member@example.com")

    result = connect(member)

    assert result.errors is not None
    assert result.errors[0].extensions == {"code": "PERMISSION_DENIED"}
    assert (hubspot.requests, sent_tasks, saved_rows()) == ([], [], (0, 0))


@pytest.mark.parametrize(
    ("token", "message"),
    [
        pytest.param(service_key("f"), REJECTED, id="a-key-of-the-usual-shape"),
        pytest.param(f"{TOKEN}7", f"{REJECTED} {CHECK_THE_KEY}", id="a-stray-digit"),
    ],
)
def test_a_key_hubspot_rejects_answers_its_operator_message_and_saves_nothing(
    hubspot: HubSpotServer, sent_tasks: list[tuple[str, dict[str, Any]]], token: str, message: str
) -> None:
    result = connect(create_platform_admin("hubspot-rejected-admin"), token=token)

    assert result.errors is not None
    assert result.errors[0].extensions["code"] == "VALIDATION"
    assert result.errors[0].extensions["formErrors"] == [message]
    assert (len(hubspot.requests), sent_tasks, saved_rows()) == (1, [], (0, 0))


def test_a_second_connection_gets_a_credential_of_its_own_and_a_name_in_use_is_refused(
    hubspot: HubSpotServer, sent_tasks: list[tuple[str, dict[str, Any]]]
) -> None:
    admin = create_platform_admin("hubspot-twice-admin")
    assert connect(admin).errors is None

    taken = connect(admin)
    assert taken.errors is not None
    assert taken.errors[0].extensions["formErrors"] == [NAME_TAKEN]
    assert saved_rows() == (1, 1)

    assert connect(admin, name="Second contacts").errors is None
    with system_context(reason="test.parties.hubspot.connect.twice"):
        directories = list(Directory.objects.order_by("pk"))
        assert [directory.display_name for directory in directories] == ["Example contacts", "Second contacts"]
        credentials = {directory.credential_id for directory in directories}
        assert len(credentials) == 2
        assert sorted(Credential.objects.filter(pk__in=credentials).values_list("name", flat=True)) == [
            "HubSpot — Example contacts",
            "HubSpot — Second contacts",
        ]
    assert sorted(kwargs["integration_pk"] for _name, kwargs in sent_tasks) == [
        directory.pk for directory in directories
    ]
