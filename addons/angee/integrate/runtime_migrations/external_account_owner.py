"""External-account ownership moves from stored REBAC tuples into the ``owner`` column.

Each released ``integrate/external_account#owner@auth/user`` tuple names the user
who owns the account; that user becomes its ``owner``. The relation is backed by
the column now, so the tuples are deleted from both relationship stores. An account
never takes its creator as owner. When a tuple's account is gone, or an account's
tuples name no existing user or more than one, the account stays unowned and the
skip is logged at WARNING; those tuples are deleted too. Reverse recreates no tuples.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.models.ownership import retained_set_null

logger = logging.getLogger(__name__)
STORES = (
    ("Relationship", "resource_type", "resource_id", "subject_type", "subject_id"),
    (
        "RelationshipRegistry",
        "resource_fk__resource_type",
        "resource_fk__resource_id",
        "subject_fk__resource_type",
        "subject_fk__resource_id",
    ),
)


def applies(project_state: ProjectState) -> bool:
    account = project_state.models.get(("integrate", "externalaccount"))
    return account is not None and "owner" not in account.fields


def existing_pk(rows, pk):
    try:
        return rows.filter(pk=pk).values_list("pk", flat=True).first()
    except (ValidationError, ValueError, TypeError):
        return None


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    accounts = apps.get_model("integrate", "ExternalAccount")._base_manager.using(alias).order_by()
    users = apps.get_model(settings.AUTH_USER_MODEL)._base_manager.using(alias).order_by()
    tuples = []
    subjects = defaultdict(set)
    for name, resource_type, resource_id, subject_type, subject_id in STORES:
        rows = apps.get_model("rebac", name)._base_manager.using(alias).order_by().filter(
            **{resource_type: "integrate/external_account", subject_type: "auth/user"},
            relation="owner",
            optional_subject_relation="",
        )
        tuples.append(rows)
        for account_id, user_id in rows.values_list(resource_id, subject_id):
            subjects[account_id].add(user_id)

    owned, skipped = 0, []
    for account_id, user_ids in sorted(subjects.items()):
        account_pk = existing_pk(accounts, account_id)
        owners = {pk for pk in (existing_pk(users, user_id) for user_id in user_ids) if pk is not None}
        if account_pk is None or len(owners) != 1:
            skipped.append(f"{account_id} (users {', '.join(sorted(user_ids))})")
            continue
        owned += accounts.filter(pk=account_pk, owner__isnull=True).update(owner_id=owners.pop())
    deleted = sum(rows.delete()[0] for rows in tuples)
    if skipped:
        logger.warning(
            "External accounts left unowned: the account is gone or its owner tuples name no single "
            "existing user: %s",
            "; ".join(skipped),
        )
    logger.info("External-account owners: %d account(s) owned, %d tuple(s) deleted.", owned, deleted)


class Migration(migrations.Migration):
    dependencies = [("rebac", "__latest__"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "externalaccount",
            "owner",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=retained_set_null,
                related_name="+",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
