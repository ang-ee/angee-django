"""Retire legacy question links and account receipts before the decision cutover."""

from django.db import migrations


def applies(state):
    decision = state.models.get(("decisions", "decision"))
    need = state.models.get(("intake", "need"))
    return bool(decision is not None and need is not None
                and {"form_schema", "step_run", "subject_object_id"}.intersection(decision.fields)
                and {"access_decision", "admitted_user"}.intersection(need.fields))


class RemoveIfPresent(migrations.RemoveField):
    """Some legacy stacks predate the account admission receipt."""

    def state_forwards(self, app_label, state):
        if self.name in state.models[app_label, self.model_name_lower].fields:
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self.name in from_state.models[app_label, self.model_name_lower].fields:
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if self.name in to_state.models[app_label, self.model_name_lower].fields:
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    dependencies = [("intake", "__latest__")]
    operations = [RemoveIfPresent("need", "access_decision"), RemoveIfPresent("need", "admitted_user")]
