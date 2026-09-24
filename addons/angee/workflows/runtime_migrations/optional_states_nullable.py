"""Replace optional workflow state sentinels without dropping retained rows.

Field choices and constraints are frozen here, independently of live models.
Rollback restores the empty-string representation before NOT NULL and the old
checks return. Only the five columns named in STATE_FIELDS are rewritten.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

STATE_FIELDS = (
    ("workflowrun", "parent_relation"),
    ("workflowrun", "test_scope"),
    ("steprun", "waiting_kind"),
    ("stepattempt", "lease_revocation_reason"),
    ("stepattempt", "result_kind"),
)


def applies(project_state: ProjectState) -> bool:
    if not all(
        (model := project_state.models.get(("workflows", model_name))) is not None
        and field_name in model.fields
        for model_name, field_name in STATE_FIELDS
    ):
        return False
    # A schema-only nullability alteration can leave the old sentinel checks.
    # Already-current checks must not be replaced: their rollback requires NULL.
    return any(
        not project_state.models["workflows", model_name].fields[field_name].null
        for model_name, field_name in STATE_FIELDS
    ) or any(
        operation.constraint not in project_state.models["workflows", operation.model_name].options.get(
            "constraints", [],
        )
        for operation in Migration.operations if isinstance(operation, migrations.AddConstraint)
    )


def forwards(apps, schema_editor):
    # A literal expression bypasses StateField's '' -> None value preparation.
    empty = models.Value("", output_field=models.CharField())
    for model_name, field_name in STATE_FIELDS:
        rows = apps.get_model("workflows", model_name)._base_manager.using(schema_editor.connection.alias)
        rows.filter(**{field_name: empty}).update(**{field_name: None})


def backwards(apps, schema_editor):
    empty = models.Value("", output_field=models.CharField())
    for model_name, field_name in STATE_FIELDS:
        rows = apps.get_model("workflows", model_name)._base_manager.using(schema_editor.connection.alias)
        rows.filter(**{f"{field_name}__isnull": True}).update(**{field_name: empty})


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint("workflowrun", "chk_wfr_test_scope"),
        migrations.RemoveConstraint("stepattempt", "chk_wsa_revocation_pair"),
        migrations.RemoveConstraint("stepattempt", "chk_wsa_result_pair"),
        migrations.RemoveConstraint("stepattempt", "chk_wsa_orchestration_error"),
        migrations.AlterField(
            "workflowrun", "parent_relation",
            StateField(
                choices=[("owned_call", "Owned call"), ("continuation", "Continuation")],
                null=True, blank=True, editable=False,
            ),
        ),
        migrations.AlterField(
            "workflowrun", "test_scope",
            StateField(
                choices=[("whole", "Whole workflow"), ("node", "Selected node")],
                null=True, blank=True, editable=False,
            ),
        ),
        migrations.AlterField(
            "steprun", "waiting_kind",
            StateField(
                choices=[
                    ("scheduled", "Scheduled"), ("approval", "Approval"),
                    ("external", "External input"), ("children", "Child steps"),
                ],
                null=True, blank=True,
            ),
        ),
        migrations.AlterField(
            "stepattempt", "lease_revocation_reason",
            StateField(
                choices=[("canceled", "Canceled"), ("heartbeat_lost", "Heartbeat Lost"), ("superseded", "Superseded")],
                null=True, blank=True, db_index=False,
            ),
        ),
        migrations.AlterField(
            "stepattempt", "result_kind",
            StateField(
                choices=[
                    ("done", "Done"), ("wait", "Wait"), ("suspend", "Suspend"), ("error", "Error"),
                    ("no_result", "No Result"), ("preparation_error", "Preparation Error"),
                    ("transient_error", "Transient Error"),
                ],
                null=True, blank=True, db_index=False,
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AddConstraint(
            "workflowrun",
            models.CheckConstraint(
                condition=(
                    (models.Q(test_scope__isnull=True) | models.Q(test_scope__isnull=False, test_scope="whole"))
                    & models.Q(origin="test", test_step__isnull=True, test_source_step_id__isnull=True)
                    | models.Q(
                        origin="test", test_scope="node", test_scope__isnull=False,
                        test_step__isnull=False, test_source_step_id__isnull=False,
                    )
                    | (~models.Q(origin="test") & models.Q(
                        test_scope__isnull=True, test_step__isnull=True, test_source_step_id__isnull=True,
                    ))
                ),
                name="chk_wfr_test_scope",
            ),
        ),
        migrations.AddConstraint(
            "stepattempt",
            models.CheckConstraint(
                condition=(
                    models.Q(lease_revoked_at__isnull=True, lease_revocation_reason__isnull=True)
                    | models.Q(lease_revoked_at__isnull=False, lease_revocation_reason__isnull=False)
                ),
                name="chk_wsa_revocation_pair",
            ),
        ),
        migrations.AddConstraint(
            "stepattempt",
            models.CheckConstraint(
                condition=(
                    models.Q(result_recorded_at__isnull=True, result_kind__isnull=True)
                    | models.Q(result_recorded_at__isnull=False, result_kind__isnull=False)
                ),
                name="chk_wsa_result_pair",
            ),
        ),
        migrations.AddConstraint(
            "stepattempt",
            models.CheckConstraint(
                condition=models.Q(orchestration_error="") | models.Q(
                    result_kind__isnull=False, result_kind="transient_error",
                ),
                name="chk_wsa_orchestration_error",
            ),
        ),
    ]
