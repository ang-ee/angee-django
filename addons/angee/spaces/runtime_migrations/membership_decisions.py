"""Retire roster provenance labels without reinterpreting any decision.

Confidence and confirmed/dismissed flags remain unchanged. The old source label
is discarded: a roster decision is not a PERSON provenance statement.
Reverse restores the schema default, not the discarded origin label.

Ordering (guard rounds, no composer primitive): on a stack with parties installed
this drop waits for the parties statement schema, which the parties fold preflight
precedes, so no label is discarded before the preflight has accepted the upgrade.
"""

from django.db import migrations


def applies(state):
    model = state.models.get(("spaces", "membership"))
    if model is None or "source" not in model.fields:
        return False
    if ("parties", "partyhandle") in state.models and ("parties", "partystatement") not in state.models:
        return False  # the parties fold preflight has not run yet
    return True


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RemoveField(model_name="membership", name="source")]
