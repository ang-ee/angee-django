"""Rewrite integration lifecycle rows to the connection-focused vocabulary."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

LEGACY_VALUES = ("draft", "active", "paused", "disabled")
CURRENT_CHOICES = (
    ("disconnected", "Disconnected"),
    ("connected", "Connected"),
    ("paused", "Paused"),
)
CURRENT_VALUES = tuple(value for value, _label in CURRENT_CHOICES)

LEGACY_MARKERS = frozenset(LEGACY_VALUES) - frozenset(CURRENT_VALUES)
"""Values only the pre-split vocabulary had — their presence is what this applies to."""

CURRENT_MARKERS = frozenset(CURRENT_VALUES) - frozenset(LEGACY_VALUES)
"""Values only the connection-focused vocabulary has — their presence means done."""


def applies(project_state: ProjectState) -> bool:
    """Return whether Integration still carries the legacy lifecycle vocabulary.

    Keyed on the marker values each vocabulary owns exclusively, never on the
    exact choice tuple: a project that adds a *fourth* lifecycle value later
    still has :data:`CURRENT_MARKERS` and no :data:`LEGACY_MARKERS`, so it reads
    as already-migrated instead of failing every ``angee build`` forever. Only a
    genuinely mixed vocabulary — both marker sets, or neither — is unresolvable.
    """

    model = project_state.models.get(("integrate", "integration"))
    if model is None:
        return False
    field = model.fields.get("lifecycle")
    if field is None:
        raise ImproperlyConfigured("angee.integrate:integration_lifecycle_values found Integration without lifecycle")
    values = frozenset(value for value, _label in field.choices)
    legacy = values & LEGACY_MARKERS
    current = values & CURRENT_MARKERS
    if legacy and not current:
        return True
    if current and not legacy:
        return False
    raise ImproperlyConfigured(
        "angee.integrate:integration_lifecycle_values found a partial Integration lifecycle "
        f"transition: {sorted(values)}"
    )


FORWARD_VALUES = (
    ("draft", "disconnected"),
    ("active", "connected"),
    ("disabled", "disconnected"),
)
"""Legacy value → current value. Not injective: ``disconnected`` has two sources."""

BACKWARD_VALUES = (
    ("disconnected", "draft"),
    ("connected", "active"),
)
"""Current value → legacy value — the lossy inverse of :data:`FORWARD_VALUES`.

``disconnected`` collapses to ``draft`` because the ``disabled`` it may also have
come from is unrecoverable; ``paused`` is unchanged in both vocabularies. Lossy,
but every value it writes fits the ``max_length=8`` column the paired
``AlterField`` restores — which is what makes a backwards ``migrate`` work.
"""


def _rewrite_values(apps, schema_editor, mapping) -> None:
    """Rewrite the lifecycle column through ``mapping`` without loading current models.

    Raw SQL, deliberately — not the ``_base_manager`` shape the migration
    pitfalls prescribe (and that the ``parties`` runtime migrations use). Both
    directions run while the recorded ``StateField`` choices disagree with the
    values in the column, so ``to_python`` rejects every row: any ORM read raises
    before a write could fix it. The column is addressed through the historical
    model's own table/column names, so it stays self-contained history either way.
    """

    integration_model = apps.get_model("integrate", "Integration")
    table = schema_editor.quote_name(integration_model._meta.db_table)
    column = schema_editor.quote_name(integration_model._meta.get_field("lifecycle").column)
    for old, new in mapping:
        schema_editor.execute(
            f"UPDATE {table} SET {column} = %s WHERE {column} = %s",
            (new, old),
        )


def rewrite_lifecycle_values(apps, schema_editor) -> None:
    """Map every legacy lifecycle value onto the connection-focused vocabulary."""

    _rewrite_values(apps, schema_editor, FORWARD_VALUES)


def restore_lifecycle_values(apps, schema_editor) -> None:
    """Map every current lifecycle value back onto the legacy vocabulary, lossily."""

    _rewrite_values(apps, schema_editor, BACKWARD_VALUES)


class Migration(migrations.Migration):
    """Widen the lifecycle column, record its new choices, then rewrite data.

    Reversible, lossily: operations reverse last-first, so
    :func:`restore_lifecycle_values` returns the column to legacy values *before*
    the ``AlterField`` narrows it back to ``max_length=8``. A ``noop`` reverse
    would leave ``disconnected`` (12 chars) in a column being shrunk to 8 —
    Postgres fails the reverse with "value too long", SQLite accepts it and every
    row then fails the legacy ``StateField``'s ``to_python``. The loss is real
    (:data:`BACKWARD_VALUES`) and preferred to either outcome.
    """

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField(
            model_name="integration",
            name="lifecycle",
            field=StateField(
                choices=CURRENT_CHOICES,
                default="disconnected",
                max_length=12,
            ),
        ),
        migrations.RunPython(rewrite_lifecycle_values, restore_lifecycle_values),
    ]
