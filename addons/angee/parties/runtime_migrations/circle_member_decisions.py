"""Retire roster provenance labels without reinterpreting any decision.

Confidence and confirmed/dismissed flags remain unchanged. The old source label
is discarded: a roster decision is not a PERSON provenance statement.
Reverse restores the schema default, not the discarded origin label.

The guard waits for the statement schema: the label leaves with the other
LinkSource carriers, after the folds the preflight has cleared, so a fold refusal
never leaves the pre-cutover release without the column it reads.
"""

from django.db import migrations


def applies(state):
    model = state.models.get(("parties", "circlemember"))
    if model is None or "source" not in model.fields:
        return False
    return ("parties", "partysameaspairstatement") in state.models


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RemoveField(model_name="circlemember", name="source")]
