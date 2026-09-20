"""Validate retained extraction engine keys against the active registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply after the current extraction model exists."""

    return ("workflows_extraction", "extraction") in project_state.models


def validate_engine_keys(apps: Any, schema_editor: Any) -> None:
    """Refuse retained rows whose implementation is absent from current settings."""

    extraction = apps.get_model("workflows_extraction", "Extraction")
    registry = getattr(settings, "ANGEE_EXTRACTION_ENGINE_CLASSES", {})
    registered = set(registry) if isinstance(registry, Mapping) else set()
    engine_column = extraction._meta.get_field("engine").column
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT {quote(engine_column)} FROM {quote(extraction._meta.db_table)}"
        )
        retained = {str(row[0]) for row in cursor.fetchall()}
    unavailable = retained - registered
    if unavailable:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:validate_extraction_engines found retained engine keys "
            f"without current implementations: {sorted(unavailable)!r}"
        )


class Migration(migrations.Migration):
    """Gate both already-adopted and future-adopted retained evidence."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(validate_engine_keys, migrations.RunPython.noop),
    ]
