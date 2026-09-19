"""Record the closed extraction status and carrier-kind vocabularies."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

STATUS_CHOICES = (("succeeded", "Succeeded"), ("failed", "Failed"))
PART_KIND_CHOICES = (
    ("structured", "Structured"),
    ("native_text", "Native text"),
    ("recognized_text", "Recognized text"),
)


def applies(project_state: ProjectState) -> bool:
    """Apply only to the previous untyped extraction fields."""

    extraction = project_state.models.get(("workflows_extraction", "extraction"))
    part = project_state.models.get(("workflows_extraction", "extractionpart"))
    if extraction is None and part is None:
        return False
    if extraction is None or part is None:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:extraction_state_enums found a partial model graph"
        )
    status = extraction.fields.get("status")
    kind = part.fields.get("kind")
    if status is None or kind is None:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:extraction_state_enums found missing state fields"
        )
    status_values = tuple(value for value, _label in (status.choices or ()))
    kind_values = tuple(value for value, _label in (kind.choices or ()))
    current = (
        isinstance(status, StateField)
        and isinstance(kind, StateField)
        and status.max_length == 9
        and kind.max_length == 15
        and status_values == tuple(value for value, _label in STATUS_CHOICES)
        and kind_values == tuple(value for value, _label in PART_KIND_CHOICES)
        and not status.editable
        and not kind.editable
    )
    legacy = (
        type(status) is models.CharField
        and type(kind) is models.CharField
        and status.max_length == 16
        and kind.max_length == 32
        and not status_values
        and not kind_values
        and not status.editable
        and not kind.editable
    )
    if current:
        return False
    if legacy:
        return True
    raise ImproperlyConfigured(
        "angee.workflows_extraction:extraction_state_enums found a partial vocabulary transition"
    )


def validate_stored_values(apps, schema_editor) -> None:
    """Refuse enum projection when retained rows contain undeclared values."""

    extraction = apps.get_model("workflows_extraction", "Extraction")
    part = apps.get_model("workflows_extraction", "ExtractionPart")
    alias = schema_editor.connection.alias
    status_values = set(
        extraction._base_manager.using(alias).order_by().values_list("status", flat=True).distinct()
    )
    kind_values = set(
        part._base_manager.using(alias).order_by().values_list("kind", flat=True).distinct()
    )
    invalid_statuses = status_values - {value for value, _label in STATUS_CHOICES}
    invalid_kinds = kind_values - {value for value, _label in PART_KIND_CHOICES}
    if invalid_statuses or invalid_kinds:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:extraction_state_enums found undeclared retained values: "
            f"status={sorted(invalid_statuses)}, kind={sorted(invalid_kinds)}"
        )


class Migration(migrations.Migration):
    """Validate retained tokens before recording their native enum fields."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(validate_stored_values, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="extraction",
            name="status",
            field=StateField(choices=STATUS_CHOICES, editable=False),
        ),
        migrations.AlterField(
            model_name="extractionpart",
            name="kind",
            field=StateField(choices=PART_KIND_CHOICES, editable=False),
        ),
    ]
