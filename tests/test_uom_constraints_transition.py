"""Legacy quantities fail before constraint installation without inferred repair."""

from decimal import Decimal
from importlib import import_module

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

transition = import_module("angee.uom.compose_migrations.uom.preflight_unit_constraints")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ratio,rounding,invalid", [("1", "0.5", True), ("0", "0.01", True), ("2", "0.01", False)])
def test_preflight_legacy_units_preserves_values(ratio, rounding, invalid):
    state = ProjectState()
    state.add_model(ModelState("uom", "Uom", [
        ("id", models.AutoField(primary_key=True)),
        ("name", models.CharField(max_length=128)),
        ("ratio", models.DecimalField(max_digits=20, decimal_places=10)),
        ("rounding", models.DecimalField(max_digits=12, decimal_places=6)),
    ], {"db_table": "test_legacy_unit_preflight"}))
    assert transition.applies(state)
    unit = state.apps.get_model("uom", "Uom")
    with connection.schema_editor() as editor:
        editor.create_model(unit)
    try:
        row = unit.objects.create(name="Legacy half", ratio=Decimal(ratio), rounding=Decimal(rounding))
        with connection.schema_editor() as editor:
            if invalid:
                with pytest.raises(RuntimeError, match="Legacy half"):
                    transition.preflight(state.apps, editor)
            else:
                transition.preflight(state.apps, editor)
        row.refresh_from_db()
        assert row.ratio == Decimal(ratio)
        assert row.rounding == Decimal(rounding)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(unit)
