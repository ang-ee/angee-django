"""Add protected changeset grouping to knowledge history without attributing old rows.

Legacy changeset attribution stays NULL. Reverse drops only the foreign keys,
retaining every history row and snapshot; it loses changeset grouping on rows
written after forward.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

HISTORICAL_MODELS = ("historicalmarkdownpage", "historicalpage", "historicalvault")


def applies(state: ProjectState) -> bool:
    histories = [state.models.get(("knowledge", name)) for name in HISTORICAL_MODELS]
    if all(model is None for model in histories):
        return False
    required = {"history_id", "history_date", "history_type", "history_user", "history_change_reason"}
    if histories[0] is None and all(
        model is not None and required.issubset(model.fields) and "changeset" not in model.fields
        for model in histories[1:]
    ):
        # The earlier markdown_page_history declaration must run first; it is
        # pending in this build whatever shape MarkdownPage has reached.
        return False
    if any(model is None or not required.issubset(model.fields) for model in histories):
        raise ValueError("Complete or reverse the partial knowledge history schema before adding changesets.")
    fields = [model.fields.get("changeset") for model in histories if model is not None]
    if all(field is None for field in fields):
        return True
    expected = Migration.operations[0].field.deconstruct()[1:]
    if any(field is None or field.deconstruct()[1:] != expected for field in fields):
        raise ValueError("Complete or reverse the partial knowledge history changeset transition before upgrading.")
    return False


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [
        migrations.AddField(
            model_name=name,
            name="changeset",
            field=models.ForeignKey(
                settings.ANGEE_CHANGESET_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
            ),
        )
        for name in HISTORICAL_MODELS
    ]
