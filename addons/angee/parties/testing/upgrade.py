"""Test support for replaying the sync-provenance upgrade: its pre-upgrade state, its rounds, its bodies.

A stack adopting the upgrade proves its own declarations against this chain without
copying it. :func:`pre_upgrade_state` is the state a database before the upgrade holds,
which the adopter extends with its own pre-upgrade shapes; :func:`upgrade_rounds` plans
the chain's rounds (:data:`UPGRADE_CHAIN` plus the adopter's own declarations) through
the composer's own loop (core ``angee.testing.migrations``); and
:class:`UpgradeHost` holds the pre-upgrade parties shapes on the host database so
declaration bodies run against seeded rows, each applied when its round comes.
Nothing here imports a host's test models, so a composed host can load it beside its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from django.utils import timezone

from angee.base.fields import StateField
from angee.integrate.runtime_migrations import record_link_history_state
from angee.testing.migrations import replay_rounds

CARD_COLUMNS = ("folder", "source_uid", "source_etag", "raw_vcard", "extensions")
"""The CardDAV enrollment columns the upgrade retires from parties and their history."""

UPGRADE_CHAIN = (
    "angee.provenance:changeset_schema",
    "angee.provenance:statement_principal_schema",
    "angee.provenance:retire_reversion",
    "angee.integrate:record_link_history_state",
    "angee.integrate:record_sync_state",
    "angee.integrate:fold_record_sync_history",
    "angee.integrate:retire_record_sync_stores",
    "angee.integrate:retire_record_sync_hash_bases",
    "angee.integrate:record_link_failure_status",
    "angee.integrate:sync_stream_authoritative",
    "angee.spaces:membership_decisions",
    "angee.parties:preflight_party_folds",
    "angee.parties:party_statement_schema",
    "angee.parties:party_aspect_schema",
    "angee.parties:fold_party_handle_statements",
    "angee.parties:fold_party_aspects",
    "angee.parties:circle_member_decisions",
    "angee.parties:retire_party_handle",
    "angee.parties:retire_party_carriers",
)
"""The declarations this upgrade introduced, in each addon's manifest order."""


def _card_fields(*, constrained: bool) -> dict[str, models.Field]:
    return {
        "folder": models.ForeignKey(
            "parties.Folder",
            null=True,
            blank=True,
            related_name="+",
            on_delete=models.SET_NULL if constrained else models.DO_NOTHING,
            db_constraint=constrained,
        ),
        "source_uid": models.CharField(max_length=512, blank=True, default=""),
        "source_etag": models.CharField(max_length=512, blank=True, default=""),
        "raw_vcard": models.TextField(blank=True, default=""),
        "extensions": models.JSONField(blank=True, default=dict),
    }


def _audit_fields() -> dict[str, models.Field]:
    return {
        "created_at": models.DateTimeField(default=timezone.now),
        "updated_at": models.DateTimeField(default=timezone.now),
        "created_by": models.ForeignKey(
            settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL
        ),
        "updated_by": models.ForeignKey(
            settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL
        ),
    }


