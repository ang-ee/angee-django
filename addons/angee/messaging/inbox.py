"""Authorized personal-message exploration over the existing messaging corpus.

Conversation sections are selected in SQL before their bounded previews. Every
count and backlink is derived from readable eligible messages, never a global
Fragment counter or a page of messages regrouped in the browser.
"""

from __future__ import annotations

import shlex
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.apps import apps
from django.db import connections, models
from django.db.models import Case, Count, Exists, F, Max, OuterRef, Q, Subquery, When, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rebac import current_actor

from angee.base.actors import actor_user_id


class InboxCoverage(BaseModel):
    """Shared coverage; independent values intersect and values within a list unite."""

    model_config = ConfigDict(frozen=True)
    platforms: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    after: datetime | None = None
    before: datetime | None = None
    start: date | None = None
    end: date | None = None
    period: str = ""
    timezone: str = "UTC"

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        """Reject invalid local bounds rather than silently widening coverage."""

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid IANA timezone.") from error
        if self.period not in ("", "today", "7days", "30days"):
            raise ValueError("Unknown coverage period.")
        if self.period and (self.start is not None or self.end is not None):
            raise ValueError("Choose a relative period or custom dates.")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("The end date must follow the start date.")
        if any(value is not None and timezone.is_naive(value) for value in (self.after, self.before)):
            raise ValueError("Timestamp coverage requires an explicit timezone.")
        return self

    def bounds(self, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
        """Inclusive start, exclusive end, converted from local calendar days with DST."""

        zone = ZoneInfo(self.timezone)
        start, end = self.start, self.end
        if self.period:
            end = (now or timezone.now()).astimezone(zone).date()
            start = end - timedelta(days={"today": 0, "7days": 6, "30days": 29}[self.period])
        return (
            datetime.combine(start, time.min, zone) if start is not None else None,
            datetime.combine(end + timedelta(days=1), time.min, zone) if end is not None else None,
        )


class InboxSearch(BaseModel):
    """Message-local predicates, independent of sender-list presentation."""

    model_config = ConfigDict(frozen=True)
    text: str = ""
    quoted: bool = False
    attachment: str = ""
    direction: str = ""
    starred: bool = False
    handle: str = ""
    relations: list[str] = Field(default_factory=list)

    def terms(self) -> list[str]:
        """Keep quoted phrases together and require every search term."""

        try:
            return shlex.split(self.text.replace("\x00", ""))
        except ValueError as error:
            raise ValueError("Close the quotation mark to search for a phrase.") from error


@dataclass(frozen=True)
class InboxPage:
    """A server-selected page; total rows and messages are independent facts."""

    rows: list[Any]
    count: int
    message_count: int = 0

    @staticmethod
    def window(page: int, size: int) -> slice:
        """Validate the bounded offset page shared by records and group headers."""

        if page < 1 or size < 1 or size > 100:
            raise ValueError("Page must be positive and page size must be between 1 and 100.")
        return slice((page - 1) * size, page * size)


@dataclass(frozen=True)
class InboxConversation:
    """One logical thread, or one standalone message, with bounded previews."""

    id: str
    thread: Any | None
    latest: datetime
    matching_count: int
    total_count: int
    messages: Any


@dataclass(frozen=True)
class InboxPartUse:
    """One readable selected-message target and its distinct personal-message uses."""

    part_id: str
    target: str
    count: int


@dataclass(frozen=True)
class InboxSelection:
    """A readable navigator selection, independent of its current finder page."""

    id: str
    label: str
    party: Any | None = None
    handle: Any | None = None
    circle: Any | None = None


class MessageInbox:
    """Query owner for personal inbox reads, composed from a MessageQuerySet."""

    def __init__(self, queryset: Any) -> None:
        self.actor = queryset.actor() or current_actor()
        self.user_id = actor_user_id(self.actor)
        self.messages = (
            queryset.inbox()
            .filter(status__in=("synced", "edited", "sent"))
            .exclude(thread__modality="public_thread")
            .for_feed()
        )
        self.handles = self.collection("parties", "Handle").with_sender_name()
        self.parties = self.collection("parties", "Party")
        self.threads = self.collection("messaging", "Thread").inbox()
        self.parts = self.parts_for(self.messages)
        self.participants = self.collection("messaging", "Participant")

    def collection(self, app: str, model: str) -> Any:
        """Propagate the message collection actor to every joined read owner."""

        rows = apps.get_model(app, model).objects.all()
        return rows.with_actor(self.actor).scoped_for_aggregate() if self.actor is not None else rows.none()

    def parts_for(self, messages: Any) -> Any:
        """Readable parts of an already authorized eligible message population."""

        return self.collection("messaging", "Part").filter(
            message_id__in=Subquery(messages.order_by().values("pk"))
        )

    def accounts(self) -> Any:
        """Readable accounts projected from eligible messages without full-row deduplication."""

        return (
            self.collection("integrate", "Integration")
            .filter(pk__in=Subquery(self.messages.order_by().values("channel_id").distinct()))
            .order_by("display_name", "pk")
        )

    def coverage(self, coverage: InboxCoverage) -> Any:
        """Apply source/date coverage without silently relaxing contradictions."""

        rows = self.messages
        if coverage.platforms:
            rows = rows.filter(platform__in=coverage.platforms)
        if coverage.accounts:
            accounts = self.collection("integrate", "Integration")
            rows = rows.filter(channel_id__in=Subquery(accounts.filter(sqid__in=coverage.accounts).values("pk")))
        if coverage.kinds:
            allowed = {"mail": "email_thread", "direct": "direct", "group": "group"}
            if any(kind not in {*allowed, "other"} for kind in coverage.kinds):
                raise ValueError("Unknown conversation kind.")
            predicate = Q(thread__modality__in=[allowed[kind] for kind in coverage.kinds if kind in allowed])
            if "other" in coverage.kinds:
                predicate |= Q(thread__isnull=True) | ~Q(thread__modality__in=list(allowed.values()))
            rows = rows.filter(predicate)
        if coverage.after is not None:
            rows = rows.filter(_order_at__gte=coverage.after)
        if coverage.before is not None:
            rows = rows.filter(_order_at__lt=coverage.before)
        start, end = coverage.bounds()
        if start is not None:
            rows = rows.filter(_order_at__gte=start)
        if end is not None:
            rows = rows.filter(_order_at__lt=end)
        return rows

    def involving_handles(self, rows: Any, handles: Any) -> Any:
        """Authorship or explicit message recipients; thread membership is insufficient."""

        ids = handles.order_by().values("pk")
        recipients = self.participants.filter(handle_id__in=Subquery(ids), role__in=("to", "cc", "bcc"))
        # Keep the two indexed access paths separate. A correlated recipient
        # EXISTS inside an OR makes PostgreSQL estimate almost every message as
        # a match, then evaluate shared-content predicates across the corpus.
        # Build membership from the authorized corpus, then intersect the caller's
        # scope once. Reusing rows inside each branch recursively duplicates its
        # earlier sender/circle predicates when an exact-handle filter is added.
        authored = self.messages.filter(sender_id__in=Subquery(ids)).order_by().values("pk")
        addressed = (
            self.messages.filter(pk__in=Subquery(recipients.order_by().values("message_id")))
            .order_by().values("pk")
        )
        return rows.filter(pk__in=Subquery(authored.union(addressed)))

    def selection(self, *, sender: str = "", circle: str = "") -> InboxSelection | None:
        """Resolve selection labels and commands through the authorized record owner."""

        if sender and circle:
            raise ValueError("Choose one sender or one circle.")
        if circle:
            selected = self.collection("parties", "Circle").from_public_id(circle)
            if selected is None:
                raise ValueError("Circle unavailable.")
            return InboxSelection(id=f"circle:{selected.public_id}", label=selected.name, circle=selected)
        if not sender:
            return None
        kind, _, public_id = sender.partition(":")
        if kind == "party":
            party = self.parties.from_public_id(public_id)
            if party is not None:
                return InboxSelection(id=sender, label=party.display_name, party=party)
        elif kind == "handle":
            handle = self.handles.from_public_id(public_id)
            if handle is not None:
                return InboxSelection(id=sender, label=handle._sender_name, handle=handle)
        else:
            raise ValueError("Unknown sender selection.")
        raise ValueError("Sender unavailable.")

    def scoped(self, rows: Any, *, sender: str = "", circle: str = "") -> Any:
        """Restrict messages to an independently readable navigator selection."""

        selected = self.selection(sender=sender, circle=circle)
        if selected is None:
            return rows
        if selected.party is not None:
            handles = self.handles.filter(party=selected.party, party_link_confirmed=True)
        elif selected.handle is not None:
            handles = self.handles.filter(pk=selected.handle.pk)
        else:
            members = self.parties.in_circle(selected.circle, confirmed_only=True)
            handles = self.handles.filter(party_id__in=Subquery(members.values("pk")), party_link_confirmed=True)
        return self.involving_handles(rows, handles)

    def matching(self, rows: Any, search: InboxSearch) -> Any:
        """Search readable part uses with native Postgres full text and filename matching."""

        if search.direction:
            if search.direction not in ("inbound", "outbound"):
                raise ValueError("Unknown message direction.")
            rows = rows.filter(direction=search.direction)
        if search.handle:
            handle = self.handles.from_public_id(search.handle)
            if handle is None:
                raise ValueError("Handle unavailable.")
            rows = self.involving_handles(rows, self.handles.filter(pk=handle.pk))
        if search.starred:
            stars = apps.get_model("messaging", "MessageStar").objects.filter(user_id=self.user_id)
            rows = rows.filter(pk__in=Subquery(stars.values("message_id")))
        parts = self.parts.filter(message_id=OuterRef("pk"))
        if search.attachment:
            attachments = parts.filter(disposition="attachment")
            if search.attachment in ("image", "video", "audio"):
                attachments = attachments.filter(type__startswith=f"{search.attachment}/")
            elif search.attachment == "document":
                attachments = attachments.exclude(
                    Q(type__startswith="image/") | Q(type__startswith="video/") | Q(type__startswith="audio/")
                )
            elif search.attachment not in ("any", "none"):
                raise ValueError("Unknown attachment type.")
            rows = rows.filter(~Exists(attachments) if search.attachment == "none" else Exists(attachments))
        roles = ("title", "body", "quoted", "signature") if search.quoted else ("title", "body")
        for term in search.terms():
            text = (
                Q(
                    fragment__search=apps.get_model("messaging", "Fragment").objects.search_query(
                        term, search_type="phrase"
                    )
                )
                if connections[rows.db].vendor == "postgresql"
                else Q(fragment__text__icontains=term)
            )
            # Independent candidate paths let PostgreSQL start at the fragment
            # GIN index. A correlated text-or-filename predicate instead probes
            # parts for every message before it can narrow the population.
            text_matches = self.parts.filter(Q(role__in=roles) & text).order_by().values("message_id")
            filenames = (
                self.parts.filter(name__icontains=term, disposition="attachment").order_by().values("message_id")
            )
            rows = rows.filter(pk__in=Subquery(text_matches.union(filenames)))
        if search.relations:
            if set(search.relations) - {*apps.get_model("messaging", "MessageEdge").EdgeKind.values, "reply"}:
                raise ValueError("Unknown message relation kind.")
            edges = self.edges().filter(kind__in=search.relations)
            predicate = Q(pk__in=Subquery(edges.values("src_id"))) | Q(pk__in=Subquery(edges.values("dst_id")))
            if "reply" in search.relations:
                readable = Subquery(self.messages.order_by().values("pk"))
                predicate |= Q(parent_id__in=readable) | Q(
                    pk__in=Subquery(self.messages.order_by().values("parent_id"))
                )
            rows = rows.filter(predicate)
        return rows

    def edges(self) -> Any:
        """Only readable produced relations whose two endpoints are in the personal corpus."""

        readable = Subquery(self.messages.order_by().values("pk"))
        return self.collection("messaging", "MessageEdge").filter(src_id__in=readable, dst_id__in=readable)

    def relation_kinds(self) -> list[str]:
        """Expose the kinds of available readable connections, including reply pointers."""

        edges = self.edges()
        # Availability needs one match per declared kind, not a distinct scan of
        # the entire quote graph and both authorized endpoint populations.
        kinds = {kind for kind in edges.model.EdgeKind.values if edges.filter(kind=kind).exists()}
        if self.messages.filter(parent_id__in=Subquery(self.messages.order_by().values("pk"))).exists():
            kinds.add("reply")
        return sorted(kinds)

    def results(self, coverage: InboxCoverage, search: InboxSearch, *, sender: str = "", circle: str = "") -> Any:
        """The one intersection used by conversation rows, counts and selection status."""

        return self.matching(self.scoped(self.coverage(coverage), sender=sender, circle=circle), search)

    def _conversation_rows(self, rows: Any) -> Any:
        # An unreadable thread is not an exposed grouping identity. Its readable
        # messages remain independently reachable as standalone sections.
        return rows.annotate(
            _conversation=Case(
                When(thread_id__in=Subquery(self.threads.order_by().values("pk")), then=F("thread_id")),
                default=-F("pk"),
                output_field=models.BigIntegerField(),
            )
        )

    def conversations(self, rows: Any, *, page: int = 1, size: int = 25, oldest: bool = False) -> InboxPage:
        """Page logical sections, then select at most three matches in each using SQL windows."""

        matches = self._conversation_rows(rows).scoped_for_aggregate().order_by()
        groups = matches.values("_conversation").annotate(latest=Max("_order_at"), total=Count("pk", distinct=True))
        count = groups.count()
        selected = list(
            groups.order_by("latest" if oldest else "-latest", "_conversation")[InboxPage.window(page, size)]
        )
        keys = [row["_conversation"] for row in selected]
        ranked = (
            matches.filter(_conversation__in=keys)
            .annotate(
                _preview_rank=Window(
                    RowNumber(), partition_by=[F("_conversation")], order_by=[F("_order_at").desc(), F("pk").desc()]
                )
            )
            .filter(_preview_rank__lte=3)
        )
        preview_ids: dict[int, list[int]] = defaultdict(list)
        public_ids: dict[int, str] = {}
        for row in ranked:
            preview_ids[row._conversation].append(row.pk)
            public_ids[row.pk] = str(row.sqid)
        totals = dict(
            self._conversation_rows(self.messages)
            .scoped_for_aggregate()
            .order_by()
            .filter(_conversation__in=keys)
            .values("_conversation")
            .annotate(total=Count("pk", distinct=True))
            .values_list("_conversation", "total")
        )
        threads = {row.pk: row for row in self.threads.filter(pk__in=[key for key in keys if key > 0])}
        previews = self.messages.filter(pk__in=[pk for ids in preview_ids.values() for pk in ids])
        # Keep querysets in the GraphQL projection so its native optimizer can
        # batch only requested sender/part/file fields through their read gates.
        result = [
            InboxConversation(
                id=f"thread:{threads[key].sqid}" if key in threads else f"message:{public_ids[-key]}",
                thread=threads.get(key),
                latest=row["latest"],
                matching_count=row["total"],
                total_count=totals.get(key, 0),
                messages=previews.filter(pk__in=preview_ids[key]),
            )
            for row in selected
            for key in [row["_conversation"]]
        ]
        return InboxPage(result, count, matches.count())

    def message(self, public_id: str) -> Any:
        """Read a message independently of the current result filters."""

        message = self.messages.from_public_id(public_id)
        if message is None:
            raise ValueError("Message unavailable.")
        return message

    def target_parts(self, target: str) -> Any:
        """Authorize an exact file or shared-text target through an eligible use."""

        kind, _, public_id = target.partition(":")
        if kind == "file":
            file = self.collection("storage", "File").from_public_id(public_id)
            if file is None:
                raise ValueError("Related content unavailable.")
            parts = self.parts.filter(file=file)
        elif kind == "fragment":
            parts = self.parts.filter(fragment__sqid=public_id)
        else:
            raise ValueError("Unknown Related target.")
        if not parts.exists():
            raise ValueError("Related content unavailable.")
        return parts

    def part_uses(self, message: Any) -> list[InboxPartUse]:
        """Batch both target counts; selecting more parts does not add count queries."""

        parts = list(self.parts.filter(message=message).select_related("fragment"))
        files = {
            row.pk: str(row.sqid)
            for row in self.collection("storage", "File").filter(
                pk__in=[part.file_id for part in parts if part.file_id]
            )
        }
        targets = {"file_id": list(files), "fragment_id": [part.fragment_id for part in parts if part.fragment_id]}
        counts = {
            field: dict(
                self.parts.order_by()
                .filter(**{f"{field}__in": ids})
                .values(field)
                .annotate(total=Count("message_id", distinct=True))
                .values_list(field, "total")
            )
            for field, ids in targets.items()
        }
        result = []
        for part in parts:
            if part.file_id in files:
                result.append(
                    InboxPartUse(str(part.sqid), f"file:{files[part.file_id]}", counts["file_id"].get(part.file_id, 0))
                )
            if part.fragment_id:
                result.append(
                    InboxPartUse(
                        str(part.sqid), f"fragment:{part.fragment.sqid}", counts["fragment_id"].get(part.fragment_id, 0)
                    )
                )
        return result

    def related(self, target: str, *, text: str = "", page: int = 1, size: int = 25, oldest: bool = False) -> InboxPage:
        """Distinct readable message uses, independent of coverage and sender scope."""

        parts = self.target_parts(target)
        all_uses = self.messages.filter(pk__in=Subquery(parts.order_by().values("message_id")))
        rows = self.matching(all_uses, InboxSearch(text=text, quoted=True))
        return InboxPage(
            list(rows.order_by("_order_at" if oldest else "-_order_at", "pk")[InboxPage.window(page, size)]),
            rows.count(),
            all_uses.count(),
        )
