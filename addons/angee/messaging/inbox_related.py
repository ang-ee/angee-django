"""Pinned Related targets and independently paged, authorized connection rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import models
from django.db.models import F, Q, Subquery, Value
from django.db.models.functions import Cast

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
    """One distinct use, participant, confirmed handle or readable relation endpoint pair."""

    id: str
    label: str
    role: str = ""
    provenance: str = ""
    confidence: float | None = None
    message: Any | None = None
    part: Any | None = None
    handle: Any | None = None
    src: Any | None = None
    dst: Any | None = None


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
        """Known source roles, including unavailable handles without leaking their identity."""

        return self.inbox.participants.filter(message=self.message)

    def relations(self, text: str = "") -> Any:
        """Union produced edges with reply pointers before ordering and server paging."""

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
        replies = replies.filter(Q(parent=self.message) | Q(pk=self.message.pk)).filter(
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
            return InboxRelatedTarget(
                self.target, self.kind, self.party.display_name, self.handles().count(), "handles", self.source
            )
        count = self.participants().count() if self.kind == "participants" else self.relations().count()
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
            uses = self.inbox.related(self.target, text=text, oldest=oldest, page=page, size=size)
            parts = self.parts.filter(message_id__in=[message.pk for message in uses.rows]).order_by("position", "pk")
            by_message: dict[int, Any] = {}
            for part in parts:
                by_message.setdefault(part.message_id, part)
            return InboxPage(
                [
                    InboxConnection(str(message.sqid), message.preview, message=message, part=by_message[message.pk])
                    for message in uses.rows
                ],
                uses.count,
                uses.message_count,
            )
        if self.kind == "relations":
            relations = self.relations(text)
            selected = list(relations.order_by("_at" if oldest else "-_at", "_key")[window])
            messages = {
                message.pk: message
                for message in self.inbox.messages.filter(
                    pk__in=[pk for edge in selected for pk in (edge["_src"], edge["_dst"])]
                )
            }
            return InboxPage(
                [
                    InboxConnection(
                        f"edge:{self.inbox.edges().model.public_id_from_pk(edge['_key'])}"
                        if edge["_key"] > 0
                        else f"reply:{self.inbox.messages.model.public_id_from_pk(-edge['_key'])}",
                        edge["_kind"],
                        provenance="Reply pointer" if edge["_kind"] == "reply" else "Produced message edge",
                        confidence=edge["_confidence"],
                        src=messages[edge["_src"]],
                        dst=messages[edge["_dst"]],
                    )
                    for edge in selected
                ],
                relations.count(),
                self.relations().count(),
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
            )
        participants = self.participants()
        handles = self.inbox.handles
        if text.strip():
            found = handles.filter(Q(_sender_name__icontains=text.strip()) | Q(value__icontains=text.strip()))
            participants = participants.filter(handle_id__in=Subquery(found.values("pk")))
        selected = list(participants.order_by("created_at" if oldest else "-created_at", "pk")[window])
        readable = {
            handle.pk: handle for handle in handles.filter(pk__in=[participant.handle_id for participant in selected])
        }
        return InboxPage(
            [
                InboxConnection(
                    str(participant.sqid),
                    readable[participant.handle_id]._sender_name
                    if participant.handle_id in readable
                    else "Unavailable identity",
                    role=participant.role,
                    handle=readable.get(participant.handle_id),
                )
                for participant in selected
            ],
            participants.count(),
            self.participants().count(),
        )
