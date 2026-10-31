"""Channels managers for Messaging; public imports remain on the package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.db import models, router, transaction
from django.db.models.deletion import ProtectedError
from rebac import (
    PermissionDenied,
    current_actor,
    system_context,
)

from angee.base import locks as model_locks
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import session_store_path
from angee.integrate.models import IntegrationLifecycle, IntegrationManager
from angee.messaging.backends import PairingActionError
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
        Channel,
    )



_ChannelManagerBase = IntegrationManager.from_queryset(ChannelQuerySet)


class ChannelManager[ChannelT: Channel](_ChannelManagerBase[ChannelT]):
    """Channel factory + delete owner, bound as ``Channel.objects``.

    A Channel is a multi-table-inheritance child of the concrete ``Integration``, so
    its abstract source declares this manager for Django to inherit. It extends
    ``IntegrationManager`` and adds the
    channel-specific verbs: ``create_disconnected`` (vendor connect services) and the
    purge owner (:meth:`purge`/:meth:`inventory`) that deletes everything a channel
    ingested and forecasts the same scope for the delete-confirmation preview — one
    owner for both the destructive path and its forecast, so the preview cannot drift.
    """

    def create_disconnected(
        self,
        user: Any,
        *,
        name: str,
        backend_class: str,
        **extra: Any,
    ) -> Any:
        """Create one vendor-backed channel in its initial disconnected state."""

        name = self.normalized_name(name)
        vendor_model = apps.get_model("integrate", "Vendor")
        return self.create(
            vendor_id=vendor_model.objects.seeded(backend_class).pk,
            owner_id=user.pk,
            backend_class=backend_class,
            display_name=name,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            created_by_id=user.pk,
            **extra,
        )

    def resume_or_create_pairing(self, user: Any, *, name: str, backend_class: str) -> Any:
        """Restart the user's newest unfinished pairing channel, or create one.

        A reused channel keeps its display name; ``name`` applies only to creation.
        Reuse resets an existing session store: a released claim can follow a
        duplicate-account rejection while the store still holds that linked
        account. Without a store there is nothing to wipe, so the channel simply
        resumes. A process-local lock backend cannot prove the reset safe, so there
        a channel with a store is left alone and a new one is created. Reset errors
        propagate without creating another channel, so an unproven shutdown leaves
        the store intact and the caller can retry shortly.
        """

        # Reset crosses the process/session-store owners; import after models populate.
        from angee.messaging.connect import reset_channel_pairing

        channel_model = self.model
        field = channel_model.live_implementation_field()
        if field is None:
            raise PairingActionError("This action requires a live channel.")
        impl_class = field.resolve_class(backend_class)
        if not issubclass(impl_class, LiveBridgeImpl):
            raise PairingActionError("This action requires a live channel.")
        actor = current_actor() or user
        using = self._db or router.db_for_write(self.model, instance=user)
        with system_context(reason="messaging.resume_or_create_channel"), transaction.atomic(using=using):
            type(user)._base_manager.using(using).select_for_update().get(pk=user.pk)
            channel = self.db_manager(using).unfinished_pairing(user, backend_class).first()
            if (
                channel is not None and session_store_path(channel).exists()
                and not model_locks.task_locks_are_cross_process()
            ):
                channel = None
            reused = channel is not None
            if channel is None:
                channel = self.db_manager(using).create_disconnected(user, name=name, backend_class=backend_class)
        if reused and session_store_path(channel).exists():
            reset_channel_pairing(channel)
        else:
            channel.resume_pairing()
        return channel.with_actor(actor)


    @staticmethod
    def normalized_name(name: str) -> str:
        """Normalize a channel display name once for every provider factory."""
        value = str(name or "").strip()
        if not value:
            raise ValueError("A channel name is required.")
        return value

    def named_import_channel(self, user: Any, *, name: str, backend_class: str) -> Any:
        """Reuse the owner's named import target, serializing creation on that owner.

        Ordinary channels may share names; this named factory alone promises
        reuse. PostgreSQL's owner-row lock serializes concurrent first creation.
        """
        name = self.normalized_name(name)
        using = self._db or router.db_for_write(self.model, instance=user)
        with system_context(reason="messaging.channel.named_import"), transaction.atomic(using=using):
            type(user)._base_manager.using(using).select_for_update().get(pk=user.pk)
            existing = self.model._base_manager.using(using).filter(
                owner_id=user.pk, backend_class=backend_class, display_name=name,
            ).order_by("pk").first()
            if existing is not None:
                return existing
            return self.db_manager(using).create_disconnected(user, name=name, backend_class=backend_class)

    def inventory(self, channel: Any) -> dict[type[models.Model], int]:
        """Return ``{model: count}`` for every row a purge of ``channel`` would delete.

        Threads and messages are counted through their :meth:`ThreadQuerySet.for_channel` /
        :meth:`MessageQuerySet.for_channel` owner; the ``CASCADE`` children through
        :func:`_channel_cascade_children` — counts only, no materialization, so the preview
        stays a fixed number of queries on a 90k-message channel. Excludes the channel row
        and its ``Integration`` MTI parent: :meth:`DeletePreview.from_counts` derives those
        from ``target``. Runs elevated so the totals are the true purge scope regardless of
        the caller's REBAC row visibility.
        """

        thread_model = apps.get_model("messaging", "Thread")
        message_model = apps.get_model("messaging", "Message")
        with system_context(reason="messaging.channel.purge_inventory"):
            counts: dict[type[models.Model], int] = {
                thread_model: int(thread_model.objects.for_channel(channel).count()),
                message_model: int(message_model.objects.for_channel(channel).count()),
            }
            for model, predicate in _channel_cascade_children(channel):
                counts[model] = int(model._base_manager.filter(predicate).count())
        return {model: count for model, count in counts.items() if count}

    def purge(self, channel: Any) -> None:
        """Preflight ``delete``, tear down the ingested subtree, then delete the channel row.

        The channel's own ``delete`` permission is checked under the caller's actor first
        (the explicit-preflight shape :class:`~angee.messaging.models.ThreadedModelMixin`
        uses), then :meth:`ThreadManager.teardown_for_channel` removes the private
        thread/message subtree and the channel + MTI parent row is deleted under
        ``system_context`` — all in one transaction, so a denied or failing row delete rolls
        the teardown back. Purging first means the channel delete's collector never has to
        ``SET_NULL`` the rows it just removed. A ``ProtectedError``/``RestrictedError`` from
        a consumer addon that ``PROTECT``\\s a reverse FK to ``integrate.Integration``
        propagates (the transaction rolls back); the authored preview mutation catches it
        and surfaces a blocked preview rather than a raw 500. The deleted instance keeps its
        pk so the Hasura delete shape can return it.
        """

        # Defense-in-depth: the channel's read/write/delete scopes are today all the same
        # owner+admin arm, so a caller that resolved the channel to write it already passes
        # `delete`; this preflight denies at resolve regardless. A divergent-zed preflight
        # test (delete narrower than write) is only meaningful once the deferred thread/
        # message `delete` arm (see teardown FOLLOW-UP) splits the scopes.
        if channel.declared_concrete_model() is not self.model:
            raise ValueError("A channel child must be deleted through its own capability.")
        if not channel.has_access("delete"):
            raise PermissionDenied(f"Denied: cannot delete {channel._meta.label}")
        if blockers := channel.purge_blockers():
            raise ProtectedError(
                "Retained source identity evidence protects this channel. Disconnect it to stop sync.", blockers,
            )
        thread_model = apps.get_model("messaging", "Thread")
        deleted_pk = channel.pk
        with transaction.atomic():
            thread_model.objects.teardown_for_channel(channel)
            with system_context(reason="messaging.channel.delete"):
                channel.delete()
        channel.pk = deleted_pk


def _channel_cascade_children(channel: Any) -> tuple[tuple[Any, models.Q], ...]:
    """Return each ``(model, predicate)`` a channel purge deletes below its threads/messages.

    A fixed, bounded inventory of the real top-level ``CASCADE`` children of a channel's
    messages and threads — the delete removes exactly these (plus the threads and messages
    counted through their :meth:`for_channel` owner). Each predicate reaches the channel
    through the child's own delete root, so a plain ``.count()`` never materializes a row.
    The ``OR`` predicates join two independent to-one FKs (a participant/notification FKs a
    message *or* a thread; an edge FKs a source *or* destination message), so each matching
    row still appears once — no fan-out to dedupe. ``ThreadAttachment`` is absent by
    construction: an attachment makes a thread record chatter, never channel-scoped.
    """

    by_message = models.Q(message__channel=channel)
    by_thread = models.Q(thread__channel=channel)
    return (
        (apps.get_model("messaging", "Part"), by_message),
        (apps.get_model("messaging", "Reaction"), by_message),
        (apps.get_model("messaging", "MessageStar"), by_message),
        (
            apps.get_model("messaging", "MessageEdge"),
            models.Q(src__channel=channel) | models.Q(dst__channel=channel),
        ),
        (apps.get_model("messaging", "Participant"), by_message | by_thread),
        (apps.get_model("messaging", "ThreadNotification"), by_message | by_thread),
        (apps.get_model("messaging", "ThreadFollower"), by_thread),
        (apps.get_model("messaging", "ThreadAttachment"), by_thread),
    )
