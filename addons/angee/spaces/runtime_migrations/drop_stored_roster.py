"""Convert retired stored grants into confirmed rosters and thread-group links.

Existing roster rows win. Where several legacy roles reach one new pair, the
strongest role wins. IAM group expansion snapshots direct user members at upgrade
time. Only non-expiring, uncaveated tuples grant a roster row or group-member
expansion. Missing people are created with the column values written by
``angee.parties.managers.PartyManager.for_user`` and its ``_user_display_name``
helper: user_id, display_name and created_by_id; no name-part columns are written.
Unresolvable subjects and existing rows that reduce access are counted separately.
All retired tuples are deleted, including counted non-granting tuples.

``applies()`` is true whenever the required models exist because the old state is
data. A stack without retired tuples materializes this as a no-op. Reversal keeps
canonical rows because subsequent edits cannot be reconstructed as grants.
"""

import logging
from collections import Counter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import migrations, models, router
from django.db.migrations.state import ProjectState

logger = logging.getLogger(__name__)
ROLE_STRENGTH = {"owner": 3, "moderator": 2, "member": 1, "viewer": 0}
UNCONDITIONAL = models.Q(expires_at__isnull=True, caveat_name="")


def applies(project_state: ProjectState) -> bool:
    return all(
        key in project_state.models
        for key in (
            ("spaces", "group"), ("spaces", "membership"), ("parties", "person"),
            ("messaging", "thread"), ("rebac", "relationship"), ("rebac", "relationshipregistry"),
        )
    )


def existing_pk(rows, **lookup):
    try:
        return rows.filter(**lookup).values_list("pk", flat=True).first()
    except (ValidationError, ValueError, TypeError):
        return None


def person_for_user(people, users, user_id, counts):
    person_pk = existing_pk(people, user_id=user_id)
    if person_pk is not None:
        return person_pk
    try:
        user = users.filter(pk=user_id).first()
    except (ValidationError, ValueError, TypeError):
        return None
    if user is None:
        return None
    full_name = f"{user.first_name} {user.last_name}".strip()
    display_name = next(
        (value.strip() for value in (full_name, user.username, user.email) if (value or "").strip()),
        str(user.pk),
    )
    person, created = people.get_or_create(
        user_id=user.pk,
        defaults={"display_name": display_name, "created_by_id": user.pk},
    )
    counts["people_created"] += created
    return person.pk


