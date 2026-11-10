"""Explicit historical sample actions on the canonical Messaging Channel."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django_compose.models import Composable
from rebac import actor_context, system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.streams import open_stream, reset_stream
from angee.messaging_integrate_imap.backend import (
    NEW_MAIL_DELIVERY_MODE,
    ImapChannelBackend,
    ImapDeliveryBoundary,
    ImapSampleImport,
    ImapSamplePreview,
    ImapSamplePreviewRequest,
)


class ImapChannelSampling(Composable, extends="messaging.Channel"):
    """Compose bounded imports without a second channel, cursor or intake ledger."""


    class Meta:
        abstract = True

    def _require_paused_imap(self, actor: Any) -> ImapChannelBackend:
        """Require explicit write access to one paused IMAP channel."""

        self.require_access("write", actor)
        if self.lifecycle != self.Lifecycle.PAUSED:
            raise ValidationError("Pause this IMAP channel before changing its mailbox position.")
        backend = self.backend
        if not isinstance(backend, ImapChannelBackend):
            raise ValidationError("Mailbox positioning is available for IMAP channels.")
        return backend

    def imap_delivery_boundary(self) -> dict[str, Any]:
        """Return this channel's retained future-only delivery intent, or ``{}`` when it syncs history."""

        return self.config if self.config.get("delivery_mode") == NEW_MAIL_DELIVERY_MODE else {}

    def select_imap_new_mail_boundary(
        self, snapshot: ImapDeliveryBoundary, backend: ImapChannelBackend,
    ) -> ImapDeliveryBoundary:
        """Keep committed same-epoch intent instead of moving the boundary past newer arrivals.

        A repeated starting-point action against the same account, mailbox
        selection and epochs returns the retained cursors unchanged: losing the
        first action's response can never make a retry skip mail that arrived
        after it committed. Each snapshot mailbox opens against its declaration
        without listing the server again.
        """

        streams = tuple(
            open_stream(self, "messages", name, backend, definition=backend.mailbox_stream(name))
            for name in sorted(snapshot.cursors)
        )
        retained = {stream.partition: stream.cursor for stream in streams}
        intent = self.imap_delivery_boundary()
        same_epochs = (
            intent.get("source_identity") == snapshot.source_identity
            and intent.get("mailbox_selection") == sorted(snapshot.cursors)
            and all(
                retained[name].get("uidvalidity") == value["uidvalidity"] for name, value in snapshot.cursors.items()
            )
        )
        return ImapDeliveryBoundary(
            snapshot.source_identity, retained if same_epochs else snapshot.cursors, streams, not same_epochs,
        )

    def install_imap_new_mail_boundary(self, boundary: ImapDeliveryBoundary) -> None:
        """Install each selected mailbox boundary through the stream and epoch owners."""

        for stream in boundary.streams:
            cursor = boundary.cursors[stream.partition]
            if stream.cursor != cursor:
                reset_stream(stream, cursor=cursor)

    def update_imap_credential(self, *, username: str, password: str) -> None:
        """Re-enter this IMAP channel's Basic-auth login in place.

        The channel keeps its row, config, cursor and credential identity; only
        the encrypted material is replaced, validated by the credential's own
        kind handler. A channel whose credential was detached has nothing to
        update and must be connected again.
        """

        credential = self.credential
        if credential is None:
            raise ValidationError("This channel has no credential; connect it again.")
        if credential.kind != CredentialKind.BASIC_AUTH:
            raise ValidationError("Only a username/password IMAP login can be re-entered here.")
        credential.replace_material({"username": str(username).strip(), "password": str(password)})

    def prepare_imap_new_mail(self, *, actor: Any) -> tuple[int, bool]:
        """Atomically exclude the selected mailboxes' current contents from live sync.

        The transport snapshot happens under the bridge's normal sync lock but
        outside a database transaction. The locked row revalidates configuration
        and credential identity, then selects its retained intent and installs
        all boundaries atomically.
        """

        channel_model = type(self)
        with actor_context(actor):
            current = channel_model._base_manager.select_related(
                "credential__external_account",
                "credential__oauth_client",
            ).get(pk=self.pk)
            backend = current._require_paused_imap(actor)
            with current.sync_lock() as acquired:
                if not acquired:
                    raise ValidationError("The channel is busy. Retry when its current operation finishes.")
                snapshot = backend.prepare_new_mail_boundary()
                with transaction.atomic():
                    locked = (
                        channel_model.objects.sudo(
                            reason="messaging.imap.new_mail.lock",
                        )
                        .lock_if_supported(of=())
                        .get(pk=self.pk)
                    )
                    locked_backend = locked._require_paused_imap(actor)
                    if locked.config != current.config or locked.credential_id != current.credential_id:
                        raise ValidationError("The channel configuration changed. Set the starting point again.")
                    boundary = locked.select_imap_new_mail_boundary(snapshot, locked_backend)
                    locked.config = {
                        **locked.config,
                        "delivery_mode": NEW_MAIL_DELIVERY_MODE,
                        "source_identity": boundary.source_identity,
                        "mailbox_selection": sorted(boundary.cursors),
                    }
                    locked.save(update_fields=["config", "updated_at"])
                    locked.install_imap_new_mail_boundary(boundary)
            return len(boundary.cursors), boundary.changed

    def preview_imap_sample(
        self,
        request: ImapSamplePreviewRequest,
        *,
        actor: Any,
    ) -> ImapSamplePreview:
        """Read one frozen preview page; leave mailbox flags and the live cursor unchanged."""

        current = (
            type(self)
            ._base_manager.select_related(
                "credential__external_account",
                "credential__oauth_client",
            )
            .get(pk=self.pk)
        )
        backend = current._require_paused_imap(actor)
        return backend.preview_sample(request)

    def import_imap_sample(
        self,
        *,
        actor: Any,
        mailbox: str,
        uidvalidity: int,
        uids: list[int],
    ) -> ImapSampleImport:
        """Land selected messages as historical records with native live events suppressed.

        The shared bridge lock excludes normal sync. Network retrieval precedes
        the local transaction; paused state, access and transport configuration
        are rechecked before landing. Messaging owns idempotency and provenance.
        Neither this operation nor the backend writes the regular bridge cursor.
        """

        current = (
            type(self)
            ._base_manager.select_related(
                "credential__external_account",
                "credential__oauth_client",
            )
            .get(pk=self.pk)
        )
        backend = current._require_paused_imap(actor)
        with current.sync_lock() as acquired:
            if not acquired:
                raise ValidationError("The channel is busy. Retry when its current operation finishes.")
            parsed, imported_uids, flags_unchanged = backend.fetch_sample(
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uids=uids,
            )
            with transaction.atomic():
                locked = (
                    type(self)
                    .objects.sudo(
                        reason="messaging.imap.sample.lock",
                    )
                    .lock_if_supported(of=())
                    .get(pk=self.pk)
                )
                locked._require_paused_imap(actor)
                if locked.config != current.config or locked.credential_id != current.credential_id:
                    raise ValidationError("The channel configuration changed. Preview the sample again.")
                with system_context(reason="messaging_integrate_imap.historical_sample"):
                    messages = apps.get_model("messaging", "Message").objects.ingest(
                        parsed,
                        channel=locked,
                        historical=True,
                    )
            return ImapSampleImport(
                message_ids=[str(message.sqid) for message in messages],
                requested_uids=sorted(uids),
                imported_uids=imported_uids,
                missing_uids=sorted(set(uids) - set(imported_uids)),
                flags_unchanged=flags_unchanged,
            )
