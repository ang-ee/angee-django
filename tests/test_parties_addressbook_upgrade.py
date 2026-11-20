"""The address-book migration template, applied through Django's executor to today's rows.

Today's rows carry ``Folder.source_href`` and its unique constraint: the test adds
both to the folder table under a migration state derived from the installed
models, so the template's callables run on historical models, never live ones.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rebac import system_context

from angee.integrate.states import LinkStatus, StreamDirection, StreamKind, StreamState
from angee.integrate.streams import advance_stream, sync_bridge
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Folder, Person, RelationshipKind
from angee.parties.compose_migrations.parties import addressbook_links
from angee.parties_integrate_carddav.backend import CardDavDirectoryBackend
from tests.conftest import current_stream, make_integration
from tests.test_parties_carddav_sync import _BASE, _BOOK, FakeDav, _card

pytestmark = pytest.mark.django_db(transaction=True)

_STALE = f"{_BASE}books/stale/"
_OTHER = f"{_BASE}books/other/"
_LONG = f"{_BASE}books/{'x' * 600}/"
_WIDE = f"{_BASE}books/{'w' * 373}/"  # 400 characters: longer than the old 255-character partition.
_NAME = "0099_addressbook_links"


def _legacy_state() -> ProjectState:
    """Add the folder's href column and constraint to the table, returning today's migration state."""

    state = ProjectState.from_apps(apps)
    folder = state.models["parties", "folder"]
    folder.fields["source_href"] = models.CharField(max_length=1024, blank=True, default="")
    legacy = state.apps.get_model("parties", "Folder")
    with connection.schema_editor() as editor:
        editor.add_field(legacy, legacy._meta.get_field("source_href"))
    constraint = models.UniqueConstraint(
        fields=("directory", "source_href"), condition=~models.Q(source_href=""), name="uq_folder_directory_source"
    )
    folder.options["constraints"] = [*folder.options.get("constraints", ()), constraint]
    state.reload_model("parties", "folder", delay=True)
    with connection.schema_editor() as editor:
        editor.add_constraint(state.apps.get_model("parties", "Folder"), constraint)
    return state


def _todays_rows(config: dict[str, Any]) -> dict[str, Any]:
    """Two directories as the previous release left them: folders found by href, contacts streams without containers."""

    one, two = (
        make_integration(
            f"carddav-upgrade-{name}",
            model=Directory,
            backend_class="carddav",
            config={"server_url": _BASE, **config},
        )
        for name in ("one", "two")
    )
    RelationshipKind.objects.create(  # The master row contact ingestion reads.
        slug="employee", name="Employee", inverse_name="Employer", category="professional",
        other_party_kind="organization",
    )
    folders = {
        name: Folder._base_manager.create(name=name.title(), directory=directory, created_by=directory.owner)
        for name, directory in (
            ("contacts", one), ("stale", one), ("other", two), ("manual", one), ("long", one), ("wide", one)
        )
    }
    # The synced book's contacts reach a delta cursor through a scaffold container, then lose it.
    scaffold = current_stream(one, "addressbooks", kind=StreamKind.RECORD_REPLICA)
    holder = RecordLink.objects.observe(scaffold, _BOOK, target=folders["contacts"])
    backend = CardDavDirectoryBackend(one)
    contacts = advance_stream(SyncStream.objects.current(one, backend.contacts_stream(holder)), backend).stream
    synced = RecordLink.objects.get(stream=contacts, external_key="ada")
    assert (synced.status, synced.target.display_name) == (LinkStatus.CURRENT, "Ada Lovelace")
    SyncStream._base_manager.filter(pk=contacts.pk).update(container=None, last_reconciled_at=timezone.now())
    RecordLink._base_manager.filter(pk=holder.pk).delete()
    SyncStream._base_manager.filter(pk=scaffold.pk).delete()
    for directory, href in ((one, _STALE), (two, _OTHER)):
        current_stream(
            directory, "contacts", href, kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
        )
    return {"one": one, "two": two, "folders": folders, "contacts": contacts}


def _snapshot(rows: dict[str, Any]) -> tuple[Any, ...]:
    return (
        list(Folder._base_manager.order_by("pk").values()),
        list(RecordLink._base_manager.filter(stream__key="contacts").order_by("pk").values()),
        list(Person.objects.order_by("pk").values_list("pk", "display_name")),
        SyncStream._base_manager.values_list("cursor", "generation").get(pk=rows["contacts"].pk),
    )


def _upgrade_round_trip(caplog: pytest.LogCaptureFixture, config: dict[str, Any]) -> None:
    assert len(_WIDE) == 400
    assert SyncStream._meta.get_field("partition").max_length == RecordLink._meta.get_field("external_key").max_length
    server = FakeDav()
    server.books[_WIDE] = "Wide"
    server.store(f"{_WIDE}wide.vcf", _card(uid="wide", name="Wide Person"))
    executor = MigrationExecutor(connection)
    migration = addressbook_links.Migration(_NAME, "parties")
    with system_context(reason="test address-book upgrade"), mock_transport(server.handle_request):
        rows = _todays_rows(config)
        before = _legacy_state()
        legacy_columns = True
        try:
            hrefs = {"contacts": _BOOK, "stale": _STALE, "other": _OTHER, "long": _LONG, "wide": _WIDE}
            legacy = before.apps.get_model("parties", "Folder")
            for name, href in hrefs.items():
                legacy._base_manager.filter(pk=rows["folders"][name].pk).update(source_href=href)
            kept = _snapshot(rows)
            with connection.schema_editor() as editor:  # A run cut short before its column changes.
                addressbook_links.adopt_addressbooks(before.apps, editor)
            executor.apply_migration(before.clone(), migration)
            legacy_columns = False

            expected = (
                (rows["one"], {_BOOK: "contacts", _STALE: "stale", _WIDE: "wide"}),
                (rows["two"], {_OTHER: "other"}),
            )
            for directory, books in expected:
                (addressbooks,) = SyncStream.objects.current_for_bridge(directory, "addressbooks")
                assert (addressbooks.kind, addressbooks.direction) == (StreamKind.RECORD_REPLICA, StreamDirection.PULL)
                links = {link.external_key: link for link in RecordLink.objects.filter(stream=addressbooks)}
                assert {key: link.target for key, link in links.items()} == {
                    href: rows["folders"][name] for href, name in books.items()
                }
                for href, link in links.items():
                    held = SyncStream._base_manager.filter(integration_id=directory.pk, key="contacts", partition=href)
                    # The wide book's contacts stream could not exist before the partition widened.
                    assert set(held.values_list("container_id", flat=True)) == (set() if href == _WIDE else {link.pk})
            for unadopted in ("manual", "long"):
                assert not RecordLink.objects.filter(RecordLink.record_filter(rows["folders"][unadopted])).exists()
            long_folder = rows["folders"]["long"].pk
            assert any(
                f"Folder {long_folder} keeps no address-book link" in record.getMessage() for record in caplog.records
            )
            assert _snapshot(rows) == kept

            server.requests.clear()
            # Both listed books state their names once; only the wide book's new contacts stream fetches a card.
            assert sync_bridge(rows["one"]) == 3
            fetched = [request[1] for request in server.requests if "addressbook-multiget" in request[3]]
            assert _BOOK not in fetched
            wide = SyncStream.objects.current_for_bridge(rows["one"], "contacts").get(partition=_WIDE)
            assert wide.container == RecordLink.objects.get(stream__key="addressbooks", external_key=_WIDE)
            assert RecordLink.objects.get(stream=wide, external_key="wide").target.display_name == "Wide Person"
            assert not [request for request in server.requests if request[1].startswith(_STALE)]
            stale = RecordLink.objects.get(stream__key="addressbooks", external_key=_STALE)
            stale_contacts = SyncStream.objects.current_for_bridge(rows["one"], "contacts").get(partition=_STALE)
            assert (stale.status, stale.absence_count, stale_contacts.state) == (
                LinkStatus.UNAVAILABLE,
                1,
                StreamState.OPEN,
            )
            assert sync_bridge(rows["one"]) == 0
            stale.refresh_from_db()
            stale_contacts.refresh_from_db()
            assert (stale.status, stale_contacts.state) == (LinkStatus.TOMBSTONE, StreamState.RETIRED)
            people = Person.objects.exclude(display_name="Wide Person").order_by("pk")
            assert list(people.values_list("pk", "display_name")) == kept[2]

            executor.unapply_migration(before.clone(), migration)
            legacy_columns = True
            assert dict(legacy._base_manager.order_by().values_list("name", "source_href")) == {
                "Contacts": _BOOK,
                "Stale": _STALE,
                "Other": _OTHER,
                "Manual": "",
                "Long": "",
                "Wide": _WIDE,
            }
        finally:
            if legacy_columns:
                executor.apply_migration(before.clone(), migration)
            MigrationRecorder(connection).record_unapplied("parties", _NAME)


def test_the_address_book_template_upgrades_todays_rows_and_reverses(caplog: pytest.LogCaptureFixture) -> None:
    """Two directories, a stale book, a folder without an href, an over-long href, a second run and the reverse."""

    _upgrade_round_trip(caplog, {})


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Runs on the PostgreSQL lane.")
def test_the_address_book_template_upgrades_in_one_postgresql_migration(caplog: pytest.LogCaptureFixture) -> None:
    """One atomic migration without pending trigger events, then first syncs on the parallel partition pool."""

    _upgrade_round_trip(caplog, {"sync_parallelism": 4})
