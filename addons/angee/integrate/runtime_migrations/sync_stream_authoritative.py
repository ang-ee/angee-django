"""Declare the per-stream ``authoritative`` knob: whether a system-of-record stream outranks people.

One boolean column, default False (system of reference); no row changes. Reverse
drops the column and loses recorded flips, which survivorship re-derives anyway.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    stream = state.models.get(("integrate", "syncstream"))
    return stream is not None and "authoritative" not in stream.fields


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField("syncstream", "authoritative", models.BooleanField(default=False)),
    ]
