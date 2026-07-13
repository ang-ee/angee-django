"""Materialize whether each handle's resolving party link is confirmed."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether Handle has the party pointer but not its confirmed state."""

    model = project_state.models.get(("parties", "handle"))
    if model is None:
        return False
    has_party = "party" in model.fields
    has_confirmation = "party_link_confirmed" in model.fields
    if has_party and not has_confirmation:
        return True
    if has_party and has_confirmation:
        return False
    if has_confirmation:
        raise ImproperlyConfigured(
            "angee.parties:handle_party_link_confirmed found confirmation state without Handle.party"
        )
    return False


def backfill_confirmed_winners(apps, schema_editor) -> None:
    """Mark handles whose materialized party comes from a confirmed surviving link."""

    handle_model = apps.get_model("parties", "Handle")
    link_model = apps.get_model("parties", "PartyHandle")
    database = schema_editor.connection.alias
    links = (
        link_model._base_manager.using(database)
        .filter(is_confirmed=True, is_dismissed=False)
        .order_by()
        .values_list("handle_id", "party_id")
    )
    for handle_id, party_id in links.iterator():
        handle_model._base_manager.using(database).filter(
            pk=handle_id,
            party_id=party_id,
        ).update(party_link_confirmed=True)


class Migration(migrations.Migration):
    """Add and backfill the confirmed state of Handle.party's winning link."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="handle",
            name="party_link_confirmed",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.RunPython(backfill_confirmed_winners, migrations.RunPython.noop),
    ]
