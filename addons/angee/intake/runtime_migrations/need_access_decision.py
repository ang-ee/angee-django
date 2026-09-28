"""Backfill access seats after decisions and Need.access_decision are materialized.

Build/makemigrations creates the new schema; the next build attaches this data
step to those leaves. Historical models only; neither relationship store is
touched. Imported approvals invent neither a resolver nor a resolution time.
"""

from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState
from django.utils import timezone

# Frozen output of the approve/deny Pydantic action contract at adoption.
FORM_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"action": {
        "type": "string", "enum": ["approve", "deny"],
        "options": [
            {"value": "approve", "label": "Approve", "verdict": "completed"},
            {"value": "deny", "label": "Deny", "verdict": "rejected"},
        ],
    }},
    "required": ["action"],
    "discriminator": {"propertyName": "action"},
    "oneOf": [
        {
            "additionalProperties": False,
            "description": "Approve the request's account, with an optional explanation.",
            "properties": {
                "reason": {"default": "", "title": "Reason", "type": "string"},
                "action": {"type": "string", "const": "approve"},
            },
            "title": "ApproveNeedAccess", "type": "object", "required": ["action"],
        },
        {
            "additionalProperties": False,
            "description": "Decline access without changing the request's account.",
            "properties": {
                "reason": {"default": "", "title": "Reason", "type": "string"},
                "action": {"type": "string", "const": "deny"},
            },
            "title": "DenyNeedAccess", "type": "object", "required": ["action"],
        },
    ],
}


def applies(project_state: ProjectState) -> bool:
    need = project_state.models.get(("intake", "need"))
    decision = project_state.models.get(("decisions", "decision"))
    if need is None or decision is None:
        return False
    if {"access_verdict", "access_resolution", "access_resolved_by", "access_resolved_at"} & need.fields.keys():
        raise ValueError("Remove the unmaterialized access-column transition before decision adoption.")
    return "access_decision" in need.fields and "intake_need" in decision.fields


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    rows = apps.get_model("intake", "Need")._base_manager.using(alias).order_by()
    groups = apps.get_model("decisions", "DecisionGroup")._base_manager.using(alias).order_by()
    decisions = apps.get_model("decisions", "Decision")._base_manager.using(alias).order_by()
    content_type, _ = apps.get_model("contenttypes", "ContentType")._base_manager.using(alias).get_or_create(
        app_label="intake", model="need",
    )
    untouched_source = models.Q(updated_by_id=models.F("source_message__updated_by_id")) | models.Q(
        updated_by_id__isnull=True, source_message__updated_by_id__isnull=True,
    )
    unconfirmed_copy = models.Q(
        source_message__sender__party_link_confirmed=False,
        party_id=models.F("source_message__sender__party_id"),
    ) & untouched_source
    eligible = rows.filter(party__person__user__is_active=True).exclude(unconfirmed_copy)
    pending = rows.filter(access_decision__isnull=True).annotate(
        approved=models.Exists(eligible.filter(pk=models.OuterRef("pk"))),
    )
    for need in pending.iterator():
        group = groups.create(policy="first", issuer_id=None, settled_at=timezone.now() if need.approved else None)
        decision = decisions.create(
            group_id=group.pk, index=0, kind="intake.access", requester_id=None,
            subject_content_type_id=content_type.pk, subject_object_id=need.pk,
            intake_need_id=need.pk, form_schema=FORM_SCHEMA,
            basis={"migration": "intake.need_access_decision"}, context={"facts": [], "references": []},
            supersede=True, max_attempts=3, revision=1,
            verdict="completed" if need.approved else "pending",
            closed_reason="imported" if need.approved else None,
            resolution={"action": "approve", "reason": ""} if need.approved else {},
        )
        rows.filter(pk=need.pk, access_decision__isnull=True).update(access_decision_id=decision.pk)


def backwards(apps, schema_editor):
    raise IrreversibleError("Retained access decisions cannot be discarded.")


class Migration(migrations.Migration):
    dependencies = [("decisions", "__latest__"), ("parties", "__latest__"), ("contenttypes", "__latest__")]
    operations = [migrations.RunPython(forwards, backwards)]
