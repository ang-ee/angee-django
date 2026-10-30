"""Resources-owned retirement policy composes native migration guards."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import connection, connections, migrations, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ModelState, ProjectState
from django.test import override_settings
from django.utils import timezone
from django_compose.migrations import DropGuardAutodetector

from angee.base.fields.identity import SqidField
from angee.resources.entries import resource_manifest_for, retired_models_for


@contextmanager
def _default_database(name: str, wrapper: type[BaseDatabaseWrapper] = DatabaseWrapper) -> Iterator[None]:
    """Swap the default alias to a separate SQLite (or dummy) database."""

    original = connections["default"]
    database = wrapper(
        {
            **original.settings_dict,
            "ENGINE": wrapper.__module__.rpartition(".")[0],
            "NAME": name,
            "OPTIONS": {},
            "TIME_ZONE": None,
        },
        alias="default",
    )
    connections["default"] = database
    try:
        yield
    finally:
        database.close()
        connections["default"] = original



@pytest.fixture
def isolated_runtime_database():
    """Exercise drop guards without colliding with the source test apps."""

    with _default_database(":memory:"):
        yield



def seed_ledger(model_label: str, public_id: str, *, addon: str = "probe", xref: str = "seeded") -> None:
    """Record a resource-ledger row naming ``model_label``, as its addon's resource files once seeded it.

    The ledger's label field refuses models no longer in code, so the row is
    written raw, as it survives in a database whose model was since removed.
    """

    ledger = apps.get_model("resources", "Resource")
    audit = apps.get_model("rebac", "PermissionAuditEvent")
    existing = connection.introspection.table_names()
    with connection.schema_editor() as editor:
        for model in (ledger, audit):
            if model._meta.db_table not in existing:
                editor.create_model(model)
    values = {
        "source_addon": addon, "source_path": f"resources/master/010_{xref}.yaml", "tier": "master",
        "xref": xref, "content_hash": "sha256:" + "0" * 64, "target_model": model_label,
        "target_id": public_id, "loaded_at": timezone.now(),
    }
    fields = [field for field in ledger._meta.local_concrete_fields if not field.primary_key]
    row = [
        values[field.attname] if field.attname in values
        else timezone.now() if isinstance(field, models.DateTimeField) and not field.null
        else field.get_default() if field.has_default() else None
        for field in fields
    ]
    columns = ", ".join(connection.ops.quote_name(field.column) for field in fields)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {connection.ops.quote_name(ledger._meta.db_table)} ({columns}) "
            f"VALUES ({', '.join(['%s'] * len(fields))})",
            [field.get_db_prep_value(value, connection) if field.attname not in ("target_model",) else value
             for field, value in zip(fields, row, strict=True)],
        )



@pytest.mark.parametrize("public_ids", ["sqid", "primary_key"])
@pytest.mark.django_db
@pytest.mark.usefixtures("isolated_runtime_database")
def test_autodetected_table_drop_lets_rows_its_retiring_addon_seeded_go(monkeypatch, public_ids: str) -> None:
    """Only the addon that names the model vouches for its own seeded rows; any other row refuses the drop."""

    history, current = ProjectState(), ProjectState()
    for state in (history, current):
        state.add_model(ModelState("probe", "Kept", [("id", models.AutoField(primary_key=True))]))
    history.add_model(ModelState("probe", "Retired", [("id", models.AutoField(primary_key=True))]))
    graph = MigrationGraph()
    graph.add_node(("probe", "0001_initial"), migrations.Migration("0001_initial", "probe"))
    retired = history.apps.get_model("probe", "Retired")
    with connection.schema_editor() as editor:
        editor.create_model(retired)
    codec = SqidField(real_field_name="id", min_length=8, prefix="ret")

    def public_id(row):
        return codec.public_id_from_value(row.pk) if public_ids == "sqid" else str(row.pk)

    owner = apps.get_app_config("resources")
    seeded = retired._base_manager.create()
    seed_ledger("probe.Retired", public_id(seeded), addon=owner.name)
    refused = r"probe\.retired: probe_retired\.\* holds data in 1 rows"
    with pytest.raises(CommandError, match=refused):  # No addon vouches for the row yet.
        DropGuardAutodetector(history, current).changes(graph=graph)

    monkeypatch.setattr("angee.resources.entries.addon_facts",
        lambda name, capability: {"retired_models": ["probe.Retired"]})
    (migration,) = DropGuardAutodetector(history, current).changes(graph=graph)["probe"]
    assert [type(operation).__name__ for operation in migration.operations] == ["DeleteModel"]

    # The compiler by itself grants no resource exemption. Removing the owner
    # leaves even this vouched-for row protected by the ordinary populated guard.
    with override_settings(COMPOSE_INTEGRATIONS=[]):
        with pytest.raises(CommandError, match=refused):
            DropGuardAutodetector(history, current).changes(graph=graph)

    # Another addon's seeded row, a ledger id that is not the row's canonical public
    # id, and an unledgered row each still count as data.
    for addon, ledgered in (("probe.other", True), (owner.name, False), (None, None)):
        row = retired._base_manager.create()
        if addon is not None:
            stale = SqidField(real_field_name="id", min_length=0, prefix="ret").public_id_from_value(row.pk)
            seed_ledger("probe.Retired", public_id(row) if ledgered else stale, addon=addon, xref=f"row{row.pk}")
        with pytest.raises(CommandError, match=refused):
            DropGuardAutodetector(history, current).changes(graph=graph)
        retired._base_manager.filter(pk=row.pk).delete()



def test_retired_models_are_a_resources_declaration_not_a_tier(monkeypatch) -> None:
    messaging = apps.get_app_config("messaging")
    assert retired_models_for(messaging) == ("messaging.ActivityType",)
    assert "retired_models" not in resource_manifest_for(messaging)
    monkeypatch.setattr("angee.resources.entries.addon_facts",
        lambda name, capability: {"retired_models": "messaging.ActivityType"})
    with pytest.raises(ImproperlyConfigured, match="retired_models must be an array of model labels"):
        retired_models_for(messaging)
