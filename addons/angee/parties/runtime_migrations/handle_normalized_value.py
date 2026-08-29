"""Backfill the persisted comparison value for retained Handle rows."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

_BATCH_SIZE = 1_000


def applies(project_state: ProjectState) -> bool:
    """Return whether Handle has its source fields but not normalized_value."""

    model = project_state.models.get(("parties", "handle"))
    if model is None:
        return False
    fields = frozenset(model.fields)
    sources = frozenset({"platform", "value"})
    if sources <= fields:
        return "normalized_value" not in fields
    raise ImproperlyConfigured(
        "angee.parties:handle_normalized_value found Handle without its normalization sources: "
        f"{sorted(fields & sources)}"
    )


def _normalize_value(platform: str, value: str) -> str:
    """Mirror Handle.normalize_value without importing the live model."""

    normalized = (value or "").strip().lower()
    if platform == "email" and "@" in normalized:
        local, _, domain = normalized.rpartition("@")
        if domain in ("gmail.com", "googlemail.com"):
            local = local.split("+", 1)[0].replace(".", "")
        return f"{local}@{domain}"
    return normalized


def backfill_normalized_values(apps, schema_editor) -> None:
    """Populate retained handles in bounded batches before enforcing NOT NULL."""

    handle_model = apps.get_model("parties", "Handle")
    database = schema_editor.connection.alias
    manager = handle_model._base_manager.using(database)
    batch = []
    handles = manager.order_by("pk").only("pk", "platform", "value", "normalized_value")
    for handle in handles.iterator(chunk_size=_BATCH_SIZE):
        handle.normalized_value = _normalize_value(handle.platform, handle.value)
        batch.append(handle)
        if len(batch) == _BATCH_SIZE:
            manager.bulk_update(batch, ["normalized_value"], batch_size=_BATCH_SIZE)
            batch.clear()
    if batch:
        manager.bulk_update(batch, ["normalized_value"], batch_size=_BATCH_SIZE)


class Migration(migrations.Migration):
    """Add, backfill, index, and require Handle.normalized_value."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="handle",
            name="normalized_value",
            field=models.CharField(
                max_length=512,
                null=True,
                editable=False,
            ),
        ),
        migrations.RunPython(backfill_normalized_values, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="handle",
            name="normalized_value",
            field=models.CharField(
                max_length=512,
                db_index=True,
                editable=False,
            ),
        ),
    ]
