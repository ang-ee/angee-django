"""Declare inbound stream state and nullable mapped local-deletion evidence.

No stream history is introduced. A successful local-deletion write-back has a
null mapped projection. Reverse loses unlinked failures and requires non-null
mapped payloads before restoring the old column contract.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    stream = state.models.get(("integrate", "syncstream"))
    link = state.models.get(("integrate", "recordlink"))
    history = state.models.get(("integrate", "historicalrecordlink"))
    if stream is None or link is None or history is None:
        return False
    complete = "inbound" in stream.fields and link.fields["mapped_payload"].null and history.fields["mapped_payload"].null
    if complete:
        return False
    if "inbound" not in stream.fields and not link.fields["mapped_payload"].null and not history.fields["mapped_payload"].null:
        return True
    raise ValueError("Record sync has a partial inbound/nullable-projection transition.")


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField("recordlink", "mapped_payload", models.JSONField(default=dict, blank=True, null=True)),
        migrations.AlterField("historicalrecordlink", "mapped_payload", models.JSONField(default=dict, blank=True, null=True)),
        migrations.AddField("syncstream", "inbound", models.JSONField(default=list, blank=True)),
    ]
