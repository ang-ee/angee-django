"""Normalize retained person emails before the host adds their unique constraint.

Resolve collisions with iam_email_collisions before applying this transition.
Rollback retains the normalized values; original casing and whitespace are lost.
"""

from collections import Counter

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("iam", "user"))
    if model is None or not {"email", "kind"}.issubset(model.fields):
        return False
    for constraint in model.options.get("constraints", []):
        if constraint.name != "iam_user_person_email_unique":
            continue
        if (
            isinstance(constraint, models.UniqueConstraint)
            and constraint.fields == ("email",)
            and constraint.condition == (models.Q(kind="person") & ~models.Q(email=""))
        ):
            return False
        raise ValueError(
            "Person email uniqueness has a partial transition; "
            "normalize person emails before adding the stored-email constraint."
        )
    return True


def normalize_email(email):
    # Frozen Python rule: do not import the live manager into migration history.
    return (email or "").strip().lower()


def forwards(apps, schema_editor):
    rows = (
        apps.get_model("iam", "User")._base_manager.using(schema_editor.connection.alias)
        .filter(kind="person").order_by()
    )
    normalized = [(pk, normalize_email(email)) for pk, email in rows.values_list("pk", "email").iterator()]
    counts = Counter(email for _, email in normalized if email)
    collision_count = sum(count > 1 for count in counts.values())
    if collision_count:
        raise RuntimeError(
            f"{collision_count} normalized person email addresses collide; "
            "resolve iam_email_collisions before migrating."
        )
    for pk, email in normalized:
        rows.filter(pk=pk).update(email=email)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
