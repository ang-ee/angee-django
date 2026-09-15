"""Bind a retained attachment Part to its exact storage contributor claim."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def _field_shape(field: models.Field) -> tuple[object, ...]:
    """Return the declaration-owned field shape without its bound name."""

    _name, path, args, kwargs = field.deconstruct()
    return path, tuple(args), tuple(sorted(kwargs.items()))


def applies(project_state: ProjectState) -> bool:
    """Apply only across the complete claim-provenance transition."""

    part = project_state.models.get(("messaging", "part"))
    if part is None:
        return False
    field = part.fields.get("source_claim")
    constraints = {
        getattr(value, "name", ""): value
        for value in part.options.get("constraints", ())
    }
    constraint = constraints.get("ck_part_source_claim_attachment")
    expected_field = Migration.operations[0].field
    expected_constraint = Migration.operations[1].constraint
    base_manager_name = part.options.get("base_manager_name")
    if field is None and constraint is None and base_manager_name != "objects":
        return True
    if (
        field is not None
        and _field_shape(field) == _field_shape(expected_field)
        and constraint is not None
        and constraint.deconstruct() == expected_constraint.deconstruct()
        and base_manager_name == "objects"
    ):
        return False
    raise ImproperlyConfigured(
        "angee.messaging:part_source_claim found a partial Part claim-provenance "
        "transition"
    )


class Migration(migrations.Migration):
    """Append exact claim identity without adopting existing attachment Parts."""

    dependencies = [("storage", "__latest__")]
    operations = [
        migrations.AddField(
            model_name="part",
            name="source_claim",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=models.PROTECT,
                related_name="message_part",
                to="storage.fileattachmentclaim",
            ),
        ),
        migrations.AddConstraint(
            model_name="part",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(source_claim__isnull=True)
                    | (
                        models.Q(parent__isnull=True)
                        & models.Q(file__isnull=False)
                        & models.Q(fragment__isnull=True)
                        & models.Q(disposition="attachment")
                    )
                ),
                name="ck_part_source_claim_attachment",
            ),
        ),
        migrations.AlterModelOptions(
            name="part",
            options={
                "base_manager_name": "objects",
                "ordering": ("message", "position", "sqid"),
            },
        ),
    ]
