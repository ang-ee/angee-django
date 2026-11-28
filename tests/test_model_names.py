"""Model display names come from their Django metadata."""

from __future__ import annotations

from angee.money.models import Currency
from angee.parties.models import Party


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