def legacy_state() -> ProjectState:
    """The host's composed schema plus every retired parties shape the chain's bodies read."""
    state = ProjectState.from_apps(apps)
    party = state.models["parties", "party"]
    party.fields.update(_card_fields(constrained=True))
    party.options["constraints"] = [
        *party.options.get("constraints", []),
        models.UniqueConstraint(
            fields=("folder", "source_uid"),
            name="uq_party_folder_source_uid",
            condition=models.Q(folder__isnull=False) & ~models.Q(source_uid=""),
        ),
    ]
    for name in ("historicalparty", "historicalperson", "historicalorganization"):
        historical = state.models["parties", name]
        historical.fields.update(_card_fields(constrained=False))
        historical.fields["handle_count"] = models.PositiveIntegerField(default=0)
    relationship = state.models["parties", "relationship"]
    relationship.fields["source"] = models.CharField(max_length=16, default="manual")
    relationship.options["constraints"] = [
        *relationship.options.get("constraints", []),
        models.UniqueConstraint(
            fields=("party", "other_name"),
            name="uq_relationship_carddav_employment",
            condition=models.Q(source="carddav"),
        ),
    ]
    state.models["parties", "circlemember"].fields["source"] = models.CharField(max_length=16, default="manual")
    state.add_model(
        ModelState(
            "parties",
            "PartyHandle",
            [
                ("id", models.BigAutoField(primary_key=True)),
                *_audit_fields().items(),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
                ("handle", models.ForeignKey("parties.Handle", on_delete=models.CASCADE, related_name="+")),
                ("confidence", models.FloatField(default=1.0)),
                ("source", models.CharField(max_length=16, default="manual")),
                ("is_confirmed", models.BooleanField(default=False)),
                ("is_dismissed", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "MergeVeto",
            [
                ("id", models.BigAutoField(primary_key=True)),
                *_audit_fields().items(),
                ("party_a", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
                ("party_b", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
            ],
        )
    )
    return state


def pre_upgrade_state() -> ProjectState:
    """The tables the upgrade's guards read, as a database before the upgrade holds them.

    An adopting stack adds its own pre-upgrade shapes to this state before planning.
    """
    state = ProjectState()
    state.add_model(ModelState.from_model(ContentType))
    state.add_model(
        ModelState(
            "spaces",
            "Membership",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("source", models.CharField(max_length=16, default="manual")),
            ],
        )
    )
    state.add_model(
        ModelState(
            "iam", "User", [("id", models.BigAutoField(primary_key=True)), ("kind", models.CharField(max_length=8))]
        )
    )
    state.add_model(ModelState("integrate", "Integration", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "integrate",
            "SyncStream",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("integration", models.ForeignKey("integrate.Integration", on_delete=models.CASCADE)),
                ("key", models.CharField(max_length=64)),
                ("partition", models.CharField(max_length=512)),
                ("generation", models.PositiveIntegerField(default=1)),
            ],
        )
    )
    link_fields: dict[str, models.Field] = {
        "id": models.BigAutoField(primary_key=True),
        "created_at": models.DateTimeField(),
        "updated_at": models.DateTimeField(),
        "created_by": models.ForeignKey("iam.User", null=True, on_delete=models.SET_NULL, related_name="+"),
        "updated_by": models.ForeignKey("iam.User", null=True, on_delete=models.SET_NULL, related_name="+"),
        "stream": models.ForeignKey("integrate.SyncStream", on_delete=models.CASCADE),
        "parent": models.ForeignKey("integrate.RecordLink", null=True, on_delete=models.SET_NULL, related_name="+"),
        "external_key": models.CharField(max_length=512),
        "target_content_type": models.ForeignKey("contenttypes.ContentType", null=True, on_delete=models.PROTECT),
        "target_object_id": models.CharField(max_length=255, null=True),
        "status": StateField(
            choices=[
                ("current", "Current"),
                ("observed", "Observed"),
                ("unavailable", "Unavailable"),
                ("discrepant", "Discrepant"),
                ("withdrawn", "Withdrawn"),
                ("tombstone", "Tombstone"),
            ],
            default="observed",
            max_length=11,
            db_index=True,
        ),
        "remote_version": models.CharField(max_length=512, blank=True),
        "remote_base_hash": models.CharField(max_length=64, blank=True),
        "local_base_hash": models.CharField(max_length=64, blank=True),
        "origin": models.CharField(max_length=16, blank=True),
        "last_seen_at": models.DateTimeField(null=True),
        "last_verified_generation": models.PositiveIntegerField(default=0),
        "absence_count": models.PositiveIntegerField(default=0),
        "metadata": models.JSONField(default=dict),
        "tombstoned_at": models.DateTimeField(null=True),
    }
    if set(link_fields) != record_link_history_state.LEGACY_FIELDS:
        raise AssertionError("the pre-upgrade RecordLink differs from the shape its history declaration selects")
    state.add_model(ModelState("integrate", "RecordLink", list(link_fields.items())))
    state.add_model(
        ModelState(
            "integrate",
            "RecordRevision",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("link", models.ForeignKey("integrate.RecordLink", on_delete=models.CASCADE)),
                ("number", models.PositiveIntegerField()),
            ],
        )
    )
    state.add_model(ModelState("integrate", "SyncDiscrepancy", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(ModelState("parties", "Folder", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "parties",
            "Party",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("display_name", models.TextField()),
                ("handle_count", models.PositiveIntegerField(default=0)),
                *_card_fields(constrained=True).items(),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("folder", "source_uid"), name="uq_party_folder_source_uid")
                ]
            },
        )
    )
    state.add_model(ModelState("parties", "Handle", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "parties",
            "PartyHandle",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
                ("handle", models.ForeignKey("parties.Handle", on_delete=models.CASCADE)),
            ],
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "CircleMember",
            [("id", models.BigAutoField(primary_key=True)), ("source", models.CharField(max_length=16))],
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "Relationship",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
                ("other_name", models.TextField(blank=True)),
                ("source", models.CharField(max_length=16)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("party", "other_name"), name="uq_relationship_carddav_employment")
                ]
            },
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "Address",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
            ],
        )
    )
    state.add_model(ModelState("parties", "MergeVeto", [("id", models.BigAutoField(primary_key=True))]))
    return state


def upgrade_rounds(state: ProjectState | None = None, *, origins: Iterable[str] = ()) -> dict[ModuleType, int]:
    """The round each declaration of the chain lands in from ``state`` (default: :func:`pre_upgrade_state`).

    Replays :data:`UPGRADE_CHAIN` plus ``origins`` (an adopting addon's own declarations,
    ``"<addon>:<name>"``) through core test support's ``replay_rounds``, the composer's
    own round loop, keyed by each declaration's source module; one that never becomes
    applicable is absent. It models the chain's own ordering, not a stack that skips
    releases (see ``RuntimeMigrations.schedule``).

    From a migrated state the chain still reports ``retire_reversion``: reversion's
    tables are not in the project state, so its guard holds wherever reversion is not
    installed and materialization bounds its one run. An adopter proving its own
    declarations idle on its migrated schema replays only its own origins through
    ``replay_rounds``.
    """

    return replay_rounds(pre_upgrade_state() if state is None else state, (*UPGRADE_CHAIN, *origins))


