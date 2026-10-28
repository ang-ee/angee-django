"""Adopt native label/diagnostic fields without losing retained messaging facts.

Subtype identity, keys and global blank labels remain unchanged. Unknown labels,
canonical label/key collisions and diagnostics changed by the bounded field
refuse the atomic migration before any data write. Affected diagnostic rows need
an explicit retention decision; this migration never truncates retained text.
The canonical label normalization cannot safely be reversed after later writes.
"""

from django.core.exceptions import ValidationError
from django.db import migrations, router
from django.db.migrations.state import ProjectState

from angee.base.fields import DiagnosticTextField, ModelLabelField

_LABEL_FIELD = ModelLabelField(max_length=128, blank=True, default="")
_DIAGNOSTIC_FIELD = DiagnosticTextField(max_length=8192, blank=True, default="")


def applies(state: ProjectState) -> bool:
    subtype = state.models.get(("messaging", "messagesubtype"))
    notification = state.models.get(("messaging", "threadnotification"))
    return subtype is not None and notification is not None and (
        not isinstance(subtype.fields["model_label"], ModelLabelField)
        or not isinstance(notification.fields["failure_reason"], DiagnosticTextField)
    )


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    subtype = apps.get_model("messaging", "MessageSubtype")
    notification = apps.get_model("messaging", "ThreadNotification")
    normalized = []
    subtype_rows = subtype._base_manager.using(alias)
    if router.allow_migrate_model(alias, subtype):
        identities = {}
        for pk, label, key in subtype_rows.order_by("pk").values_list("pk", "model_label", "key"):
            try:
                canonical = _LABEL_FIELD.normalize(label)
            except ValidationError as error:
                raise ValueError(f"Message subtype {pk} has an unknown model label.") from error
            identity = (canonical, key)
            if identity in identities:
                raise ValueError(
                    f"Message subtypes {identities[identity]} and {pk} have colliding canonical model/key identities."
                )
            identities[identity] = pk
            if canonical != label:
                normalized.append((pk, canonical))
    if router.allow_migrate_model(alias, notification):
        rows = notification._base_manager.using(alias)
        for pk, reason in rows.order_by("pk").values_list("pk", "failure_reason").iterator(chunk_size=500):
            if _DIAGNOSTIC_FIELD.get_prep_value(reason) != reason:
                raise ValueError(
                    f"Notification {pk} has retained failure text outside the native field contract; "
                    "preserve that diagnostic through an explicit retention decision before retrying this migration."
                )
    for pk, canonical in normalized:
        subtype_rows.filter(pk=pk).update(model_label=canonical)
    if normalized:
        schema_editor.connection.check_constraints(table_names=[subtype._meta.db_table])


class Migration(migrations.Migration):
    operations = [
        migrations.RunPython(forwards),
        migrations.AlterField("messagesubtype", "model_label", _LABEL_FIELD),
        migrations.AlterField("threadnotification", "failure_reason", _DIAGNOSTIC_FIELD),
    ]
