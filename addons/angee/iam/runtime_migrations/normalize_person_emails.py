"""Normalize retained person emails and then add their unique constraint.

Resolve collisions with iam_email_collisions before applying this transition.
Rollback retains the normalized values; original casing and whitespace are lost.
"""

from collections import Counter

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply only to a user history that still lacks the person email constraint.

    A history that already carries the constraint is complete: a new stack's
    initial migration creates it with the empty table, so nothing needs normalizing.
    """

    model = project_state.models.get(("iam", "user"))
    if model is None or not {"email", "kind"}.issubset(model.fields):
        return False
    return not any(
        constraint.name == "iam_user_person_email_unique" for constraint in model.options.get("constraints", [])
    )


def normalize_email(email):
    # Frozen Python rule: do not import the live manager into migration history.
    return (email or "").strip().lower()


def forwards(apps, schema_editor):
    rows = (
        apps.get_model("iam", "User")._base_manager.using(schema_editor.connection.alias)
        .filter(kind="person").order_by()
    )
    counts = Counter()
    for _pk, email in rows.values_list("pk", "email").iterator():
        key = normalize_email(email)
        if key:
            counts[key] += 1
    collision_count = sum(count > 1 for count in counts.values())
    if collision_count:
        raise RuntimeError(
            f"{collision_count} normalized person email addresses collide; "
            "resolve iam_email_collisions before migrating."
        )
    for pk, email in rows.values_list("pk", "email").iterator():
        key = normalize_email(email)
        if key != email:
            rows.filter(pk=pk).update(email=key)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddConstraint(
            "user",
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(kind="person") & ~models.Q(email=""),
                name="iam_user_person_email_unique",
            ),
        ),
    ]
