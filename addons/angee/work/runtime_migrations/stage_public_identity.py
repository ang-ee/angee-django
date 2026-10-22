"""Retain resource-ledger references when Work stages adopt their own prefix.

Stage primary keys and relation/history pointers do not change. Only canonical
legacy public strings owned by work.Stage are re-encoded; there is no legacy URL
alias. Malformed legacy references fail rather than silently becoming orphaned.
"""

from django.core.exceptions import ValidationError
from django.db import migrations, router
from django.db.migrations.state import ProjectState

from angee.fields import SqidField


def applies(state: ProjectState) -> bool:
    resource = state.models.get(("resources", "resource"))
    return (("work", "stage") in state.models and resource is not None
            and {"target_model", "target_id"} <= resource.fields.keys())


def forwards(apps, schema_editor):
    resource = apps.get_model("resources", "Resource")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, resource):
        return
    # Freeze the former/current field declarations; the native field owns both codecs.
    legacy = SqidField(real_field_name="id", prefix="stg_", min_length=8)
    current = SqidField(real_field_name="id", prefix="wst_", min_length=8)
    rows = resource._base_manager.using(alias).filter(
        target_model__iexact="work.Stage", target_id__startswith="stg_",
    )
    for ledger_pk, public_id in rows.order_by("pk").values_list("pk", "target_id").iterator(chunk_size=500):
        try:
            pk = legacy.public_id_to_value(public_id)
            valid = pk is not None and legacy.public_id_from_value(pk) == public_id
        except (TypeError, ValueError, ValidationError):
            valid = False
        if not valid:
            raise ValueError(f"Resource {ledger_pk} has an invalid legacy work.Stage public ID.")
        rows.filter(pk=ledger_pk, target_id=public_id).update(target_id=current.public_id_from_value(pk))


class Migration(migrations.Migration):
    dependencies = [("work", "__latest__")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
