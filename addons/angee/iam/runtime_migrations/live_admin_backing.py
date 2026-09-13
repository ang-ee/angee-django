"""Retire the historical superuser-to-admin tuple mirror."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import SqidField

_ADMIN_TYPE = "angee/role"
_ADMIN_ID = "admin"
_MEMBER_RELATION = "member"


def applies(project_state: ProjectState) -> bool:
    """Materialize once whenever the exact IAM User source state exists.

    This is a pure data transition: removing a model ``save()`` side effect does
    not alter Django's ProjectState, so historical and fresh states are
    intentionally indistinguishable. The runtime migration origin/digest is the
    durable one-time marker; a fresh database executes a harmless empty cleanup.
    """

    user = project_state.models.get(("iam", "user"))
    if user is None:
        return False
    required = {"is_superuser", "is_active"}
    if not required.issubset(user.fields):
        raise ImproperlyConfigured(
            "angee.iam:live_admin_backing requires User.is_superuser and User.is_active"
        )
    if not isinstance(user.fields["is_superuser"], models.BooleanField) or not isinstance(
        user.fields["is_active"],
        models.BooleanField,
    ):
        raise ImproperlyConfigured(
            "angee.iam:live_admin_backing found an unexpected IAM User admin state"
        )
    return True


def _admin_rows(model: Any, database: str, *, registry: bool) -> Any:
    """Return the complete stored admin-member namespace in one physical store."""

    identity = {
        "relation": _MEMBER_RELATION,
    }
    if registry:
        identity.update(
            resource_fk__resource_type=_ADMIN_TYPE,
            resource_fk__resource_id=_ADMIN_ID,
        )
    else:
        identity.update(resource_type=_ADMIN_TYPE, resource_id=_ADMIN_ID)
    rows = model._base_manager.using(database).filter(**identity)
    return rows.select_related("subject_fk") if registry else rows


def remove_evidenced_admin_mirrors(apps: Any, schema_editor: Any) -> None:
    """Validate the admin namespace, then delete historical superuser mirrors."""

    database = schema_editor.connection.alias
    user = apps.get_model("iam", "User")
    codec = SqidField(real_field_name="id", prefix="usr_", min_length=8)
    evidenced = {
        codec.public_id_from_value(value)
        for value in user._base_manager.using(database)
        .filter(is_superuser=True)
        .values_list("pk", flat=True)
    }
    stores = (
        (apps.get_model("rebac", "Relationship"), False),
        (apps.get_model("rebac", "RelationshipRegistry"), True),
    )
    validated: list[Any] = []
    for model, registry in stores:
        rows = _admin_rows(model, database, registry=registry)
        for row in rows:
            subject_type = (
                str(row.subject_fk.resource_type) if registry else str(row.subject_type)
            )
            subject_id = str(row.subject_fk.resource_id) if registry else str(row.subject_id)
            if (
                subject_type != "auth/user"
                or str(row.optional_subject_relation)
                or str(row.caveat_name)
                or bool(row.caveat_context)
                or row.expires_at is not None
                or subject_id not in evidenced
            ):
                raise ImproperlyConfigured(
                    "angee.iam:live_admin_backing found a non-mirror admin membership; "
                    "remove or migrate manual, group, subject-set, caveated, expiring, "
                    "or non-superuser admin grants before retrying"
                )
        validated.append(rows)
    for rows in validated:
        rows.delete()


class Migration(migrations.Migration):
    """Remove tuples formerly mirrored from the superuser field."""

    atomic = True
    dependencies = [
        ("iam", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations = [
        migrations.RunPython(
            remove_evidenced_admin_mirrors,
            migrations.RunPython.noop,
        )
    ]
