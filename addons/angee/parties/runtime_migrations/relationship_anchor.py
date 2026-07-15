"""Preserve Relationship counterparties while moving to anchor vocabulary."""

from __future__ import annotations

import django.db.models.deletion
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether the exact pre-anchor Relationship fields are present."""

    model = project_state.models.get(("parties", "relationship"))
    if model is None:
        return False
    fields = frozenset(model.fields)
    old = frozenset({"from_party", "to_party"})
    new = frozenset({"party", "other_party", "other_name"})
    if old <= fields and not fields & new:
        return True
    if new <= fields and not fields & old:
        return False
    raise ImproperlyConfigured(
        "angee.parties:relationship_anchor found a partial Relationship field transition: "
        f"{sorted(fields & (old | new))}"
    )


class Migration(migrations.Migration):
    """Rename both foreign keys in place and add the free-text fallback."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint(
            model_name="relationship",
            name="uq_relationship_edge",
        ),
        migrations.RemoveConstraint(
            model_name="relationship",
            name="ck_relationship_distinct_parties",
        ),
        migrations.RenameField(
            model_name="relationship",
            old_name="from_party",
            new_name="party",
        ),
        migrations.RenameField(
            model_name="relationship",
            old_name="to_party",
            new_name="other_party",
        ),
        migrations.AlterField(
            model_name="relationship",
            name="other_party",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inbound_relationships",
                to="parties.party",
            ),
        ),
        migrations.AddField(
            model_name="relationship",
            name="other_name",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AlterModelOptions(
            name="relationship",
            options={"ordering": ("party", "sqid")},
        ),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.UniqueConstraint(
                condition=models.Q(other_party__isnull=False),
                fields=("party", "other_party", "kind"),
                name="uq_relationship_edge",
            ),
        ),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(other_party__isnull=True)
                    | ~models.Q(party=models.F("other_party"))
                ),
                name="ck_relationship_distinct_parties",
            ),
        ),
        migrations.AddConstraint(
            model_name="relationship",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(other_party__isnull=False)
                    | ~models.Q(other_name="")
                ),
                name="ck_relationship_has_other",
            ),
        ),
    ]
