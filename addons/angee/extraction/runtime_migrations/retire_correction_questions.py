from django.db import migrations


def applies(state):
    decision = state.models.get(("decisions", "decision"))
    extraction = state.models.get(("extraction", "extraction"))
    return (
        decision is not None
        and bool({"form_schema", "step_run"}.intersection(decision.fields))
        and extraction is not None
        and "correction_decision" in extraction.fields
    )


class Migration(migrations.Migration):
    dependencies = [("extraction", "__latest__")]
    operations = [migrations.RemoveField("extraction", "correction_decision")]
