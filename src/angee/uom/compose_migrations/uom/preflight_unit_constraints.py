"""Migration template: refuse ambiguous legacy unit data before adding quantity constraints.

Copy this module into the host's ``runtime/uom/migrations/`` under the host's next
migration name, then replace the placeholder dependency below with the host's own
explicit migration (never ``__latest__``), and make the host migration that adds the
``uom.Uom`` ratio and rounding constraints depend on it. A host whose ``uom.Uom``
already carries ``uom_uom_rounding_power_of_ten`` skips this template.

No conversion semantics can be inferred from a nonpositive ratio or a rounding
increment such as 0.5. An operator must choose the intended unit definition. The
preflight changes nothing, so its reverse has nothing to undo.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def preflight(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Refuse the migration, listing up to 20 units, while a ratio or rounding step is ambiguous."""

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
    # Placeholder: the host's latest migration of ``uom.Uom`` before its ratio and rounding constraints.
    dependencies = [("uom", "HOST_MIGRATION_BEFORE_THE_UNIT_CONSTRAINTS")]
    operations = [migrations.RunPython(preflight, migrations.RunPython.noop)]
