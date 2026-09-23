"""Explicit historical sample actions on the canonical Messaging Channel."""

from __future__ import annotations

from datetime import date
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import actor_context, system_context

from angee.base.db import get_write_alias
from angee.base.permissions import require_authorization_database
from angee.integrate.locks import bridge_advisory_lock
from angee.messaging_integrate_imap.backend import (
    ImapChannelBackend,
    ImapSampleImport,
    ImapSamplePreview,
)
from angee.messaging_integrate_imap.parser import EMBEDDED_MESSAGE_MAX_BYTES, expand_embedded_message


class ImapChannelSampling(models.Model):
    """Compose bounded imports without a second channel, cursor or intake ledger."""

    extends = "messaging.Channel"

    class Meta:
        abstract = True

    def _require_paused_imap(self, actor: Any) -> None:
        """Require explicit write access to one paused IMAP channel."""

        self.with_actor(actor)._require_record_access("write")
        if self.lifecycle != self.Lifecycle.PAUSED:
            raise ValidationError("Pause this IMAP channel before changing its mailbox position.")
        if not isinstance(self.backend, ImapChannelBackend):
            raise ValidationError("Mailbox positioning is available for IMAP channels.")

    def prepare_imap_new_mail(self, *, actor: Any, using: str | None = None) -> tuple[int, bool]:
        """Atomically exclude the selected mailboxes' current contents from live sync.

        The transport snapshot happens under the bridge's normal sync lock but
        outside a database transaction. The locked row then revalidates every
        fact used by that snapshot before it commits the complete cursor at once.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(using, operation="IMAP sample and mailbox access")
        channel_model = type(self)
        with actor_context(actor):
            current = channel_model._base_manager.using(using).select_related(
                "credential__external_account", "credential__oauth_client",
            ).get(pk=self.pk)
            current._require_paused_imap(actor)
            original_cursor = current.cursor if isinstance(current.cursor, dict) else {}
            with bridge_advisory_lock(current) as acquired:
                if not acquired:
                    raise ValidationError("The channel is busy. Retry when its current operation finishes.")
                cursor, changed = current.backend.prepare_new_mail_cursor(original_cursor)
                with transaction.atomic(using=using):
                    locked = channel_model._base_manager.using(using).select_for_update().get(pk=self.pk)
                    locked._require_paused_imap(actor)
                    if locked.config != current.config or locked.credential_id != current.credential_id:
                        raise ValidationError("The channel configuration changed. Set the starting point again.")
                    locked_cursor = locked.cursor if isinstance(locked.cursor, dict) else {}
                    if locked_cursor != original_cursor:
                        raise ValidationError("The channel cursor changed. Set the starting point again.")
                    if changed:
                        locked.cursor = cursor
                        locked.save(using=using, update_fields=["cursor", "updated_at"])
            return len(cursor.get("mailboxes", {})), changed

    def preview_imap_sample(
        self,
        *,
        actor: Any,
        mailbox: str,
        since: date | None,
        before: date | None,
        all_dates: bool = False,
        uidvalidity: int | None = None,
        upper_uid: int | None = None,
        before_uid: int | None = None,
        limit: int = 20,
        using: str | None = None,
    ) -> ImapSamplePreview:
        """Read one frozen preview page; leave mailbox flags and the live cursor unchanged."""

        using = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(using, operation="IMAP sample and mailbox access")
        current = type(self)._base_manager.using(using).select_related(
            "credential__external_account", "credential__oauth_client",
        ).get(pk=self.pk)
        current._require_paused_imap(actor)
        return current.backend.preview_sample(
            mailbox=mailbox,
            since=since,
            before=before,
            all_dates=all_dates,
            uidvalidity=uidvalidity,
            upper_uid=upper_uid,
            before_uid=before_uid,
            limit=limit,
        )

    def import_imap_sample(
        self, *, actor: Any, mailbox: str, uidvalidity: int, uids: list[int],
        using: str | None = None,
    ) -> ImapSampleImport:
        """Land selected messages as historical records with native live events suppressed.

        The shared bridge lock excludes normal sync. Network retrieval precedes
        the local transaction; paused state, access and transport configuration
        are rechecked before landing. Messaging owns idempotency and provenance.
        Neither this operation nor the backend writes the regular bridge cursor.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(using, operation="IMAP sample and mailbox access")
        current = type(self)._base_manager.using(using).select_related(
            "credential__external_account", "credential__oauth_client",
        ).get(pk=self.pk)
        current._require_paused_imap(actor)
        with bridge_advisory_lock(current) as acquired:
            if not acquired:
                raise ValidationError("The channel is busy. Retry when its current operation finishes.")
            parsed, imported_uids, flags_unchanged = current.backend.fetch_sample(
                mailbox=mailbox, uidvalidity=uidvalidity, uids=uids,
            )
            with transaction.atomic(using=using):
                locked = type(self)._base_manager.using(using).select_for_update().get(pk=self.pk)
                locked._require_paused_imap(actor)
                if locked.config != current.config or locked.credential_id != current.credential_id:
                    raise ValidationError("The channel configuration changed. Preview the sample again.")
                with system_context(reason="messaging_integrate_imap.historical_sample"):
                    messages = apps.get_model("messaging", "Message").objects.db_manager(using).ingest(
                        parsed, channel=locked, historical=True,
                    )
            return ImapSampleImport(
                message_ids=[str(message.sqid) for message in messages],
                requested_uids=sorted(uids), imported_uids=imported_uids,
                missing_uids=sorted(set(uids) - set(imported_uids)), flags_unchanged=flags_unchanged,
            )

    def expand_retained_imap_part(self, part: Any, *, actor: Any, using: str | None = None) -> tuple[Any, ...]:
        """Append bounded evidence below this Channel's retained RFC 822 Part.

        Existing Message, Part and File identities remain unchanged. The IMAP
        adapter owns RFC 822 decoding; Messaging owns the locked, idempotent
        descendant write.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(using, operation="IMAP sample and mailbox access")
        with actor_context(actor):
            current = type(self)._base_manager.using(using).get(pk=self.pk)
            current._require_record_access("write")
            part_model = apps.get_model("messaging", "Part")
            if not isinstance(part, part_model) or part.pk is None:
                raise ValidationError("Embedded expansion requires a retained Message Part.")
            retained = part_model._base_manager.using(using).select_related("message", "file").get(pk=part.pk)
            if (retained.message.channel_id != current.pk or str(retained.type).lower() != "message/rfc822"
                    or retained.file_id is None):
                raise ValidationError("Select an RFC 822 Part retained by this IMAP Channel.")
            retained.file._require_record_access("read")
            with retained.file.open_stream() as stream:
                raw = stream.read(EMBEDDED_MESSAGE_MAX_BYTES + 1)
            child = expand_embedded_message(raw)
            if child is None:
                return ()
            return apps.get_model("messaging", "Message").objects.db_manager(using).expand_retained_part(
                retained, (child,),
            )
