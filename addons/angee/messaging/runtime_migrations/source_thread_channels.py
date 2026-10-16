"""Retain one physical conversation per channel before enforcing its identity.

Messages keep their primary keys and source payloads. Only their container and
its per-message participant/notification pointers move. Source attachments on
the old container remain retained. What one shared container showed as one
conversation stays one: each copied container redirects to the original, and an
email received through two accounts redirects its newer copies to the oldest
(``merged_into``), the same retained redirect later ``same_as`` evidence writes.
"""

from django.db import migrations, models, router
from django.db.models.functions import MD5, Coalesce

OLD = "uq_thread_platform_external_id"
SOURCE = "uq_thread_channel_external_id"
LOCAL = "uq_thread_local_external_id"


def applies(state):
    thread = state.models.get(("messaging", "thread"))
    message = state.models.get(("messaging", "message"))
    if thread is None or message is None or "is_trashed" not in message.fields:
        return False
    constraints = {constraint.name for constraint in thread.options.get("constraints", ())}
    if OLD in constraints and not constraints & {SOURCE, LOCAL}:
        return True
    if {SOURCE, LOCAL} <= constraints and OLD not in constraints:
        return False
    raise ValueError("Thread has a partial channel identity transition; reconcile migration history.")


def copy_row(row, *, using, **updates):
    """Copy historical concrete columns while keeping the original audit times."""

    values = {
        field.attname: field.value_from_object(row)
        for field in row._meta.concrete_fields if not field.primary_key
    }
    values.update(updates)
    copied = type(row)._base_manager.using(using).create(**values)
    type(row)._base_manager.using(using).filter(pk=copied.pk).update(**values)
    return copied


