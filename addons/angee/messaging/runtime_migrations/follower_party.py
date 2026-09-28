"""Move follower identity to the user's person without replacing follower rows."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    follower = project_state.models.get(("messaging", "threadfollower"))
    if follower is None or "user" not in follower.fields:
        return False
    if "party" in follower.fields:
        raise ValueError("Complete or reverse the partial follower-party transition before upgrading.")
    return True


def immediate_constraints(schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def forwards(apps, schema_editor):
    immediate_constraints(schema_editor)
    alias = schema_editor.connection.alias
    followers = apps.get_model("messaging", "ThreadFollower")._base_manager.using(alias).order_by()
    people = apps.get_model("parties", "Person")._base_manager.using(alias).order_by()
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    user_ids = followers.values_list("user_id", flat=True).distinct()
    for user in user_model._base_manager.using(alias).order_by().filter(pk__in=user_ids).iterator():
        # Frozen PartyManager.for_user defaults: historical models have no methods.
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        display_name = next(
            (
                str(value).strip()
                for value in (full_name, getattr(user, "username", ""), getattr(user, "email", ""), str(user.pk))
                if str(value or "").strip()
            ),
        )
        person, _created = people.get_or_create(
            user_id=user.pk,
            defaults={"display_name": display_name, "created_by_id": user.pk},
        )
        followers.filter(user_id=user.pk).update(party_id=person.pk)


def backwards(apps, schema_editor):
    immediate_constraints(schema_editor)
    alias = schema_editor.connection.alias
    followers = apps.get_model("messaging", "ThreadFollower")._base_manager.using(alias).order_by()
    people = apps.get_model("parties", "Person")._base_manager.using(alias).order_by()
    if followers.exclude(party_id__in=people.filter(user__isnull=False).values("pk")).exists():
        raise IrreversibleError("Followers without accounts cannot be restored to user-keyed follows.")
    for person in people.filter(pk__in=followers.values("party_id")).iterator():
        followers.filter(party_id=person.pk).update(user_id=person.user_id)


class Migration(migrations.Migration):
    dependencies = [("parties", "__latest__"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "threadfollower",
            "party",
            models.ForeignKey("parties.Party", null=True, on_delete=models.CASCADE, related_name="+"),
        ),
        # Nullable in historical state so reversing RemoveField can restore the
        # column before backwards() fills it. Its original non-null state follows.
        migrations.AlterField(
            "threadfollower",
            "user",
            models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="+"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            "threadfollower",
            "party",
            models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+"),
        ),
        migrations.RemoveConstraint("threadfollower", "uq_thread_follower_thread_user"),
        # Index.set_name_with_model: messaging_threadfollower, columns user_id, thread_id.
        migrations.RemoveIndex("threadfollower", "messaging_t_user_id_88fea0_idx"),
        migrations.AddConstraint(
            "threadfollower",
            models.UniqueConstraint(fields=("thread", "party"), name="uq_thread_follower_thread_party"),
        ),
        migrations.AddIndex(
            "threadfollower",
            models.Index(fields=("party", "thread"), name="ix_follower_party_thread"),
        ),
        migrations.RemoveField("threadfollower", "user"),
    ]
