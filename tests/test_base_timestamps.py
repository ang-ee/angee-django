"""The creation stamp keeps a time its creating owner states at insert, and costs a host no migration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.apps import apps
from django.db import models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ProjectState
from django.db.migrations.writer import MigrationWriter
from django.db.models import F
from django.db.models.functions import Coalesce, Greatest, Least
from django.utils import timezone
from rebac import system_context

from angee.base.models.timestamps import CreatedAtField, TimestampMixin
from tests.core_persistence import OwnedRow, TrashRow

SOURCE_CREATED = datetime(2019, 4, 2, 10, 30, tzinfo=UTC)
"""When an invented source says its record came into being."""


@pytest.mark.django_db
def test_a_creation_time_stated_at_insert_is_kept_and_none_stated_stamps_the_insert() -> None:
    before = timezone.now()
    stated = TrashRow.objects.create(title="from a source", created_at=SOURCE_CREATED)
    unstated = TrashRow.objects.create(title="made here")

    stated.refresh_from_db()
    unstated.refresh_from_db()
    assert stated.created_at == SOURCE_CREATED
    assert stated.updated_at >= before
    assert before <= unstated.created_at <= unstated.updated_at


@pytest.mark.django_db
def test_a_creation_time_in_the_future_is_clamped_to_the_insert() -> None:
    before = timezone.now()
    row = TrashRow.objects.create(created_at=before + timedelta(days=30))

    row.refresh_from_db()
    assert before <= row.created_at <= timezone.now()
    assert row.created_at <= row.updated_at


@pytest.mark.django_db
def test_a_save_of_an_existing_row_stamps_nothing_on_its_creation_time() -> None:
    row = TrashRow.objects.create(created_at=SOURCE_CREATED)
    row.title = "edited"
    row.save()
    TrashRow.objects.get(pk=row.pk).save(update_fields=["title", "updated_at"])

    row.refresh_from_db()
    assert (row.title, row.created_at) == ("edited", SOURCE_CREATED)
    assert row.updated_at > SOURCE_CREATED


@pytest.mark.django_db
def test_a_bulk_insert_keeps_a_stated_creation_time_too() -> None:
    TrashRow.objects.bulk_create([TrashRow(title="stated", created_at=SOURCE_CREATED), TrashRow(title="unstated")])

    assert TrashRow.objects.get(title="stated").created_at == SOURCE_CREATED
    assert TrashRow.objects.get(title="unstated").created_at > SOURCE_CREATED


@pytest.mark.django_db
def test_historys_creation_snapshot_carries_the_stated_time_and_its_date_is_the_recording_moment() -> None:
    before = timezone.now()
    with system_context(reason="tests.timestamps.history"):
        row = OwnedRow.objects.create(title="from a source", created_at=SOURCE_CREATED)

    created = row.history.model.objects.get(id=row.pk, history_type="+")
    assert created.created_at == SOURCE_CREATED
    assert created.history_date >= before


@pytest.mark.django_db
def test_an_expression_combines_the_creation_time_with_any_other_datetime_in_either_order() -> None:
    TrashRow.objects.create(created_at=SOURCE_CREATED)

    first = TrashRow.objects.annotate(at=Coalesce("updated_at", "created_at"), low=Least("updated_at", "created_at"))
    second = TrashRow.objects.annotate(
        at=Coalesce("created_at", "updated_at"), high=Greatest("created_at", "updated_at")
    )

    assert first.get().low == SOURCE_CREATED
    assert second.get().at == SOURCE_CREATED
    assert first.get().at == second.get().high > SOURCE_CREATED
    assert TrashRow.objects.filter(created_at__lt=F("updated_at")).count() == 1


def test_the_field_deconstructs_as_djangos_own_so_a_migration_writes_the_field_hosts_already_hold() -> None:
    field = CreatedAtField(auto_now_add=True, db_index=True)
    plain = models.DateTimeField(auto_now_add=True, db_index=True)

    assert field.deconstruct()[1:] == plain.deconstruct()[1:]
    assert field.deconstruct()[1] == "django.db.models.DateTimeField"
    assert MigrationWriter.serialize(field) == MigrationWriter.serialize(plain)
    assert isinstance(TimestampMixin._meta.get_field("created_at"), CreatedAtField)


def test_no_model_of_the_host_needs_a_migration_for_the_field() -> None:
    """Every table and history table authored with Django's own field compares unchanged."""

    current = ProjectState.from_apps(apps)
    authored = current.clone()
    replaced = 0
    for state in authored.models.values():
        field = state.fields.get("created_at")
        if isinstance(field, CreatedAtField):
            name, path, args, kwargs = field.deconstruct()
            state.fields["created_at"] = models.DateTimeField(*args, **kwargs)
            replaced += 1
    assert replaced > 50

    changes = MigrationAutodetector(authored, current).changes(graph=MigrationGraph())

    assert changes == {}