class UpgradeHost:
    """The host database holding the pre-upgrade parties shapes, on which declaration bodies replay.

    It creates the retired tables, columns and constraints of :func:`legacy_state` on the
    host's composed tables and removes them again on :meth:`close`. An adopting stack
    passes that state extended with its own shapes and names what it added.
    """

    TABLES = ("parties.PartyHandle", "parties.MergeVeto")
    COLUMNS = (
        ("parties.Party", CARD_COLUMNS),
        ("parties.HistoricalParty", (*CARD_COLUMNS, "handle_count")),
        ("parties.HistoricalPerson", (*CARD_COLUMNS, "handle_count")),
        ("parties.HistoricalOrganization", (*CARD_COLUMNS, "handle_count")),
        ("parties.Relationship", ("source",)),
        ("parties.CircleMember", ("source",)),
    )
    CONSTRAINTS = (
        ("parties.Party", "uq_party_folder_source_uid"),
        ("parties.Relationship", "uq_relationship_carddav_employment"),
    )

    def __init__(
        self,
        state: ProjectState | None = None,
        *,
        tables: Iterable[str] = (),
        columns: Iterable[tuple[str, Iterable[str]]] = (),
        constraints: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.state = legacy_state() if state is None else state
        self.initial = self.state.clone()
        self.apps = self.state.apps
        self.tables = (*self.TABLES, *tables)
        self.constraints = (*self.CONSTRAINTS, *constraints)
        self.added: list[tuple[str, str]] = []
        with connection.schema_editor() as editor:
            for label in self.tables:
                editor.create_model(self.model(label))
            for label, names in (*self.COLUMNS, *columns):
                model = self.model(label)
                for name in names:
                    editor.add_field(model, model._meta.get_field(name))
                    self.added.append((model._meta.db_table, model._meta.get_field(name).column))
            for label, name in self.constraints:
                # SQLite's table remake already materialized the state's constraints.
                model = self.model(label)
                with connection.cursor() as cursor:
                    present = name in connection.introspection.get_constraints(cursor, model._meta.db_table)
                if not present:
                    editor.add_constraint(model, next(c for c in model._meta.constraints if c.name == name))

    def close(self) -> None:
        """Remove what the host added, whatever the replayed declarations left of it."""

        # Render the teardown models without the legacy constraints: SQLite remakes a
        # table from the model when a column leaves, and the constraint names that column.
        state = self.initial.clone()
        constraints = {}
        for label, name in self.constraints:
            app_label, model_name = label.split(".")
            options = state.models[app_label, model_name.lower()].options
            constraints[label] = next(c for c in options["constraints"] if c.name == name)
            options["constraints"] = [c for c in options["constraints"] if c.name != name]
        current = state.apps
        with connection.schema_editor() as editor:
            tables = set(connection.introspection.table_names())
            for label in reversed(self.tables):
                model = current.get_model(label)
                if model._meta.db_table in tables:
                    editor.delete_model(model)
            for label, constraint in constraints.items():
                model = current.get_model(label)
                with connection.cursor() as cursor:
                    present = constraint.name in connection.introspection.get_constraints(cursor, model._meta.db_table)
                if present:
                    editor.remove_constraint(model, constraint)
            for table, column in self.added:
                with connection.cursor() as cursor:
                    columns = {row.name for row in connection.introspection.get_table_description(cursor, table)}
                if column not in columns:
                    continue
                model = next(m for m in current.get_models() if m._meta.db_table == table)
                editor.remove_field(model, next(f for f in model._meta.fields if f.column == column))

    def model(self, label: str) -> Any:
        return self.apps.get_model(label)

    def rows(self, label: str) -> Any:
        return self.model(label)._base_manager.order_by()

    def create(self, model_label: str, /, **values: Any) -> Any:
        return self.rows(model_label).create(**values)

    def content_type(self, model: str, app_label: str = "parties") -> Any:
        row, _ = self.rows("contenttypes.ContentType").get_or_create(app_label=app_label, model=model)
        return row

    def apply(self, module: ModuleType, app_label: str | None = None) -> None:
        """Run one declaration's migration on the host, moving its state on as the composer would."""

        label = app_label or apps.get_containing_app_config(module.__name__).label
        migration = module.Migration(f"0001_{module.__name__.rsplit('.', 1)[1]}", label)
        with connection.schema_editor() as editor:
            self.state = migration.apply(self.state, editor)
        self.apps = self.state.apps
