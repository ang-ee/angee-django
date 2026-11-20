"""A creation time is kept only when its creating owner states it for the insert, at no migration."""

from __future__ import annotations

import copy
import pickle
from datetime import UTC, datetime, timedelta

import pytest
from django.apps import apps
from django.core import serializers
from django.db import models, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ProjectState
from django.db.migrations.writer import MigrationWriter
from django.db.models import F
from django.db.models.functions import Coalesce, Greatest, Least
from django.forms.models import model_to_dict
from django.utils import timezone
from rebac import system_context

from angee.base.models.timestamps import CreatedAtField, TimestampMixin
from tests.core_persistence import OwnedRow, TrashRow

SOURCE_CREATED = datetime(2019, 4, 2, 10, 30, tzinfo=UTC)
"""When an invented source says its record came into being."""


def stated(title: str = "from a source", at: datetime = SOURCE_CREATED) -> TrashRow:
    """Insert a row its creating owner dated ``at``."""

    row = TrashRow(title=title).stamp_created_at(at)
    row.save()
    return row


def imported() -> TrashRow:
    """Return a saved row whose stored creation time is the source's, as an import leaves it."""

    row = TrashRow.objects.create(title="from a source")
    TrashRow.objects.filter(pk=row.pk).update(created_at=SOURCE_CREATED)
    row.refresh_from_db()
    return row


@pytest.mark.django_db
def test_a_creation_time_stated_for_the_insert_is_kept_and_none_stated_stamps_the_insert() -> None:
    before = timezone.now()
    kept = stated()
    unstated = TrashRow(title="made here").stamp_created_at(None)
    unstated.save()

    kept.refresh_from_db()
    unstated.refresh_from_db()
    assert kept.created_at == SOURCE_CREATED
    assert kept.updated_at >= before
    assert before <= unstated.created_at <= unstated.updated_at


@pytest.mark.django_db
def test_a_creation_time_in_the_future_is_clamped_to_the_insert() -> None:
    before = timezone.now()
    row = stated(at=before + timedelta(days=30))

    row.refresh_from_db()
    assert before <= row.created_at <= timezone.now()
    assert row.created_at <= row.updated_at


@pytest.mark.django_db
def test_setting_the_attribute_alone_states_nothing_and_the_insert_is_stamped() -> None:
    before = timezone.now()
    row = TrashRow.objects.create(title="set on the attribute", created_at=SOURCE_CREATED)

    row.refresh_from_db()
    assert before <= row.created_at <= timezone.now()


@pytest.mark.django_db
def test_a_saved_row_cannot_be_dated_through_its_insert_and_its_saves_stamp_nothing() -> None:
    row = stated()
    with pytest.raises(ValueError, match="before the row is saved"):
        row.stamp_created_at(SOURCE_CREATED - timedelta(days=1))
    row.title = "edited"
    row.save()
    TrashRow.objects.get(pk=row.pk).save(update_fields=["title", "updated_at"])

    row.refresh_from_db()
    assert (row.title, row.created_at) == ("edited", SOURCE_CREATED)
    assert row.updated_at > SOURCE_CREATED


@pytest.mark.django_db
def test_a_saved_row_cloned_by_clearing_its_key_stamps_its_own_insert() -> None:
    original = imported()
    before = timezone.now()
    original._set_pk_val(None)
    original._state.adding = True
    original.save()

    clone = TrashRow.objects.get(pk=original.pk)
    assert before <= clone.created_at <= timezone.now()
    assert TrashRow.objects.filter(created_at=SOURCE_CREATED).count() == 1


@pytest.mark.django_db
def test_a_row_inserted_again_after_its_insert_rolled_back_stamps_the_new_insert() -> None:
    """The changeset claim's recovery (``provenance.context``) re-inserts its instance this way."""

    row = TrashRow(title="claim")
    with pytest.raises(RuntimeError, match="roll back"), transaction.atomic():
        row.save(force_insert=True)
        rolled_back = row.created_at
        raise RuntimeError("roll back")
    row._set_pk_val(None)
    row._state.adding = True
    row.save(force_insert=True)

    row.refresh_from_db()
    assert row.created_at > rolled_back


@pytest.mark.django_db
def test_a_stated_time_is_kept_once_and_an_insert_retried_keeps_it_only_when_stated_again() -> None:
    row = stated()
    assert row.created_at == SOURCE_CREATED
    before = timezone.now()

    row._set_pk_val(None)
    row._state.adding = True
    row.save()
    assert before <= TrashRow.objects.get(pk=row.pk).created_at

    row._set_pk_val(None)
    row._state.adding = True
    row.stamp_created_at(SOURCE_CREATED).save()
    assert TrashRow.objects.get(pk=row.pk).created_at == SOURCE_CREATED


@pytest.mark.django_db
def test_a_row_built_from_another_rows_values_stamps_its_own_insert() -> None:
    original = imported()
    before = timezone.now()
    from_form = TrashRow(**model_to_dict(original, exclude=["id"]))
    from_form.save()
    every_column = TrashRow(**{
        field.attname: getattr(original, field.attname)
        for field in TrashRow._meta.concrete_fields
        if not field.primary_key
    })
    every_column.save()

    for row in (from_form, every_column):
        row.refresh_from_db()
        assert before <= row.created_at <= timezone.now()


@pytest.mark.django_db
def test_a_copied_or_pickled_row_carries_no_stated_time_and_the_original_keeps_its_own() -> None:
    original = TrashRow(title="from a source").stamp_created_at(SOURCE_CREATED)
    before = timezone.now()
    copies = [copy.copy(original), copy.deepcopy(original), pickle.loads(pickle.dumps(original))]
    for row in copies:
        row.save()
    original.save()

    for row in copies:
        row.refresh_from_db()
        assert before <= row.created_at <= timezone.now()
    original.refresh_from_db()
    assert original.created_at == SOURCE_CREATED


@pytest.mark.django_db
def test_a_bulk_insert_keeps_each_stated_creation_time_and_stamps_the_rest() -> None:
    TrashRow.objects.bulk_create([
        TrashRow(title="stated").stamp_created_at(SOURCE_CREATED),
        TrashRow(title="attribute only", created_at=SOURCE_CREATED),
        TrashRow(title="unstated"),
    ])

    assert TrashRow.objects.get(title="stated").created_at == SOURCE_CREATED
    assert TrashRow.objects.get(title="attribute only").created_at > SOURCE_CREATED
    assert TrashRow.objects.get(title="unstated").created_at > SOURCE_CREATED


@pytest.mark.django_db
def test_a_fixture_loaded_raw_keeps_the_creation_time_it_carries() -> None:
    fixture = serializers.serialize("json", [imported()])
    TrashRow.objects.all().delete()

    for loaded in serializers.deserialize("json", fixture):
        loaded.save()

    assert TrashRow.objects.get().created_at == SOURCE_CREATED


@pytest.mark.django_db
def test_historys_creation_snapshot_carries_the_stated_time_and_its_date_is_the_recording_moment() -> None:
    before = timezone.now()
    with system_context(reason="tests.timestamps.history"):
        row = OwnedRow(title="from a source").stamp_created_at(SOURCE_CREATED)
        row.save()

    created = row.history.model.objects.get(id=row.pk, history_type="+")
    assert created.created_at == SOURCE_CREATED
    assert created.history_date >= before


@pytest.mark.django_db
def test_an_expression_combines_the_creation_time_with_any_other_datetime_in_either_order() -> None:
    imported()

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
