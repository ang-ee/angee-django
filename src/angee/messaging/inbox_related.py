"""Pinned Related targets and independently paged, authorized connection rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.db import models
from django.db.models import F, Prefetch, Q, Subquery, Value
from django.db.models.functions import Cast

from angee.base.permissions import relation_read_queryset
from angee.messaging.inbox import InboxPage, InboxSearch, MessageInbox


@dataclass(frozen=True)
class InboxRelatedTarget:
    """Metadata and original source independent of the current uses page/search."""

    id: str
    kind: str
    label: str
    count: int
    unit: str
    source: Any | None = None
    part: Any | None = None
    file: Any | None = None
    fragment: Any | None = None


@dataclass(frozen=True)
class InboxConnection:
    """One distinct use, participant, confirmed handle or readable relation endpoint pair.

    Message endpoints are public IDs of messages the page carries.
    """

    id: str
    label: str
    role: str = ""
    provenance: str = ""
    confidence: float | None = None
    message_id: str | None = None
    part: Any | None = None
    handle: Any | None = None
    src_id: str | None = None
    dst_id: str | None = None


class InboxRelated:
    """Authorize the target once, retaining its metadata when local search has no matches."""

    def __init__(self, inbox: MessageInbox, target: str, source: str = "") -> None:
        self.inbox = inbox
        self.target = target
        self.kind, _, public_id = target.partition(":")
        self.source = inbox.messages.from_public_id(source) if source else None
        self.parts = inbox.parts.none()
        self.message = None
        self.party = None
        if self.kind in ("file", "fragment"):
            self.parts = inbox.target_parts(target)
        elif self.kind in ("participants", "relations"):
            self.message = inbox.message(public_id)
        elif self.kind == "handles":
            self.party = inbox.parties.from_public_id(public_id)
            if self.party is None:
                raise ValueError("Identity unavailable.")
        else:
            raise ValueError("Unknown Related target.")

    def handles(self) -> Any:
        """Confirmed readable handles; suggestions do not consolidate identity."""

        return self.inbox.handles.filter(party=self.party, party_link_confirmed=True)

    def participants(self) -> Any:
        """Recipient roles, including unavailable handles without leaking their identity.

        The pane lists the sender first, from ``Message.sender``; a ``from`` row
        repeats it and is never listed.
        """

        return self.inbox.participants.filter(message=self.message, role__in=("to", "cc", "bcc"))

    def participant_count(self) -> int:
        """The sender, when the message has one, and its recipients."""

        return int(cast(Any, self.message).sender_id is not None) + self.participants().count()

    def relations(self, text: str = "") -> Any:
        """Union produced edges with reply pointers before ordering and server paging."""

        message = cast(Any, self.message)
        messages = self.inbox.messages
        if text.strip():
            messages = self.inbox.matching(messages, InboxSearch(text=text, quoted=True))
        ids = Subquery(messages.order_by().values("pk"))
        edges = (
            self.inbox.edges()
            .filter(Q(src=self.message) | Q(dst=self.message))
            .filter(Q(src_id__in=ids) | Q(dst_id__in=ids))
        )
        edges = (
            edges.order_by()
            .annotate(
                _key=F("pk"),
                _src=F("src_id"),
                _dst=F("dst_id"),
                _kind=Cast("kind", models.TextField()),
                _confidence=F("confidence"),
                _at=F("created_at"),
            )
            .values("_key", "_src", "_dst", "_kind", "_confidence", "_at")
        )
        replies = self.inbox.messages.filter(parent_id__in=Subquery(self.inbox.messages.order_by().values("pk")))
        replies = replies.filter(Q(parent=message) | Q(pk=message.pk)).filter(
            Q(parent_id__in=ids) | Q(pk__in=ids)
        )
        replies = (
            replies.order_by()
            .annotate(
                _key=-F("pk"),
                _src=F("parent_id"),
                _dst=F("pk"),
                _kind=Value("reply", output_field=models.TextField()),
                _confidence=Value(None, output_field=models.FloatField()),
                _at=F("_order_at"),
            )
            .values("_key", "_src", "_dst", "_kind", "_confidence", "_at")
        )
        return edges.union(replies)

    def summary(self) -> InboxRelatedTarget:
        """The pinned target and original readable source never depend on a uses page."""

        if self.kind in ("file", "fragment"):
            source_parts = self.parts.filter(message=self.source) if self.source is not None else self.parts.none()
            part = source_parts.order_by("position", "pk").first() or self.parts.order_by("pk").first()
            if part is None:
                raise ValueError("Related source unavailable.")
            file = (
                self.inbox.collection("storage", "File").filter(pk=part.file_id).first()
                if self.kind == "file"
                else None
            )
            fragment = part.fragment if self.kind == "fragment" else None
            count = self.parts.order_by().values("message_id").distinct().count()
            return InboxRelatedTarget(
                self.target,
                self.kind,
                part.name or file.filename if file else "Shared text",
                count,
                "messages",
                self.source if source_parts.exists() else None,
                part,
                file,
                fragment,
            )
        if self.kind == "handles":
            party = cast(Any, self.party)
            return InboxRelatedTarget(
                self.target, self.kind, party.display_name, self.handles().count(), "handles", self.source
            )
        count = self.participant_count() if self.kind == "participants" else self.relations().count()
        return InboxRelatedTarget(
            self.target,
            self.kind,
            "Participants" if self.kind == "participants" else "Message connections",
            count,
            self.kind,
            self.message,
        )

    def page(self, *, text: str = "", oldest: bool = False, page: int = 1, size: int = 25) -> InboxPage:
        """Each target declares its own row/count unit and ignores main coverage."""

        window = InboxPage.window(page, size)
        if self.kind in ("file", "fragment"):
            uses = self.inbox.related(self.parts, text=text, oldest=oldest, page=page, size=size)
            # A row projects its part with the part's parent, fragment and file. Each is read once
            # for the page as the parts are read, never once per row.
            fields = self.parts.model._meta
            file_model = fields.get_field("file").related_model
            parts = (
                self.parts.filter(message_id__in=[message.pk for message in uses.rows])
                .prefetch_related(
                    Prefetch("parent", queryset=relation_read_queryset(self.parts.model, self.parts)),
                    Prefetch(
                        "fragment",
                        queryset=relation_read_queryset(fields.get_field("fragment").related_model, self.parts),
                    ),
                    Prefetch(
                        "file",
                        queryset=relation_read_queryset(file_model, self.parts).prefetch_related(
                            Prefetch(
                                "mime_type",
                                queryset=relation_read_queryset(
                                    file_model._meta.get_field("mime_type").related_model, self.parts
                                ),
                            )
                        ),
                    ),
                )
                .order_by("position", "pk")
            )
            by_message: dict[int, Any] = {}
            for part in parts:
                by_message.setdefault(part.message_id, part)
            return InboxPage(
                [
                    InboxConnection(
                        str(message.sqid), message.preview, message_id=str(message.sqid), part=by_message[message.pk]
                    )
                    for message in uses.rows
                ],
                uses.count,
                uses.message_count,
                self.inbox.page_messages([message.pk for message in uses.rows]),
            )
        if self.kind == "relations":
            relations = self.relations(text)
            selected = list(relations.order_by("_at" if oldest else "-_at", "_key")[window])
            message_model = self.inbox.messages.model
            return InboxPage(
                [
                    InboxConnection(
                        f"edge:{self.inbox.edges().model.public_id_from_pk(edge['_key'])}"
                        if edge["_key"] > 0
                        else f"reply:{self.inbox.messages.model.public_id_from_pk(-edge['_key'])}",
                        edge["_kind"],
                        provenance="Reply pointer" if edge["_kind"] == "reply" else "Produced message edge",
                        confidence=edge["_confidence"],
                        src_id=message_model.public_id_from_pk(edge["_src"]),
                        dst_id=message_model.public_id_from_pk(edge["_dst"]),
                    )
                    for edge in selected
                ],
                relations.count(),
                self.relations().count(),
                self.inbox.page_messages([pk for edge in selected for pk in (edge["_src"], edge["_dst"])]),
            )
        if self.kind == "handles":
            handles = self.handles()
            if text.strip():
                handles = handles.filter(Q(_sender_name__icontains=text.strip()) | Q(value__icontains=text.strip()))
            return InboxPage(
                [
                    InboxConnection(str(handle.sqid), handle._sender_name, role=handle.platform, handle=handle)
                    for handle in handles.order_by("created_at" if oldest else "-created_at", "pk")[window]
                ],
                handles.count(),
                self.handles().count(),
                self.inbox.messages.none(),
            )
        message = cast(Any, self.message)
        participants = self.participants()
        handles = self.inbox.handles
        sender_id = message.sender_id
        if text.strip():
            found = handles.filter(Q(_sender_name__icontains=text.strip()) | Q(value__icontains=text.strip()))
            participants = participants.filter(handle_id__in=Subquery(found.values("pk")))
            if sender_id is not None and not found.filter(pk=sender_id).exists():
                sender_id = None
        # The sender leads the recipients in either order; the recipients' window shifts past it.
        lead = int(sender_id is not None)
        recipients = participants.order_by("created_at" if oldest else "-created_at", "pk")
        selected = [
            *([(f"sender:{message.sqid}", sender_id, participants.model.ParticipantRole.FROM.value)]
              if lead and window.start == 0 else []),
            *((str(row.sqid), row.handle_id, row.role)
              for row in recipients[max(window.start - lead, 0):window.stop - lead]),
        ]
        readable = {handle.pk: handle for handle in handles.filter(pk__in=[handle_id for _, handle_id, _ in selected])}
        return InboxPage(
            [
                InboxConnection(
                    key,
                    readable[handle_id]._sender_name if handle_id in readable else "Unavailable identity",
                    role=role,
                    handle=readable.get(handle_id),
                )
                for key, handle_id, role in selected
            ],
            lead + participants.count(),
            self.participant_count(),
            self.inbox.messages.none(),
        )
