"""Model display names come from their Django metadata."""

from __future__ import annotations

from django.apps import apps

from angee.compose.model_composition import ModelComposition
from angee.money.models import Currency
from angee.parties.models import Party
from angee.uom.models import Uom, UomCategory


def test_record_picker_model_names() -> None:
    """Irregular nouns and abbreviations have the names shown in record pickers."""

    assert (str(Currency._meta.verbose_name), str(Currency._meta.verbose_name_plural)) == (
        "currency",
        "currencies",
    )
    assert (str(Party._meta.verbose_name), str(Party._meta.verbose_name_plural)) == (
        "party",
        "parties",
    )
    assert (str(Uom._meta.verbose_name), str(Uom._meta.verbose_name_plural)) == (
        "unit of measure",
        "units of measure",
    )
    assert (str(UomCategory._meta.verbose_name), str(UomCategory._meta.verbose_name_plural)) == (
        "unit of measure category",
        "unit of measure categories",
    )


def test_framework_runtime_models_do_not_use_naive_irregular_plurals() -> None:
    """Every composed framework model with an irregular ending declares a real plural."""

    composition = ModelComposition.discover(apps.get_app_configs())
    for candidate in composition.ordered_models:
        if not candidate.__module__.startswith("angee."):
            continue
        singular = str(candidate._meta.verbose_name)
        if singular.endswith(("y", "s", "x", "ch", "sh")):
            assert str(candidate._meta.verbose_name_plural) != f"{singular}s", (
                f"{candidate._meta.label} uses Django's naive plural"
            )
