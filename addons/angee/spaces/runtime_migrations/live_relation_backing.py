"""Retire tuple mirrors after roster and thread reach become live-backed."""

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import SqidField


def _legacy_id(value, *, prefix):
    """Encode one historical public id through its owning field implementation."""

    return SqidField(real_field_name="id", prefix=prefix, min_length=8).public_id_from_value(value)


def applies(project_state: ProjectState) -> bool:
    """Apply to the exact snapshot-backed roster after Thread.groups exists."""

    membership = project_state.models.get(("spaces", "membership"))
    thread = project_state.models.get(("messaging", "thread"))
    if membership is None and thread is None:
        return False
    if membership is None or thread is None:
        raise ImproperlyConfigured(
            "angee.spaces:live_relation_backing found only part of its composed model state"
        )
    has_snapshot = "granted_user" in membership.fields
    if not has_snapshot:
        if not {"group", "party", "role"}.issubset(membership.fields):
            raise ImproperlyConfigured(
                "angee.spaces:live_relation_backing found an unexpected current Membership state"
            )
        if "groups" in thread.fields and "group" not in thread.fields:
            return False
        raise ImproperlyConfigured(
            "angee.spaces:live_relation_backing found an unexpected current Thread state"
        )
    required_membership = {"group", "party", "role", "granted_user"}
    if not required_membership.issubset(membership.fields):
        raise ImproperlyConfigured(
            "angee.spaces:live_relation_backing found a partial Membership mirror state"
        )
    if "groups" in thread.fields and "group" not in thread.fields:
        return True
    if "group" in thread.fields and "groups" not in thread.fields:
        return False
    raise ImproperlyConfigured(
        "angee.spaces:live_relation_backing found an unexpected messaging.Thread group state"
    )


def _through_field_for(through_model, target_model):
    """Return the through-table FK whose remote model is ``target_model``."""

    return next(
        field
        for field in through_model._meta.fields
        if getattr(getattr(field, "remote_field", None), "model", None) is target_model
    )


def _delete_tuple(rows, *, registry=False, **identity):
    """Delete one exact uncaveated tuple in either physical storage shape."""

    if registry:
        identity = {
            "resource_fk__resource_type": identity.pop("resource_type"),
            "resource_fk__resource_id": identity.pop("resource_id"),
            "subject_fk__resource_type": identity.pop("subject_type"),
            "subject_fk__resource_id": identity.pop("subject_id"),
            **identity,
        }
    rows.filter(**identity, optional_subject_relation="", caveat_name="").delete()


def remove_evidenced_mirrors(apps, schema_editor) -> None:
    """Delete only uncaveated tuples evidenced by the legacy source rows."""

    database = schema_editor.connection.alias
    relationship = apps.get_model("rebac", "Relationship")
    registry = apps.get_model("rebac", "RelationshipRegistry")
    stores = (
        (relationship._base_manager.using(database), False),
        (registry._base_manager.using(database), True),
    )

    membership = apps.get_model("spaces", "Membership")
    roster = (
        membership._base_manager.using(database)
        .filter(granted_user_id__isnull=False)
        .values_list("group_id", "role", "granted_user_id")
    )
    for group_id, role, user_id in roster.iterator():
        for rows, is_registry in stores:
            _delete_tuple(
                rows,
                registry=is_registry,
                resource_type="spaces/group",
                resource_id=_legacy_id(group_id, prefix="grp_"),
                relation=str(role),
                subject_type="auth/user",
                subject_id=_legacy_id(user_id, prefix="usr_"),
            )

    thread = apps.get_model("messaging", "Thread")
    through = thread.groups.through
    thread_field = _through_field_for(through, thread)
    group = apps.get_model("spaces", "Group")
    group_field = _through_field_for(through, group)
    audiences = through._base_manager.using(database).values_list(
        f"{thread_field.name}_id", f"{group_field.name}_id"
    )
    for thread_id, group_id in audiences.iterator():
        for rows, is_registry in stores:
            _delete_tuple(
                rows,
                registry=is_registry,
                resource_type="messaging/thread",
                resource_id=_legacy_id(thread_id, prefix="thr_"),
                relation="group",
                subject_type="spaces/group",
                subject_id=_legacy_id(group_id, prefix="grp_"),
            )


class Migration(migrations.Migration):
    """Remove the obsolete derived-grantee snapshot column."""

    dependencies = [
        ("spaces", "__latest__"),
        ("messaging", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations = [
        migrations.RunPython(remove_evidenced_mirrors, migrations.RunPython.noop),
        migrations.RemoveField(model_name="membership", name="granted_user"),
    ]
