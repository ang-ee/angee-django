"""Notifications managers for Messaging; public imports remain on the package."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import (
    PermissionDenied,
    SubjectRef,
    current_actor,
    database_context,
    system_context,
    to_subject_ref,
)
from rebac.actors import is_sudo
from rebac.backends import backend
from rebac.resources import model_resource_type

from angee.base.fields import DiagnosticTextField
from angee.base.managers import AngeeManager
from angee.base.permissions import actor_user_id, require_rebac_queryset
from angee.base.querysets import MergeableQuerySet
from angee.base.refs import generic_pointer_model
from angee.base.serialization import strip_null_bytes
from angee.messaging.querysets import (
    ChannelQuerySet as ChannelQuerySet,
)
from angee.messaging.querysets import (
    MessageQuerySet as MessageQuerySet,
)
from angee.messaging.querysets import (
    NotificationPointerIterable as NotificationPointerIterable,
)
from angee.messaging.querysets import (
    PartQuerySet as PartQuerySet,
)
from angee.messaging.querysets import (
    ThreadFollowerQuerySet as ThreadFollowerQuerySet,
)
from angee.messaging.querysets import (
    ThreadNotificationQuerySet as ThreadNotificationQuerySet,
)
from angee.messaging.querysets import (
    ThreadQuerySet as ThreadQuerySet,
)

if TYPE_CHECKING:

    from angee.messaging.models import (
        NotificationPolicy,
        ThreadFollower,
        ThreadNotification,
    )

from angee.messaging.managers.threads import _record_attachment, _record_thread_title
from angee.messaging.querysets import _MESSAGE_ORDER, _resolve_user_id

_ThreadFollowerManagerBase = AngeeManager.from_queryset(ThreadFollowerQuerySet)


class ThreadFollowerManager[ThreadFollowerT: ThreadFollower](_ThreadFollowerManagerBase[ThreadFollowerT]):
    """Owns party subscriptions to model-attached chatter threads."""

    def for_message_expression(self, user: Any) -> models.QuerySet[ThreadFollowerT]:
        """Correlate follows to an outer message, ordered by the effective preference."""

        thread_model = self.model.thread.field.related_model
        survivor = thread_model(pk=MergeableQuerySet.survivor_id(
            "thread__", ref=lambda name: models.OuterRef(models.OuterRef(name)),
        ))
        members = thread_model._base_manager.redirect_group(survivor).values("pk")
        current_thread = MergeableQuerySet.survivor_id("thread__", ref=models.OuterRef)
        preference, tie = ThreadFollowerQuerySet._preference_order(current_thread)
        return self.model._base_manager.using(self.db).filter(
            ThreadFollowerQuerySet.user_q(user), thread_id__in=members,
        ).alias(_thread_preference=preference).order_by("_thread_preference", tie)

    def get_party_id(self, *, party: Any = None, user: Any = None, user_id: Any = None, create: bool = False) -> Any:
        """Resolve a follower identity; only a subscribing write creates a person."""

        if party is not None:
            if user is not None or user_id is not None:
                raise ValueError("Pass a party or a user, not both.")
            if not isinstance(party, apps.get_model("parties", "Party")) or party.pk is None:
                raise ValueError("A saved party is required to follow a thread.")
            return party.pk
        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return None
        if create:
            with system_context(reason="messaging.follower.person"):
                account = user if user is not None else get_user_model()._base_manager.using(self.db).get(
                    pk=resolved_user_id,
                )
                return apps.get_model("parties", "Party").objects.db_manager(self.db).for_user(account).pk
        parties = apps.get_model("parties", "Party").objects.system_context(
            reason="messaging.follower.person"
        ).using(self.db)
        return parties.person_id_for_user(resolved_user_id)

    def _require_follow_access(self, record: Any, party_id: Any) -> None:
        """A person manages their own follow; a record writer may manage others."""

        if is_sudo():
            return
        actor_id = actor_user_id(current_actor())
        if actor_id is not None and party_id == self.get_party_id(user_id=actor_id):
            return
        if not record.can_moderate():
            raise PermissionDenied("Managing another party's follow requires record write access.")

    def _person_account(self, party_id: Any) -> Any | None:
        """Return the human account for a party, if this is an account follow."""

        parties = apps.get_model("parties", "Party").objects.system_context(
            reason="messaging.follower.account"
        ).using(self.db)
        return parties.account_for_party(party_id)

    def end_unreadable_for_record(self, record: Any, *, role: str = "chatter") -> int:
        """End person-account follows after an access owner's revoke or seat removal."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        people = list(
            apps.get_model("parties", "Person")
            ._base_manager.using(self.db).filter(
                pk__in=self.model._base_manager.using(self.db).filter(
                    thread_id__in=attachment.thread.member_threads().values("pk"),
                ).values("party_id"),
                user__kind="person",
            )
            .select_related("user")
        )
        allowed = attachment.require_threaded_target().thread_reader_ids(
            account for person in people if (account := person.user) is not None
        )
        with system_context(reason="messaging.follower.read_revoked"):
            removed, _ = self.model._base_manager.using(self.db).filter(
                thread_id__in=attachment.thread.member_threads().values("pk"),
                party_id__in=[person.pk for person in people if person.user_id not in allowed],
            ).delete()
        return removed

    def end_unreadable_for_party(self, party: Any) -> int:
        """Recheck a removed seat's account follows, including indirect record reads."""

        with system_context(reason="messaging.follower.seat_revoked"):
            attachment_model = apps.get_model("messaging", "ThreadAttachment")
            attachments = attachment_model._base_manager.filter(
                pk__in=self.model._base_manager.filter(party_id=party.pk).values("attachment_id"),
                role=attachment_model.AttachmentRole.CHATTER,
            ).values_list("pk", "content_type_id", "object_id")
            return self._end_unreadable_attachments(attachments, party_id=party.pk)

    def end_unreadable_for_collections(self, collections: Sequence[models.QuerySet[Any]]) -> int:
        """Recheck record follows across several changed access collections in batches."""

        from angee.messaging.models import ThreadedModelMixin

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        selection = models.Q(pk__in=[])
        for rows in collections:
            if not issubclass(rows.model, ThreadedModelMixin):
                continue
            content_type = ContentType.objects.get_for_model(generic_pointer_model(rows.model))
            selection |= models.Q(
                content_type=content_type,
                object_id__in=rows.order_by().values("pk"),
                role=rows.model.thread_attachment_role,
            )
        with system_context(reason="messaging.follower.access_revoked"):
            attachments = attachment_model._base_manager.filter(selection).values_list(
                "pk",
                "content_type_id",
                "object_id",
            )
            return self._end_unreadable_attachments(attachments)

    def _end_unreadable_attachments(self, attachments: Any, *, party_id: Any | None = None) -> int:
        """Use each model's native account read scope and delete revoked follows together."""

        from angee.messaging.models import ThreadedModelMixin

        selected = [pk for pk, _content_type_id, _object_id in attachments]
        if not selected:
            return 0
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        thread_model = apps.get_model("messaging", "Thread")
        survivor = thread_model(pk=MergeableQuerySet.survivor_id("thread__", ref=models.OuterRef))
        selected_group = thread_model.objects.system_context(
            reason="messaging.follower.redirect_group"
        ).redirect_group(survivor).filter(attachments__pk__in=selected)
        members = list(attachment_model._base_manager.alias(
            _selected_group=models.Exists(selected_group),
        ).filter(
            _selected_group=True,
            role=attachment_model.AttachmentRole.CHATTER,
        ).values_list("pk", "thread_id", "thread__merged_into_id", "content_type_id", "object_id"))
        targets = {thread_id: (content_type_id, object_id)
                   for _pk, thread_id, merged_into_id, content_type_id, object_id in members
                   if merged_into_id is None}
        edges = {pk: targets[merged_into_id or thread_id]
                 for pk, thread_id, merged_into_id, _content_type_id, _object_id in members
                 if (merged_into_id or thread_id) in targets}
        follows = self.model._base_manager.filter(attachment_id__in=edges)
        if party_id is not None:
            follows = follows.filter(party_id=party_id)
        follow_rows = list(follows.values_list("pk", "attachment_id", "party_id"))
        people = (
            apps.get_model("parties", "Person")
            ._base_manager.filter(
                pk__in={party for _, _, party in follow_rows},
                user__kind="person",
            )
            .select_related("user")
        )
        accounts = {person.pk: person.user for person in people}
        types = ContentType.objects.in_bulk({content_type_id for content_type_id, _ in edges.values()})
        grouped: dict[tuple[Any, Any], set[Any]] = {}
        for _, attachment_id, follower_party_id in follow_rows:
            if follower_party_id in accounts:
                content_type_id, object_id = edges[attachment_id]
                grouped.setdefault((content_type_id, follower_party_id), set()).add(object_id)
        allowed: dict[tuple[Any, Any], set[Any]] = {}
        for (content_type_id, follower_party_id), record_ids in grouped.items():
            model = types[content_type_id].model_class()
            if model is None or not issubclass(model, ThreadedModelMixin):
                continue
            if not callable(getattr(model, "has_access", None)) or not model_resource_type(model):
                allowed[(content_type_id, follower_party_id)] = record_ids
                continue
            subject = to_subject_ref(accounts[follower_party_id])
            predicate = backend().queryset_filter(
                model=model,
                subject=subject,
                action=model.thread_read_access,
                using=self.db,
            )
            readable = (
                model._base_manager.using(self.db).filter(predicate, pk__in=record_ids)
                if predicate is not None
                else require_rebac_queryset(model).using(self.db)
                .with_actor(subject)
                .with_action(model.thread_read_access)
                .filter(pk__in=record_ids)
                .scoped()
            )
            allowed[(content_type_id, follower_party_id)] = set(readable.order_by().values_list("pk", flat=True))
        revoked = []
        for pk, attachment_id, follower_party_id in follow_rows:
            content_type_id, record_id = edges[attachment_id]
            readable_ids = allowed.get((content_type_id, follower_party_id))
            if readable_ids is not None and record_id not in readable_ids:
                revoked.append(pk)
        removed, _ = self.model._base_manager.filter(pk__in=revoked).delete()
        return removed

    def subjects_for_record(self, record: Any, *, role: str = "chatter", limit: int = 100) -> list[SubjectRef]:
        """List a bounded panel roster of human account follower subjects."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return []
        people = (
            apps.get_model("parties", "Person")
            ._base_manager.filter(
                pk__in=self.model._base_manager.filter(
                    thread_id__in=attachment.thread.member_threads().values("pk"),
                ).values("party_id"),
                user__kind="person",
            )
            .select_related("user")
            .order_by("pk")[:limit]
        )
        return [to_subject_ref(person.user) for person in people]

    def for_record(self, record: Any, *, role: str = "chatter") -> Any:
        """Return followers for ``record`` and ``role``."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return self.none()
        return self.for_attachment(attachment)

    def is_following(
        self, record: Any, *, party: Any = None, user: Any = None, user_id: Any = None, role: str = "chatter"
    ) -> bool:
        """Return whether ``user`` follows ``record``'s chatter thread."""

        party_id = self.get_party_id(party=party, user=user, user_id=user_id)
        if party_id is None:
            return False
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return False
        return self.model._base_manager.using(self.db).filter(
            thread_id__in=attachment.thread.member_threads().values("pk"), party_id=party_id,
        ).exists()

    def subscribe(
        self,
        record: Any,
        *,
        party: Any = None,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
        notification_policy: str | None = None,
        subtype_keys: tuple[str, ...] | None = None,
        history_before: Any | None = None,
    ) -> Any:
        """Follow a party, reusing its effective subscription across chatter redirects.

        Omitted preferences and an existing read cutoff remain unchanged. A person
        account needs the current record's read permission; managing another party
        needs that record's write permission. Only a new follow seeds a receipt.
        """

        follows = self.db_manager(self._db or record._state.db, hints={"instance": record})
        using = follows.db
        attachments = apps.get_model("messaging", "ThreadAttachment").objects.db_manager(
            using, hints={"instance": record},
        )
        with database_context(using), transaction.atomic(using=using):
            attachment = attachments.for_record(record, role=role)
            target = attachment.target if attachment is not None else record
            if target is None:
                raise ValidationError("A chatter follow requires a surviving record attachment.")
            if party is None:
                resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
                if resolved_user_id is None:
                    raise ValueError("A party or user is required to follow a thread.")
                account = user if user is not None else get_user_model()._base_manager.using(using).get(
                    pk=resolved_user_id,
                )
                if getattr(account, "kind", None) == "person" and not target.thread_reader_allowed(account):
                    raise PermissionDenied("Only a person with record read access may follow.")
                party_id = follows.get_party_id(user=account, create=True)
            else:
                if user is not None or user_id is not None:
                    raise ValueError("Pass a party or a user, not both.")
                party_id = follows.get_party_id(party=party)
                account = follows._person_account(party_id)
                if account is not None and not target.thread_reader_allowed(account):
                    raise PermissionDenied("Only a person with record read access may follow.")
            follows._require_follow_access(target, party_id)
            if attachment is None:
                attachment = attachments.ensure_for_record(record, role=role, title=_record_thread_title(record))
            with system_context(reason="messaging.follower.subscribe"):
                type(attachment.thread).system_queryset(lock=("self",)).using(using).get(pk=attachment.thread_id)
                follower = follows.effective_for_thread(attachment.thread).lock_if_supported().filter(
                    party_id=party_id,
                ).first()
                created = False
                if follower is None:
                    follower, created = self.model._base_manager.using(using).get_or_create(
                        thread_id=attachment.thread_id,
                        party_id=party_id,
                        defaults={
                            "attachment_id": attachment.pk,
                            "notification_policy": self.model.NotificationPolicy.INBOX
                            if notification_policy is None else notification_policy,
                            "subtype_keys": [] if subtype_keys is None else list(subtype_keys),
                            "created_by_id": actor_user_id(current_actor()),
                        },
                    )
                if created:
                    follows._seed_receipt(follower, attachment.thread, history_before=history_before)
                else:
                    updates: dict[str, Any] = {"attachment_id": attachment.pk}
                    if notification_policy is not None:
                        updates["notification_policy"] = notification_policy
                    if subtype_keys is not None:
                        updates["subtype_keys"] = list(subtype_keys)
                    updates = {field: value for field, value in updates.items() if getattr(follower, field) != value}
                    if updates:
                        for field, value in updates.items():
                            setattr(follower, field, value)
                        follower.save(using=using, update_fields=(*updates, "updated_at"))
            return follower

    def _seed_receipt(self, follower: Any, thread: Any, *, history_before: Any | None) -> None:
        """Anchor a fresh follower's receipt at the latest pre-join message."""

        message_model = apps.get_model("messaging", "Message")
        queryset = message_model._base_manager.using(thread._state.db).filter(
            thread_id__in=thread.member_threads().values("pk"), is_trashed=False,
        ).annotate(
            _order_at=MessageQuerySet.chronological_time()
        )
        if history_before is not None:
            queryset = queryset.filter(_MESSAGE_ORDER.before(history_before.chronological_key))
        latest = queryset.order_by("-_order_at", "-pk").first()
        if latest is None:
            return
        follower.last_read_message = latest
        follower.save(using=thread._state.db, update_fields=("last_read_message", "updated_at"))

    def mark_read_up_to(
        self,
        thread: Any,
        *,
        party: Any = None,
        user: Any = None,
        user_id: Any = None,
        message: Any | None = None,
    ) -> int:
        """Advance ``user``'s read receipt on ``thread`` to ``message`` (or the latest).

        The single owner of the receipt write. The advance is guarded on the same
        ``(order_at, pk)`` key the feed displays by, so a stale client acking an old
        message never regresses a receipt that already points past it. Returns 1
        when the receipt moved, 0 when it was already at or past the target (or the
        user does not follow the thread).
        """

        party_id = self.get_party_id(party=party, user=user, user_id=user_id)
        if party_id is None:
            return 0
        message_model = apps.get_model("messaging", "Message")
        target = message
        if target is None:
            target = (
                message_model._base_manager.using(self.db).filter(
                    thread_id__in=thread.member_threads().values("pk"), is_trashed=False,
                )
                .annotate(_order_at=MessageQuerySet.chronological_time())
                .order_by("-_order_at", "-pk")
                .first()
            )
        if target is None:
            return 0
        if message is not None and not thread.member_threads().filter(pk=message.thread_id).exists():
            raise ValueError("Receipt anchor does not belong to this thread.")
        target_key = target.chronological_key
        with transaction.atomic(using=self.db):
            # Lock only the follower row (`of=("self",)`): the receipt FK is
            # nullable, and Postgres refuses FOR UPDATE on the nullable side of
            # the select_related outer join. lock_if_supported keeps the SQLite
            # floor (the repo's greppable row-lock contract).
            follower = (
                self.sudo(reason="messaging.receipt.advance")
                .lock_if_supported()
                .effective_for_thread(thread).filter(party_id=party_id)
                .select_related("last_read_message")
                .first()
            )
            if follower is None:
                return 0
            current = follower.last_read_message
            if current is not None and current.chronological_key >= target_key:
                return 0
            follower.last_read_message = target
            follower.save(using=follower._state.db, update_fields=("last_read_message", "updated_at"))
        return 1

    def unread_messages(self, thread: Any, *, party: Any = None, user: Any = None, user_id: Any = None) -> Any:
        """Return ``user``'s unread messages on ``thread`` — the receipt-anchored scan.

        Everything strictly after the follower's ``last_read_message`` in feed order
        (the whole thread when no receipt yet), narrowed to the subtypes the follower
        subscribes to (:meth:`NotificationPreference.subscribed_subtype_q`); ``none()`` for a
        non-follower. The scan rides the thread keyset index, so its cost is bounded by
        how far behind the receipt is — never by thread size. A composable queryset the
        badge count and a caller ranging record threads reuse; the per-row callers
        (``needaction_for_message``, ``fanout_for_message``) read the same rule through
        its boolean twin ``is_subscribed_to``, so muting can never drift between the
        badge, the needaction markers, and email delivery.
        """

        party_id = self.get_party_id(party=party, user=user, user_id=user_id)
        if party_id is None:
            return apps.get_model("messaging", "Message")._base_manager.none()
        message_model = apps.get_model("messaging", "Message")
        follower = (
            self.sudo(reason="messaging.follower.effective").effective_for_thread(thread).filter(party_id=party_id)
            .select_related("last_read_message")
            .first()
        )
        if follower is None:
            return message_model._base_manager.none()
        queryset = (
            message_model._base_manager.filter(thread_id__in=thread.member_threads().values("pk"), is_trashed=False)
            .annotate(_order_at=MessageQuerySet.chronological_time())
            .filter(follower.notification_preference.subscribed_subtype_q())
        )
        if follower.last_read_message is None:
            return queryset
        anchor = follower.last_read_message.chronological_key
        return queryset.filter(_MESSAGE_ORDER.after(anchor))

    def unread_count_for_record(
        self,
        record: Any,
        *,
        party: Any = None,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Return ``user``'s unread message count on ``record``'s chatter thread."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        return int(self.unread_messages(attachment.thread, party=party, user=user, user_id=user_id).count())

    def mark_read_for_record(
        self,
        record: Any,
        *,
        party: Any = None,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Advance ``user``'s receipt on ``record``'s chatter thread to the latest message."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        return self.mark_read_up_to(attachment.thread, party=party, user=user, user_id=user_id)

    def needaction_for_message(self, message: Any, *, party: Any = None, user: Any = None, user_id: Any = None) -> bool:
        """Return whether ``message`` needs ``user``'s attention: past their read receipt
        AND within their subtype subscription.

        Reads the same muting rule as the unread scan (``NotificationPreference.is_subscribed_to``),
        so a muted or non-subscribed subtype is never needaction even when it sits past the
        receipt — the per-message marker cannot disagree with the badge count.
        """

        if message.thread_id is None or message.is_trashed:
            return False
        party_id = self.get_party_id(party=party, user=user, user_id=user_id)
        if party_id is None:
            return False
        follower = (
            self.sudo(reason="messaging.follower.effective")
            .effective_for_thread(message.thread)
            .filter(party_id=party_id)
            .select_related("last_read_message")
            .first()
        )
        if follower is None:
            return False
        if not follower.notification_preference.is_subscribed_to(message.subtype):
            return False
        if follower.last_read_message is None:
            return True
        return message.chronological_key > follower.last_read_message.chronological_key

    def unsubscribe(
        self,
        record: Any,
        *,
        party: Any = None,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Remove a party's explicit follow without changing access."""

        party_id = self.get_party_id(party=party, user=user, user_id=user_id)
        if party_id is None:
            return 0
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        self._require_follow_access(record, party_id)
        with system_context(reason="messaging.follower.unsubscribe"):
            deleted, _details = self.model._base_manager.using(self.db).filter(
                thread_id__in=attachment.thread.member_threads().values("pk"), party_id=party_id,
            ).delete()
        return deleted


_ThreadNotificationManagerBase = AngeeManager.from_queryset(ThreadNotificationQuerySet)


class ThreadNotificationManager[ThreadNotificationT: ThreadNotification](
    _ThreadNotificationManagerBase[ThreadNotificationT]
):
    """Owns per-item delivery and acknowledgement, independently of follower cursors."""


    @transaction.atomic
    def mark_read(self, notification: Any, actor: Any) -> Any:
        """Acknowledge an actor's inbox item once, without changing any follow.

        Re-resolve the row under ``read_inbox`` so a caller's cached instance or
        broader record/admin access cannot authorize the acknowledgement. Locking
        preserves the first timestamp when concurrent requests acknowledge it.
        """

        if actor is None or getattr(actor, "is_authenticated", True) is False:
            raise PermissionDenied("Authentication required.")
        row = self.with_actor(actor).with_action("read_inbox").lock_if_supported().filter(pk=notification.pk).first()
        if row is None or str(row.user_id) != str(actor.pk):
            raise PermissionDenied("Notification inbox access required.")
        if row.read_at is None:
            row.read_at = timezone.now()
            row.save(update_fields=("read_at", "updated_at"))
        return row

    def for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> Any:
        """Return delivery rows for ``user`` on ``record`` and ``role``."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return self.none()
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return self.none()
        # Notification bookkeeping bypasses per-row REBAC: the record-level gate has
        # already authorised the whole chatter read, so scope the reads with sudo
        # and let the chainable predicates own the filters.
        return (
            self.sudo(reason="messaging.notification.for_record")
            .for_attachment(attachment)
            .filter(
                user_id=resolved_user_id,
            )
            .exclude(message__is_trashed=True)
        )

    def error_count_for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Return the delivery-error count authored by ``user`` on ``record``."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return 0
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        return int(
            self.sudo(reason="messaging.notification.error_count")
            .for_attachment(attachment)
            .filter(created_by_id=resolved_user_id)
            .delivery_errors()
            .count()
        )

    def mark_failed(
        self,
        notification: Any,
        *,
        status: str = "exception",
        failure_type: str = "unknown",
        failure_reason: str = "",
    ) -> Any:
        """Mark one notification as a delivery failure."""

        if status not in ThreadNotificationQuerySet.DELIVERY_ERROR_STATUSES:
            raise ValueError("Delivery failure status must be 'bounce' or 'exception'.")
        cleaned_failure_type = strip_null_bytes(failure_type or "unknown")
        failure_field = cast(DiagnosticTextField, self.model._meta.get_field("failure_reason"))
        cleaned_failure_reason = failure_field.get_prep_value(failure_reason or "")
        now = timezone.now()
        self.model._base_manager.filter(pk=notification.pk).update(
            notification_status=status,
            failure_type=cleaned_failure_type,
            failure_reason=cleaned_failure_reason,
            updated_at=now,
        )
        notification.notification_status = status
        notification.failure_type = cleaned_failure_type
        notification.failure_reason = cleaned_failure_reason
        notification.updated_at = now
        return notification

    def mark_failed_for_message(
        self,
        message: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        status: str = "exception",
        failure_type: str = "unknown",
        failure_reason: str = "",
    ) -> Any:
        """Mark one message notification for ``user`` as failed."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            raise ValueError("A user is required to mark a notification failed.")
        notification = self.model._base_manager.get(message=message, user_id=resolved_user_id)
        return self.mark_failed(
            notification,
            status=status,
            failure_type=failure_type,
            failure_reason=failure_reason,
        )

    def fanout_for_message(
        self,
        message: Any,
        *,
        attachment: Any | None = None,
        created_by_id: Any = None,
        recipient_user_ids: tuple[Any, ...] = (),
    ) -> int:
        """Tell the union of followers, team members and record-named parties once.

        A muted follow excludes its party from the derived audience; direct
        addressing bypasses mute. A matching follow chooses delivery policy,
        otherwise email wins when derived sources disagree. Every told account
        needs record read access and gets one row, including inbox followers.
        No derived audience member is copied into followers.
        Without a threaded record there is no read owner or derived audience;
        only matching explicit followers and directly addressed accounts are told.
        """

        from angee.messaging.models import ThreadedModelMixin

        if message.thread_id is None:
            return 0
        follower_model = apps.get_model("messaging", "ThreadFollower")
        followers = {
            row.party_id: row
            for row in follower_model.objects.db_manager(self.db).effective_for_thread(message.thread)
        }
        subtype = message.subtype
        policy_type = follower_model.NotificationPolicy
        # Identity reads grant no access; the record gates each account below.
        audience: dict[Any, tuple[NotificationPolicy, ThreadFollower | None]] = {}
        follower: ThreadFollower | None
        for party_id, follower in followers.items():
            preference = follower.notification_preference
            if preference.is_subscribed_to(subtype):
                audience[party_id] = (preference.notification_policy, follower)

        target = attachment.target if attachment is not None else message.threaded_record()
        record = target if isinstance(target, ThreadedModelMixin) else None
        if record is not None:
            team = record.thread_team()
            sources = (*team.thread_audience(),) if team is not None else ()
            for member in (*sources, *record.thread_audience_members()):
                party_id = member.party_id
                follower = followers.get(party_id)
                if follower is not None and follower.notification_policy == policy_type.MUTED:
                    continue
                if not member.is_subscribed_to(subtype):
                    continue
                policy = member.notification_policy
                follower_matches = follower is not None and follower.notification_preference.is_subscribed_to(subtype)
                if follower is not None and follower_matches:
                    policy = follower.notification_preference.notification_policy
                # Without a matching explicit follow, prefer email when two derived
                # sources match with different delivery policies.
                previous = audience.get(party_id)
                if previous is None or follower_matches or policy == policy_type.EMAIL:
                    audience[party_id] = (policy, follower)

        people = (
            apps.get_model("parties", "Person")
            ._base_manager.using(self.db).filter(pk__in=audience, user__isnull=False)
            .select_related("user")
        )
        deliveries: dict[Any, tuple[NotificationPolicy, ThreadFollower | None]] = {}
        accounts: dict[Any, Any] = {}
        for person in people:
            accounts[person.user_id] = person.user
            deliveries[person.user_id] = audience[person.pk]

        # Direct addressing bypasses audience preferences, including mute. It still
        # shares the author exclusion and record-read gate with every other source.
        for account in get_user_model()._base_manager.using(self.db).filter(
            pk__in=_normalise_user_ids(recipient_user_ids),
        ):
            accounts[account.pk] = account
            deliveries.setdefault(account.pk, (policy_type.INBOX, None))

        eligible = {
            key: account for key, account in accounts.items() if created_by_id is None or str(key) != str(created_by_id)
        }
        reader_ids = record.thread_reader_ids(eligible.values()) if record is not None else set(eligible)
        existing = {row.user_id: row for row in self.model._base_manager.using(self.db).filter(message=message)}
        new_rows: list[Any] = []
        for user_id, (policy, follower) in deliveries.items():
            if user_id not in reader_ids:
                continue
            values = {
                "thread_id": message.thread_id,
                "attachment_id": attachment.pk
                if attachment is not None
                else (follower.attachment_id if follower is not None else None),
                "follower_id": follower.pk if follower is not None else None,
                "notification_type": self.model.NotificationType(policy),
            }
            existing_row = existing.get(user_id)
            if existing_row is not None:
                for field, value in values.items():
                    setattr(existing_row, field, value)
                existing_row.save(
                    using=existing_row._state.db,
                    update_fields=("thread", "attachment", "follower", "notification_type", "updated_at"),
                )
            else:
                new_rows.append(
                    self.model(
                        message_id=message.pk,
                        user_id=user_id,
                        **values,
                        notification_status=self.model.NotificationStatus.READY,
                        created_by_id=created_by_id,
                    )
                )
        self.model._base_manager.using(self.db).bulk_create(new_rows, ignore_conflicts=True)
        return len(new_rows)


def _normalise_user_ids(user_ids: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return unique user ids while preserving caller order."""

    seen: set[str] = set()
    unique: list[Any] = []
    for user_id in user_ids:
        if user_id is None:
            continue
        key = str(user_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(user_id)
    return tuple(unique)
