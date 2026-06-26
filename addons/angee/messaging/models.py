"""Source models for the messaging addon.

Messaging is threads and messages built on the parties contacts foundation: a
message's sender and participants are :class:`~angee.parties.models.Handle` rows,
so the dependency points one way (messaging → parties). A :class:`Channel` is an
``integrate.Integration`` child (a bridge) that ingests messages from an external
source; the email/social mapping lands in ``messaging_integrate_*`` backends.

The shapes mirror JMAP/Gmail/RFC-5322: a :class:`Thread` aggregates :class:`Message`
rows; a message's body is a recursive :class:`Part` tree whose text nodes reference
a content-addressed :class:`Fragment` (dedup + quotation + signature isolation) and
whose byte nodes reference a ``storage.File``; cross-message relations (quote/reply/
mention) live on :class:`MessageEdge`. Ingestion idempotency rests on
``(platform, external_id)`` unique constraints; the write path lives on the
managers.
"""

from __future__ import annotations

from typing import cast

from django.apps import apps
from django.db import models
from rebac.managers import RebacManager

from angee.base.fields import ImplClassField, SqidField, StateField
from angee.base.mixins import AuditMixin, HistoryMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.integrate.models import Bridge
from angee.messaging.backends import ChannelBackend
from angee.messaging.managers import (
    FragmentManager,
    MessageEdgeManager,
    MessageManager,
    ThreadManager,
)
from angee.parties.models import Handle


class Channel(Bridge):
    """A connected message source that ingests threads/messages from email or social.

    An ``integrate.Integration`` child (credential / owner / status from the
    connection substrate) and a ``Bridge`` (the scheduler + ``syncIntegration`` drive
    it through ``run_sync``). ``backend_class`` selects the protocol — ``imap``
    (contributed by ``messaging_integrate_imap``), later ``youtube``/``facebook`` —
    and ``config`` carries source settings. ``sync()`` fetches + parses, then maps
    each message onto the messaging managers.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Channel"

    backend_class = ImplClassField(
        base_class=ChannelBackend,
        registry_setting="ANGEE_CHANNEL_BACKEND_CLASSES",
        default="manual",
    )
    """Registry key for the channel backend bound to this channel."""

    objects = RebacManager()

    class Meta:
        """Django model options for the channel child model."""

        abstract = True
        rebac_resource_type = "messaging/channel"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> ChannelBackend:
        """Return this channel's selected backend, bound to this row."""

        field = cast(ImplClassField, type(self)._meta.get_field("backend_class"))
        backend_class = cast("type[ChannelBackend]", field.resolve_class(self.backend_class))
        return backend_class(self)

    def sync(self) -> int:
        """Fetch new messages and ingest them (the Bridge child-sync contract)."""

        message_model = apps.get_model("messaging", "Message")
        return message_model.objects.ingest(self.backend.fetch_messages(), channel=self)


