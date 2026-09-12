"""Canonicalize retained address countries before narrowing them to alpha-2."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from django_countries import countries

from angee.parties.fields import CountryCodeField


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("parties", "address"))
    if model is None:
        return False
    field = model.fields.get("country")
    if isinstance(field, CountryCodeField):
        return False
    if type(field) is models.TextField and field.blank and field.default == "":
        return True
    raise ImproperlyConfigured(
        "angee.parties:address_country_code found an unsupported Address.country field state."
    )


def _normalize_country_code(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    code = countries.alpha2(candidate) or countries.by_name(candidate)
    return code if isinstance(code, str) else ""


def canonicalize_address_countries(apps, schema_editor) -> None:
    address_model = apps.get_model("parties", "Address")
    manager = address_model._base_manager.using(schema_editor.connection.alias)
    replacements: dict[str, str] = {}
    invalid: list[str] = []
    values = manager.order_by().values_list("country", flat=True).distinct()
    for value in values.iterator(chunk_size=1_000):
        code = _normalize_country_code(value)
        if str(value or "").strip() and not code:
            invalid.append(str(value))
        else:
            replacements[str(value or "")] = code
    if invalid:
        examples = ", ".join(repr(value) for value in sorted(invalid)[:5])
        raise RuntimeError(
            "Cannot migrate Address.country to ISO alpha-2; correct unsupported values first: "
            f"{examples}"
        )
    for value, code in replacements.items():
        if value != code:
            manager.filter(country=value).update(country=code)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(canonicalize_address_countries, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="address",
            name="country",
            field=CountryCodeField(blank=True, default=""),
        ),
    ]
