"""Replace the legacy per-party Tie only when it holds no human cadence facts."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether the exact legacy per-party Tie shape is present."""

    model = project_state.models.get(("nexus", "tie"))
    if model is None:
        return False
    fields = frozenset(model.fields)
    old = frozenset(
        {
            "party",
            "outbound_count",
            "inbound_count",
            "cadence_days",
            "touch_due_at",
        }
    )
    new = frozenset({"party_a", "party_b", "a_to_b_count", "b_to_a_count"})
    if old <= fields and not fields & new:
        return True
    if new <= fields and not fields & old:
        return False
    raise ImproperlyConfigured(
        "angee.nexus:tie_pair_reshape found a partial Tie field transition: "
        f"{sorted(fields & (old | new))}"
    )


def assert_no_legacy_cadence_values(apps, schema_editor) -> None:
    """Refuse the derived-row rebuild when the old Tie stores human intent."""

    tie_model = apps.get_model("nexus", "Tie")
    database = schema_editor.connection.alias
    if tie_model._base_manager.using(database).filter(cadence_days__isnull=False).exists():
        raise RuntimeError(
            "angee.nexus cannot rebuild the legacy Tie table while legacy cadence values exist; "
            "migrate them to per-user Cadence rows first"
        )


class Migration(migrations.Migration):
    """Discard only recomputable legacy Tie rows before Django emits the pair graph."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(
            assert_no_legacy_cadence_values,
            migrations.RunPython.noop,
        ),
        migrations.DeleteModel(name="Tie"),
    ]
