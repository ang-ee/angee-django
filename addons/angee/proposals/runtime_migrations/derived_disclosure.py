"""Retain opening disclosure while moving its evidence to receipt columns."""

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    proposal = state.models.get(("proposals", "proposal"))
    if proposal is None:
        return False
    present = {name for name in ("disclosed_at", "track_published_at") if name in proposal.fields}
    if len(present) == 1:
        raise ValueError("Complete or reverse the partial proposal disclosure transition.")
    return not present


def forwards(apps, schema_editor):
    proposals = apps.get_model("proposals", "Proposal")._base_manager.using(
        schema_editor.connection.alias,
    ).order_by()
    eligible = proposals.filter(
        round__opened_at__isnull=False,
        round__opening_policy__in=("answers", "answers_and_tracks"),
        disclosed_at__isnull=True,
    ).filter(
        models.Q(state__in=("submitted", "accepted", "partially_accepted", "declined"))
        | models.Q(state="withdrawn", decided_at__gte=models.F("round__opened_at")),
    ).values_list("pk", "round__opened_at")
    for pk, opened_at in eligible.iterator(chunk_size=1000):
        proposals.filter(pk=pk, disclosed_at__isnull=True).update(disclosed_at=opened_at)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            "proposal", name, models.DateTimeField(null=True, blank=True, editable=False),
        )
        for name in ("disclosed_at", "track_published_at")
    ] + [migrations.RunPython(forwards, migrations.RunPython.noop)]
