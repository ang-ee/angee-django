"""Retire the pre-rebuild workflows engine's history before the current schema.

Stacks materialized before the engine rebuild (``752281db0``) carry the old
graph-as-rows engine: ``Step`` and ``Edge`` rows, a dispatcher, attempts and
decisions inside workflows, and no ``WorkflowVersion``. Its data is not carried
forward. This migration drops every workflows model the history holds, and the
following ``makemigrations`` creates the current engine's schema from scratch.
"""

from django.db import migrations


def applies(state):
    """Match only the pre-rebuild engine: graph rows exist and versions do not."""

    models = {name for app_label, name in state.models if app_label == "workflows"}
    return "step" in models and "workflowversion" not in models


class RetireWorkflowsModels(migrations.operations.base.Operation):
    """Drop every workflows table and forget its models."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        for key in [key for key in state.models if key[0] == app_label]:
            state.remove_model(*key)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        for model in from_state.apps.get_app_config(app_label).get_models():
            schema_editor.delete_model(model)

    def describe(self):
        return "Retire the pre-rebuild workflows engine"


class Migration(migrations.Migration):
    dependencies = [("workflows", "__latest__")]
    operations = [RetireWorkflowsModels()]
