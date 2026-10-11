"""Retire roster provenance labels without reinterpreting any decision.

Confidence and confirmed/dismissed flags remain unchanged. The old source label
is discarded: a roster decision is not a PERSON provenance statement.
Reverse restores the schema default, not the discarded origin label.
"""

from django.db import migrations


def applies(state):
    model = state.models.get(("spaces", "membership"))
    return model is not None and "source" in model.fields


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RemoveField(model_name="membership", name="source")]
