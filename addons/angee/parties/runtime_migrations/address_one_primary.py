"""Normalize retained primaries before enforcing one primary address per party."""

from __future__ import annotations

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("parties", "address"))
    if model is None:
        return False
    return not any(
        getattr(constraint, "name", "") == "parties_address_one_primary_per_party"
        for constraint in model.options.get("constraints", ())
    )


def normalize_primaries(apps, schema_editor) -> None:
    address_model = apps.get_model("parties", "Address")
    manager = address_model._base_manager.using(schema_editor.connection.alias)
    party_ids = manager.filter(is_primary=True).order_by().values_list("party_id", flat=True).distinct()
    for party_id in party_ids.iterator(chunk_size=1_000):
        primaries = manager.filter(party_id=party_id, is_primary=True).order_by("pk")
        keep = primaries.values_list("pk", flat=True).first()
        if keep is not None:
            primaries.exclude(pk=keep).update(is_primary=False)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(normalize_primaries, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="address",
            constraint=models.UniqueConstraint(
                fields=("party",),
                condition=models.Q(is_primary=True),
                name="parties_address_one_primary_per_party",
            ),
        ),
    ]
