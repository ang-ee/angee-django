"""Source models for the IMAP channel backend.

The IMAP addon owns durable wire-work state: sync runs, UID-scoped message fetch
rows, and MIME section-scoped attachment fetch rows. The neutral message graph is
still owned by ``angee.messaging``; these rows only coordinate transport fan-out
and idempotent retry.
"""

from __future__ import annotations

from typing import Any

from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel


def _wire_token(value: object) -> str:
    """Return an IMAP token as stable text for JSON persistence."""

    return value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)


class ImapSyncRunManager(AngeeManager):
    """Owns creation of one durable IMAP fan-out run."""

    def start_for_channel(self, channel: Any) -> Any:
        """Create the running ledger row for a channel sync."""

        with system_context(reason="messaging_integrate_imap.sync_run.start"):
            return self.create(channel=channel, status=self.model.RunStatus.RUNNING)


class ImapMessageWorkManager(AngeeManager):
    """Owns UID-scoped message work rows."""

    def upsert_discovered(
        self,
        run: Any,
        *,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        size: int,
        flags: tuple[object, ...],
        internal_date: Any,
    ) -> Any:
        """Return the durable work row for one discovered UID identity.

        Re-discovery refreshes wire metadata and retries unfinished/error rows, but
        does not turn completed rows back into pending work.
        """

        flag_values = [_wire_token(flag) for flag in flags]
        identity = {
            "channel": run.channel,
            "mailbox": mailbox,
            "uidvalidity": uidvalidity,
            "uid": uid,
        }
        with system_context(reason="messaging_integrate_imap.message_work.upsert"), transaction.atomic():
            work, created = self.select_for_update().get_or_create(
                **identity,
                defaults={
                    "run": run,
                    "size_bytes": size,
                    "flags": flag_values,
                    "internal_date": internal_date,
                },
            )
            if created:
                return work
            work.run = run
            work.size_bytes = size
            work.flags = flag_values
            work.internal_date = internal_date
            work.discovered_at = timezone.now()
            update_fields = ["run", "size_bytes", "flags", "internal_date", "discovered_at", "updated_at"]
            if work.status != self.model.WorkStatus.DONE:
                work.status = self.model.WorkStatus.PENDING
                work.claimed_at = None
                work.completed_at = None
                work.error = ""
                update_fields.extend(["status", "claimed_at", "completed_at", "error"])
            work.save(update_fields=update_fields)
            return work


class ImapAttachmentWorkManager(AngeeManager):
    """Owns section-scoped attachment fetch rows."""

    def claim_ready(self, *, limit: int) -> models.QuerySet[Any]:
        """Return pending attachment rows in deterministic claim order."""

        if limit <= 0:
            return self.none()
        return (
            self.system_context(reason="messaging_integrate_imap.attachment_work.claim")
            .filter(status=self.model.WorkStatus.PENDING)
            .order_by("pk")
            .lock_if_supported()[:limit]
        )


class ImapSyncRun(SqidMixin, AuditMixin, AngeeModel):
    """One durable fan-out run for an IMAP channel sync."""

    runtime = True
    sqid_prefix = "isr_"

    class RunStatus(models.TextChoices):
        """Lifecycle states for an IMAP sync run."""

        RUNNING = "running", "Running"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    channel = models.ForeignKey(
        "messaging.Channel",
        on_delete=models.CASCADE,
        related_name="imap_sync_runs",
    )
    status = StateField(choices_enum=RunStatus, default=RunStatus.RUNNING)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    discovered_messages = models.PositiveIntegerField(default=0)
    fetched_messages = models.PositiveIntegerField(default=0)
    fetched_attachments = models.PositiveIntegerField(default=0)
    failed_messages = models.PositiveIntegerField(default=0)
    failed_attachments = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")

    objects = ImapSyncRunManager()

    class Meta:
        """Django model options for IMAP sync runs."""

        abstract = True
        ordering = ("-started_at", "sqid")

    def __str__(self) -> str:
        """Return the run label used in Django displays."""

        return f"{self.channel_id} {self.status} {self.started_at:%Y-%m-%d %H:%M:%S}"


class ImapMessageWork(SqidMixin, AuditMixin, AngeeModel):
    """One UID-scoped IMAP message fetch item."""

    runtime = True
    sqid_prefix = "imw_"

    class WorkStatus(models.TextChoices):
        """Lifecycle states for IMAP message and attachment work."""

        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    run = models.ForeignKey(
        "messaging_integrate_imap.ImapSyncRun",
        on_delete=models.CASCADE,
        related_name="message_work",
    )
    channel = models.ForeignKey(
        "messaging.Channel",
        on_delete=models.CASCADE,
        related_name="+",
    )
    message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    mailbox = models.CharField(max_length=512)
    uidvalidity = models.PositiveBigIntegerField()
    uid = models.PositiveBigIntegerField()
    size_bytes = models.PositiveBigIntegerField(default=0)
    flags = models.JSONField(default=list, blank=True)
    internal_date = models.DateTimeField(null=True, blank=True)
    status = StateField(choices_enum=WorkStatus, default=WorkStatus.PENDING)
    discovered_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    objects = ImapMessageWorkManager()

    class Meta:
        """Django model options for IMAP message work."""

        abstract = True
        ordering = ("channel", "mailbox", "uidvalidity", "uid")
        constraints = (
            models.UniqueConstraint(
                fields=("channel", "mailbox", "uidvalidity", "uid"),
                name="uq_%(app_label)s_%(class)s_uid",
            ),
        )
        indexes = (
            models.Index(fields=("channel", "mailbox", "uidvalidity", "uid")),
            models.Index(fields=("status", "id")),
        )

    def __str__(self) -> str:
        """Return the IMAP UID identity."""

        return f"{self.mailbox}/{self.uidvalidity}/{self.uid}"


class ImapAttachmentWork(SqidMixin, AuditMixin, AngeeModel):
    """One MIME body-section attachment fetch item for an IMAP message work row."""

    runtime = True
    sqid_prefix = "iaw_"
    WorkStatus = ImapMessageWork.WorkStatus

    message_work = models.ForeignKey(
        "messaging_integrate_imap.ImapMessageWork",
        on_delete=models.CASCADE,
        related_name="attachment_work",
    )
    part = models.ForeignKey(
        "messaging.Part",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    section = models.CharField(max_length=128)
    file = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = StateField(choices_enum=WorkStatus, default=WorkStatus.PENDING)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    objects = ImapAttachmentWorkManager()

    class Meta:
        """Django model options for IMAP attachment work."""

        abstract = True
        ordering = ("message_work", "section", "sqid")
        constraints = (
            models.UniqueConstraint(
                fields=("message_work", "section"),
                name="uq_%(app_label)s_%(class)s_section",
            ),
        )
        indexes = (models.Index(fields=("status", "id")),)

    def __str__(self) -> str:
        """Return the MIME section identity."""

        return f"{self.message_work_id}:{self.section}"
