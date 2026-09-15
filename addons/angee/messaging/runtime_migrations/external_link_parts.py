"""Let historical message Parts render claimed external-link assets."""

from __future__ import annotations

import django.db.models.deletion
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply only to the complete file-only claimed-Part state."""

    part = project_state.models.get(("messaging", "part"))
    if part is None:
        return False
    external_field = part.fields.get("external_link")
    constraints = {
        value.name: value for value in part.options.get("constraints", ())
    }
    claim_constraint = constraints.get("ck_part_source_claim_attachment")
    exclusivity_constraint = constraints.get("ck_part_at_most_one_content")
    if (
        external_field is not None
        and claim_constraint is not None
        and exclusivity_constraint is not None
    ):
        return False
    if (
        external_field is None
        and claim_constraint is not None
        and exclusivity_constraint is None
    ):
        return True
    raise ImproperlyConfigured(
        "angee.messaging:external_link_parts found a partial Part transition"
    )


class Migration(migrations.Migration):
    """Add one exclusive link arm beside the existing claimed File arm."""

    dependencies = [("storage", "__latest__")]
    operations = [
        migrations.AddField(
            model_name="part",
            name="external_link",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="storage.externallink",
            ),
        ),
        migrations.AddConstraint(
            model_name="part",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(fragment__isnull=True, file__isnull=True)
                    | models.Q(fragment__isnull=True, external_link__isnull=True)
                    | models.Q(file__isnull=True, external_link__isnull=True)
                ),
                name="ck_part_at_most_one_content",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="part",
            name="ck_part_source_claim_attachment",
        ),
        migrations.AddConstraint(
            model_name="part",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(source_claim__isnull=True)
                    | (
                        models.Q(parent__isnull=True)
                        & (
                            models.Q(file__isnull=False, external_link__isnull=True)
                            | models.Q(file__isnull=True, external_link__isnull=False)
                        )
                        & models.Q(fragment__isnull=True)
                        & models.Q(disposition="attachment")
                    )
                ),
                name="ck_part_source_claim_attachment",
            ),
        ),
    ]