class Thread(SqidMixin, AuditMixin, AngeeModel):
    """An aggregation of related messages — an email conversation or a social post.

    Two orthogonal axes: ``modality`` (the *shape* — email thread / direct / group /
    public post) and ``visibility`` (*who can see it*). For a public thread the row
    *is* the subject post (``channel``/``external_id``/``subject``/``body``/
    ``subject_url``). ``message_count``/``last_message_at`` are denormalised and
    maintained with ``F()`` deltas by the ingest write path.
    """

    runtime = True

    class Modality(models.TextChoices):
        """The structural shape of a thread."""

        EMAIL_THREAD = "email_thread", "Email thread"
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        PUBLIC_THREAD = "public_thread", "Public thread"

    class Visibility(models.TextChoices):
        """Who can see a thread."""

        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"
        RESTRICTED = "restricted", "Restricted"

    sqid = SqidField(real_field_name="id", prefix="thr_", min_length=8)
    channel = models.ForeignKey(
        "integrate.Integration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messaging_threads",
    )
    # parent/body/subject_url/tags are social-milestone scope (a public thread row
    # *is* its subject post): the foundation for public social, with no producer in
    # this email slice yet.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    platform = StateField(choices_enum=Handle.Platform, default=Handle.Platform.EMAIL)
    modality = StateField(choices_enum=Modality, default=Modality.EMAIL_THREAD)
    visibility = StateField(choices_enum=Visibility, default=Visibility.PRIVATE)
    external_id = models.CharField(max_length=512, blank=True, default="")
    subject = models.CharField(max_length=512, blank=True, default="")
    subject_normalized = models.CharField(max_length=512, blank=True, default="", db_index=True)
    body = models.TextField(blank=True, default="")
    subject_url = models.URLField(max_length=1024, blank=True, default="")
    tags = models.JSONField(blank=True, default=list)
    message_count = models.PositiveIntegerField(default=0, db_index=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadManager()

    class Meta:
        """Django model options for the thread source model."""

        abstract = True
        ordering = ("-last_message_at", "sqid")
        rebac_resource_type = "messaging/thread"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("platform", "external_id"),
                condition=~models.Q(external_id=""),
                name="uq_thread_platform_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return the thread subject for Django displays."""

        return self.subject or f"thread:{self.public_id}"


class Message(SqidMixin, AuditMixin, AngeeModel, HistoryMixin):
    """One message — the unit of a thread. The root post is itself a Message.

    Dedup key is ``(platform, external_id)`` (NOT thread-scoped — the same comment
    can surface under two threads). ``parent`` is the single-parent reply pointer
    (In-Reply-To); richer cross-message relations live on :class:`MessageEdge`. The
    body is the :class:`Part` tree; raw envelope recipients are kept in ``metadata``
    as the lossless source behind :class:`Participant`.
    """

    runtime = True

    class Direction(models.TextChoices):
        """Whether a message came in, went out, or is internal."""

        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        INTERNAL = "internal", "Internal"

    class MessageStatus(models.TextChoices):
        """Lifecycle + public moderation state of a message."""

        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        SYNCED = "synced", "Synced"
        EDITED = "edited", "Edited"
        HIDDEN = "hidden", "Hidden"
        REMOVED = "removed", "Removed"
        FAILED = "failed", "Failed"

    sqid = SqidField(real_field_name="id", prefix="msg_", min_length=8)
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    channel = models.ForeignKey(
        "integrate.Integration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messaging_messages",
    )
    sender = models.ForeignKey(
        "parties.Handle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_messages",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    platform = StateField(choices_enum=Handle.Platform, default=Handle.Platform.EMAIL)
    direction = StateField(choices_enum=Direction, default=Direction.INBOUND)
    status = StateField(choices_enum=MessageStatus, default=MessageStatus.SYNCED)
    external_id = models.CharField(max_length=512, blank=True, default="")
    # Social-milestone scope (the root post of a public thread); no producer yet.
    is_original_post = models.BooleanField(default=False)
    subject = models.CharField(max_length=512, blank=True, default="")
    preview = models.CharField(max_length=512, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = MessageManager()

    class Meta:
        """Django model options for the message source model."""

        abstract = True
        ordering = ("-sent_at", "sqid")
        rebac_resource_type = "messaging/message"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("platform", "external_id"),
                condition=~models.Q(external_id=""),
                name="uq_message_platform_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return a readable message label for Django displays."""

        return self.subject or self.preview or f"message:{self.public_id}"


class Fragment(SqidMixin, AuditMixin, AngeeModel):
    """A content-addressed text node shared across messages.

    Email threads re-quote the same paragraphs in every reply; a hashed shared row
    dedups that text, makes the quotation graph a cheap FK-join (two messages
    quote-link iff their parts share a Fragment), and isolates signatures (one
    repeated signature → one Fragment, excluded from search/quotation). ``kind`` is
    the secondary skip axis in the quotation builder; :attr:`Part.role` is primary.

    Because the row is content-addressed and shared — two owners quoting the same
    paragraph dedup to one row — it carries no REBAC type: a per-owner ``read`` on a
    shared row would hide the text from every owner but the first. Visibility is
    scoped instead by the owning :class:`Part`/:class:`Message` (each REBAC-gated);
    the row is reached only through a readable Part and is never enumerable on its
    own, mirroring storage's unscoped ``MimeType`` catalogue.
    """

    runtime = True

    class FragmentKind(models.TextChoices):
        """What a fragment of text is."""

        PARAGRAPH = "paragraph", "Paragraph"
        QUOTE = "quote", "Quote"
        SIGNATURE = "signature", "Signature"
        CODE = "code", "Code"
        HEADER = "header", "Header"

    sqid = SqidField(real_field_name="id", prefix="frg_", min_length=8)
    text = models.TextField()
    hash = models.CharField(max_length=64, unique=True)
    kind = StateField(choices_enum=FragmentKind, default=FragmentKind.PARAGRAPH)

    objects = FragmentManager()

    class Meta:
        """Django model options for the fragment source model."""

        abstract = True
        # No rebac_resource_type: a content-addressed shared row is unscoped
        # substrate, gated through the owning Part/Message (see the class docstring).

    def __str__(self) -> str:
        """Return a truncated preview for Django displays."""

        return (self.text[:60] + "…") if len(self.text) > 60 else self.text


class Part(SqidMixin, AuditMixin, AngeeModel):
    """One recursive body node of a message (the MIME/JMAP part shape, one model).

    ``type``/``role`` is a genuine discriminator, not MTI: a ``multipart/*`` is a
    container; a text part references a :class:`Fragment`; a byte part references a
    ``storage.File``. Attachments are ``disposition=attachment`` + ``file``; inline
    images are ``disposition=inline`` + ``cid``.
    """

    runtime = True

    class Disposition(models.TextChoices):
        """How a part is presented."""

        INLINE = "inline", "Inline"
        ATTACHMENT = "attachment", "Attachment"

    class PartRole(models.TextChoices):
        """The semantic role of a part — the primary quotation/search filter axis."""

        BODY = "body", "Body"
        QUOTED = "quoted", "Quoted"
        SIGNATURE = "signature", "Signature"
        HEADER = "header", "Header"

    sqid = SqidField(real_field_name="id", prefix="prt_", min_length=8)
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="parts",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    position = models.PositiveIntegerField(default=0)
    type = models.CharField(max_length=128, default="text/plain")
    disposition = StateField(choices_enum=Disposition, default=Disposition.INLINE)
    role = StateField(choices_enum=PartRole, default=PartRole.BODY)
    cid = models.CharField(max_length=256, blank=True, default="")
    name = models.CharField(max_length=512, blank=True, default="")
    fragment = models.ForeignKey(
        "messaging.Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="parts",
    )
    file = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        """Django model options for the part source model."""

        abstract = True
        ordering = ("message", "position", "sqid")
        rebac_resource_type = "messaging/part"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the part type for Django displays."""

        return f"{self.type} ({self.role})"


class MessageEdge(SqidMixin, AuditMixin, AngeeModel):
    """One typed cross-message relation — the unified quote/reply/reference graph.

    ``Message.parent`` stays the single-parent reply pointer and ``Thread`` is
    membership; this carries the M2M/derived relations. A derived *quote* edge sets
    ``fragment`` (the shared content-addressed text) and a ``confidence``; both
    direction indexes back the bulk BFS.
    """

    runtime = True

    class EdgeKind(models.TextChoices):
        """The type of cross-message relation.

        ``quote`` is produced today (by the quotation builder); ``reply`` and the
        rest are social-milestone scope — the foundation for the next milestone
        (public social), with no producer in this email slice yet.
        """

        REPLY = "reply", "Reply"
        QUOTE = "quote", "Quote"
        MENTION = "mention", "Mention"
        CROSSPOST = "crosspost", "Crosspost"
        FORWARD = "forward", "Forward"
        DUPLICATE = "duplicate", "Duplicate"

    sqid = SqidField(real_field_name="id", prefix="mge_", min_length=8)
    src = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="edges_out",
    )
    dst = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="edges_in",
    )
    kind = StateField(choices_enum=EdgeKind, default=EdgeKind.QUOTE)
    fragment = models.ForeignKey(
        "messaging.Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="edges",
    )
    confidence = models.FloatField(default=1.0)

    objects = MessageEdgeManager()

    class Meta:
        """Django model options for the message-edge source model."""

        abstract = True
        rebac_resource_type = "messaging/message_edge"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("src", "dst", "kind"),
                name="uq_message_edge_src_dst_kind",
            ),
        )
        indexes = (
            models.Index(fields=("src", "dst")),
            models.Index(fields=("dst", "src")),
        )

    def __str__(self) -> str:
        """Return a readable edge description for Django displays."""

        return f"{self.src_id} -{self.kind}-> {self.dst_id}"


class Participant(SqidMixin, AuditMixin, AngeeModel):
    """A Handle-keyed membership of a thread/message — the queryable recipient row.

    The raw to/cc/bcc stays in ``Message.metadata`` as the lossless source; this is
    its queryable projection, so the inbox can group/filter by participant.
    """

    runtime = True

    class ParticipantRole(models.TextChoices):
        """The envelope or membership role of a participant."""

        FROM = "from", "From"
        TO = "to", "To"
        CC = "cc", "Cc"
        BCC = "bcc", "Bcc"
        AUTHOR = "author", "Author"
        OWNER = "owner", "Owner"
        MODERATOR = "moderator", "Moderator"
        VIEWER = "viewer", "Viewer"

    sqid = SqidField(real_field_name="id", prefix="ptp_", min_length=8)
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="participations",
    )
    role = StateField(choices_enum=ParticipantRole, default=ParticipantRole.TO)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Django model options for the participant source model."""

        abstract = True
        ordering = ("role", "sqid")
        rebac_resource_type = "messaging/participant"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a readable participant label for Django displays."""

        return f"{self.handle_id} ({self.role})"


class Reaction(SqidMixin, AuditMixin, AngeeModel):
    """An attributed reaction to a message (distinct from the rolled-up metrics).

    Social-milestone scope: the foundation for public social (YouTube/Facebook/
    WhatsApp); intentionally unused in this email slice (no producer yet).
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="rxn_", min_length=8)
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reactions",
    )
    reaction = models.CharField(max_length=64)

    class Meta:
        """Django model options for the reaction source model."""

        abstract = True
        rebac_resource_type = "messaging/reaction"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "handle", "reaction"),
                name="uq_reaction_message_handle_reaction",
            ),
        )

    def __str__(self) -> str:
        """Return the reaction for Django displays."""

        return self.reaction


class MessageMetrics(SqidMixin, AuditMixin, AngeeModel):
    """Rolled-up public engagement metrics for a message (the public counts).

    Flat one-to-one, not MTI — the metric set overlaps heavily across platforms;
    platform extras go in ``metadata``. Social-milestone scope: the foundation for
    public social; intentionally unused in this email slice (no producer yet).
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="mmx_", min_length=8)
    message = models.OneToOneField(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="metrics",
    )
    view_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    repost_count = models.PositiveIntegerField(default=0)
    quote_count = models.PositiveIntegerField(default=0)
    reply_count = models.PositiveIntegerField(default=0)
    bookmark_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        """Django model options for the message-metrics source model."""

        abstract = True
        rebac_resource_type = "messaging/message_metrics"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a readable metrics label for Django displays."""

        return f"metrics:{self.message_id}"