def user_subjects(stores, iam_groups, subject_type, subject_id, relation, counts):
    if subject_type == "auth/user" and not relation:
        yield subject_id
        return
    if (
        subject_type != "auth/group" or relation != "member" or iam_groups is None
        or existing_pk(iam_groups, pk=subject_id) is None
    ):
        yield None
        return
    found = False
    for rows, resource_type, resource_id, member_type, member_id in stores:
        members = rows.filter(**{
            resource_type: "auth/group", resource_id: subject_id, "relation": "member",
        })
        non_granting = members.exclude(UNCONDITIONAL).count()
        counts["non_granting_group_member_matches"] += non_granting
        found = found or bool(non_granting)
        for kind, identifier, member_relation in members.filter(UNCONDITIONAL).values_list(
            member_type, member_id, "optional_subject_relation",
        ).iterator():
            found = True
            yield identifier if kind == "auth/user" and not member_relation else None
    if not found:
        yield None


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    membership = apps.get_model("spaces", "Membership")
    person = apps.get_model("parties", "Person")
    thread = apps.get_model("messaging", "Thread")
    groups_field = thread._meta.get_field("groups")
    through = groups_field.remote_field.through
    if not all(router.allow_migrate_model(alias, model) for model in (membership, person, through)):
        return
    rosters = membership._base_manager.using(alias).order_by()
    groups = apps.get_model("spaces", "Group")._base_manager.using(alias).order_by()
    people = person._base_manager.using(alias).order_by()
    users = apps.get_model(settings.AUTH_USER_MODEL)._base_manager.using(alias).order_by()
    threads = thread._base_manager.using(alias).order_by()
    links = through._base_manager.using(alias).order_by()
    try:
        iam_groups = apps.get_model("iam", "Group")._base_manager.using(alias).order_by()
    except LookupError:
        iam_groups = None
    stores = []
    for model_name, resource_type, resource_id, subject_type, subject_id in (
        ("Relationship", "resource_type", "resource_id", "subject_type", "subject_id"),
        ("RelationshipRegistry", "resource_fk__resource_type", "resource_fk__resource_id",
         "subject_fk__resource_type", "subject_fk__resource_id"),
    ):
        model = apps.get_model("rebac", model_name)
        if router.allow_migrate_model(alias, model):
            stores.append((model._base_manager.using(alias).order_by(),
                           resource_type, resource_id, subject_type, subject_id))

    counts = Counter()
    # Process roles across both stores in authority order before creating a pair.
    for role in (*ROLE_STRENGTH, "group"):
        for rows, resource_type, resource_id, subject_type, subject_id in stores:
            retired = rows.filter(**{
                resource_type: "messaging/thread" if role == "group" else "spaces/group",
                "relation": role,
            })
            non_granting = retired.exclude(UNCONDITIONAL)
            counts["non_granting_retired_tuples"] += non_granting.count()
            deleted, _details = non_granting.delete()
            counts["retired_tuples_deleted"] += deleted
            for pk, target, kind, identifier, relation in retired.filter(UNCONDITIONAL).values_list(
                "pk", resource_id, subject_type, subject_id, "optional_subject_relation",
            ).iterator():
                if role == "group":
                    thread_pk = existing_pk(threads, pk=target)
                    group_pk = existing_pk(groups, pk=identifier) if kind == "spaces/group" and not relation else None
                    if thread_pk is None or group_pk is None:
                        counts["skipped_subjects"] += 1
                    else:
                        _link, created = links.get_or_create(**{
                            f"{groups_field.m2m_field_name()}_id": thread_pk,
                            f"{groups_field.m2m_reverse_field_name()}_id": group_pk,
                        })
                        counts["thread_links_created" if created else "thread_links_existing"] += 1
                else:
                    group_pk = existing_pk(groups, pk=target)
                    for user_id in user_subjects(stores, iam_groups, kind, identifier, relation, counts):
                        party_pk = (
                            person_for_user(people, users, user_id, counts)
                            if group_pk is not None and user_id is not None else None
                        )
                        if group_pk is None or party_pk is None:
                            counts["skipped_subjects"] += 1
                            continue
                        row, created = rosters.get_or_create(
                            group_id=group_pk, party_id=party_pk,
                            defaults={"role": role, "is_confirmed": True, "is_dismissed": False},
                        )
                        if created:
                            counts["roster_rows_created"] += 1
                        elif (
                            row.is_confirmed and not row.is_dismissed
                            and ROLE_STRENGTH.get(row.role, -1) >= ROLE_STRENGTH[role]
                        ):
                            counts["roster_rows_existing_sufficient"] += 1
                        else:
                            counts["roster_rows_existing_reduced"] += 1
                rows.filter(pk=pk).delete()
                counts["retired_tuples_deleted"] += 1
    level = logging.WARNING if any(counts[key] for key in (
        "roster_rows_existing_reduced", "skipped_subjects",
        "non_granting_retired_tuples", "non_granting_group_member_matches",
    )) else logging.INFO
    logger.log(
        level,
        "Stored roster conversion: people_created=%d roster_rows_created=%d "
        "roster_rows_existing_sufficient=%d roster_rows_existing_reduced=%d "
        "thread_links_created=%d thread_links_existing=%d skipped_subjects=%d "
        "non_granting_retired_tuples=%d non_granting_group_member_matches=%d retired_tuples_deleted=%d",
        *(counts[key] for key in (
            "people_created", "roster_rows_created", "roster_rows_existing_sufficient",
            "roster_rows_existing_reduced", "thread_links_created", "thread_links_existing",
            "skipped_subjects", "non_granting_retired_tuples", "non_granting_group_member_matches",
            "retired_tuples_deleted",
        )),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("rebac", "__latest__"),
        # The composer pins this to the host leaf providing Person.user and the
        # inherited Party.display_name/created_by columns used by person_for_user.
        ("parties", "__latest__"),
        ("messaging", "__latest__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
