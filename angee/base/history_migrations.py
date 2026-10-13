"""Durable data operations for native history tables in emitted migrations.

Only frozen declarations and Django's historical app registry are used here.
Keep this module importable after the live model and composer have changed.
"""

from __future__ import annotations

from itertools import batched

from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.operations.base import Operation, OperationCategory
from django.db.migrations.state import ProjectState
from django.utils import timezone


class HistoryBaseline(Operation):
    """Checkpoint existing rows when their native history table is introduced.

    ``fields`` freezes pairs of source/history storage attributes, including the
    copied source identity. ``null_fields`` freezes attribution columns that
    must not inherit the source record's actor or writing changeset. A baseline
    is an observation at upgrade time, never a claim that the record was created
    then. Existing captures take precedence, including deletion captures.

    Reversal deliberately retains every snapshot. Reapplying this operation is
    row-wise idempotent and cannot replace history written after the upgrade.
    """

    reduces_to_sql = False
    atomic = True
    category = OperationCategory.PYTHON

    def __init__(
        self,
        source_model: str,
        history_model: str,
        fields: tuple[tuple[str, str], ...],
        null_fields: tuple[str, ...] = (),
    ) -> None:
        self.source_model = source_model
        self.history_model = history_model
        self.fields = fields
        self.null_fields = null_fields

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        """The preceding native operations already own the complete schema."""

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        source = to_state.apps.get_model(self.source_model)
        history = to_state.apps.get_model(self.history_model)
        alias = schema_editor.connection.alias
        if not self.allow_migrate_model(alias, source) or not self.allow_migrate_model(alias, history):
            return

        source_key = source._meta.pk.attname
        history_key = next(target for origin, target in self.fields if origin == source_key)
        source_fields = tuple(dict.fromkeys(origin for origin, _target in self.fields))
        snapshots = history._base_manager.using(alias)
        observed_at = timezone.now()
        rows = source._base_manager.using(alias).order_by(source_key).select_for_update().values(*source_fields)
        for batch in batched(rows.iterator(chunk_size=500), 500):
            # Lock the stored rows before checking history: another writer or
            # retry must finish its capture before this checkpoint is considered.
            captured = set(snapshots.filter(**{
                f"{history_key}__in": [row[source_key] for row in batch],
            }).values_list(history_key, flat=True))
            pending = []
            for row in batch:
                if row[source_key] in captured:
                    continue
                values = {target: row[origin] for origin, target in self.fields}
                values.update(dict.fromkeys(self.null_fields))
                values.update(history_date=observed_at, history_type="~", history_change_reason=None)
                pending.append(history(**values))
            snapshots.bulk_create(pending, batch_size=500)

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        """Never erase captured evidence when reversing this checkpoint."""

    def describe(self) -> str:
        return f"Checkpoint existing {self.source_model} rows in {self.history_model}"

    @property
    def migration_name_fragment(self) -> str:
        return f"history_baseline_{self.source_model.split('.', 1)[1].lower()}"
