"""Refuse ambiguous legacy unit data before adding quantity constraints.

No conversion semantics can be inferred from a nonpositive ratio or a rounding
increment such as 0.5. An operator must choose the intended unit definition.
"""

from decimal import Decimal

from django.db import migrations, models


def applies(project_state):
    unit = project_state.models.get(("uom", "uom"))
    return unit is not None and not any(
        constraint.name == "uom_uom_rounding_power_of_ten"
        for constraint in unit.options.get("constraints", ())
    )


def preflight(apps, schema_editor):
    units = apps.get_model("uom", "Uom")._base_manager.using(schema_editor.connection.alias)
    steps = tuple(Decimal(10) ** exponent for exponent in range(-6, 6))
    invalid = units.exclude(models.Q(ratio__gt=0) & models.Q(rounding__in=steps))
    sample = list(invalid.order_by("pk").values_list("pk", "name", "ratio", "rounding")[:20])
    if sample:
        raise RuntimeError(
            "Unit constraint preflight failed. Reconcile the listed units' positive ratios "
            "and power-of-ten rounding steps with their business owner, then retry migrate. "
            "No unit data was changed. (pk, name, ratio, rounding): " + repr(sample)
        )


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(preflight, migrations.RunPython.noop)]