def retain_channel_occurrences(apps, schema_editor):
    thread = apps.get_model("messaging", "Thread")
    message = apps.get_model("messaging", "Message")
    participant = apps.get_model("messaging", "Participant")
    follower = apps.get_model("messaging", "ThreadFollower")
    notification = apps.get_model("messaging", "ThreadNotification")
    attachment = apps.get_model("messaging", "ThreadAttachment")
    alias = schema_editor.connection.alias
    if not thread._meta.can_migrate(alias) or not router.allow_migrate_model(alias, thread):
        return
    refused = [
        model._meta.label for model in (message, participant, follower, notification, attachment)
        if not model._meta.can_migrate(alias) or not router.allow_migrate_model(alias, model)
    ]
    if refused:
        raise ValueError(
            f"Source-thread retention requires all related models on database {alias!r}; "
            f"refused: {', '.join(refused)}."
        )
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    messages = message._base_manager.using(alias)
    threads = thread._base_manager.using(alias)
    participants = participant._base_manager.using(alias)
    notifications = notification._base_manager.using(alias)
    follows = follower._base_manager.using(alias)
    chatter = attachment._base_manager.using(alias).filter(role="chatter").values("thread_id")
    # Explicit record chatter keeps its own container. Channel-backed source
    # threads may have accepted messages from more than one account historically;
    # only a thread holding a message from another channel than its own splits,
    # so a single-channel conversation is never visited.
    foreign = messages.filter(channel__isnull=False).filter(
        models.Q(thread__channel__isnull=True) | ~models.Q(channel_id=models.F("thread__channel_id")),
    )
    source_threads = threads.exclude(pk__in=chatter).filter(
        pk__in=foreign.order_by().values("thread_id"),
    ).order_by("pk")
    for original in source_threads.iterator():
        original_follows = list(
            follows.filter(thread_id=original.pk).select_related("last_read_message").order_by("pk"),
        )
        # Capture the chronological cutoff before messages change containers.
        # Every resulting follow, including the original, must point only to
        # its own physical thread so purging another channel cannot erase it.
        cutoffs = {
            follow.pk: (
                (follow.last_read_message.sent_at or follow.last_read_message.created_at, follow.last_read_message.pk)
                if follow.last_read_message is not None else None
            )
            for follow in original_follows
        }
        receipts = [(follow.pk, original.pk, cutoffs[follow.pk]) for follow in original_follows]
        channel_ids = list(messages.filter(thread_id=original.pk, channel__isnull=False)
                           .order_by("channel_id").values_list("channel_id", flat=True).distinct())
        changed_threads = {original.pk}
        for channel_id in channel_ids:
            if original.channel_id == channel_id:
                continue
            occurrence = copy_row(original, using=alias, channel_id=channel_id, owner_id=None,
                                  message_count=0, last_message_at=None, merged_into_id=original.pk)
            moved = messages.filter(thread_id=original.pk, channel_id=channel_id)
            moved_ids = moved.order_by().values("pk")
            participants.filter(message_id__in=moved_ids).update(thread_id=occurrence.pk)
            notifications.filter(message_id__in=moved_ids).update(thread_id=occurrence.pk)
            # Preserve only the channel's known thread-level member occurrences.
            handles = participants.filter(message_id__in=moved_ids).order_by().values("handle_id")
            for member in participants.filter(thread_id=original.pk, message__isnull=True,
                                              handle_id__in=handles).order_by("pk"):
                copy_row(member, using=alias, thread_id=occurrence.pk)
            moved.update(thread_id=occurrence.pk)
            attachment_ids = {}
            source_edges = attachment._base_manager.using(alias).filter(thread_id=original.pk, role="source")
            for edge in source_edges.order_by("pk"):
                copied_edge = copy_row(edge, using=alias, thread_id=occurrence.pk)
                attachment_ids[edge.pk] = copied_edge.pk
            follower_ids = {}
            for follow in original_follows:
                copied = copy_row(follow, using=alias, thread_id=occurrence.pk,
                                  attachment_id=attachment_ids.get(follow.attachment_id),
                                  last_read_message_id=None)
                follower_ids[follow.pk] = copied.pk
                receipts.append((copied.pk, occurrence.pk, cutoffs[follow.pk]))
            # Per-message deliveries moved above. Their retained identities,
            # audit columns and acknowledgements stay intact; only their local
            # follow/source-edge pointers follow the new physical occurrence.
            for old_id, new_id in follower_ids.items():
                notifications.filter(thread_id=occurrence.pk, follower_id=old_id).update(follower_id=new_id)
            for old_id, new_id in attachment_ids.items():
                notifications.filter(thread_id=occurrence.pk, attachment_id=old_id).update(attachment_id=new_id)
            changed_threads.add(occurrence.pk)
        for follow_id, thread_id, cutoff in receipts:
            last_read_id = None
            if cutoff is not None:
                at, message_id = cutoff
                last_read_id = messages.filter(thread_id=thread_id).annotate(
                    _at=Coalesce("sent_at", "created_at"),
                ).filter(models.Q(_at__lt=at) | models.Q(_at=at, pk__lte=message_id)).order_by(
                    "-_at", "-pk",
                ).values_list("pk", flat=True).first()
            follows.filter(pk=follow_id).update(last_read_message_id=last_read_id)
        for thread_id in sorted(changed_threads):
            totals = messages.filter(thread_id=thread_id, is_trashed=False).aggregate(
                count=models.Count("pk"), latest=models.Max("sent_at"),
            )
            threads.filter(pk=thread_id).update(message_count=totals["count"], last_message_at=totals["latest"])
    # One email received through several accounts: its newer copies redirect to the oldest.
    emails = messages.filter(message_type="email", channel__isnull=False).exclude(external_id="")
    groups = emails.values("platform", "external_id").annotate(
        first=models.Min("pk"), copies=models.Count("pk"),
    ).filter(copies__gt=1).order_by("first")
    for group in groups.iterator():
        emails.filter(platform=group["platform"], external_id=group["external_id"]).exclude(
            pk=group["first"],
        ).update(merged_into_id=group["first"])


def redirect(model):
    return models.ForeignKey(
        f"messaging.{model}", null=True, blank=True, editable=False, on_delete=models.PROTECT,
        related_name="merged_from",
    )


class Migration(migrations.Migration):
    # RuntimeMigrations.plan appends the generated app leaf.
    dependencies = []
    operations = [
        migrations.AddField("thread", "merged_into", redirect("Thread")),
        migrations.AddField("message", "merged_into", redirect("Message")),
        migrations.RemoveConstraint(model_name="thread", name=OLD),
        migrations.RunPython(retain_channel_occurrences, hints={"model_name": "thread"}),
        migrations.AddConstraint(
            model_name="thread",
            constraint=models.UniqueConstraint(
                models.F("channel"), models.F("platform"), MD5("external_id"),
                condition=models.Q(channel__isnull=False) & ~models.Q(external_id=""), name=SOURCE,
            ),
        ),
        migrations.AddConstraint(
            model_name="thread",
            constraint=models.UniqueConstraint(
                models.F("platform"), MD5("external_id"),
                condition=models.Q(channel__isnull=True) & ~models.Q(external_id=""), name=LOCAL,
            ),
        ),
    ]
