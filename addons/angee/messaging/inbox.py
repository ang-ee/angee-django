"""Authorized personal-message exploration over the existing messaging corpus.

Conversation sections are selected in SQL before their bounded previews. Every
count and backlink is derived from readable eligible messages, never a global
Fragment counter or a page of messages regrouped in the browser.
"""

from __future__ import annotations

import shlex
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.apps import apps
from django.db import connections, models
from django.db.models import Case, Count, Exists, F, Max, Min, OuterRef, Q, Subquery, Value, When, Window
from django.db.models.functions import Coalesce, RowNumber
from pydantic import BaseModel, ConfigDict, Field
from rebac import current_actor

from angee.base.actors import actor_user_id, is_user_actor


class InboxCoverage(BaseModel):
    """Shared coverage; independent values intersect and values within a list unite."""

    model_config = ConfigDict(frozen=True)
    platforms: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    after: datetime | None = None
    before: datetime | None = None


class InboxSearch(BaseModel):
    """Message-local predicates, independent of sender-list presentation."""

    model_config = ConfigDict(frozen=True)
    text: str = ""
    quoted: bool = False
    attachment: str = ""
    direction: str = ""
    starred: bool = False
    handle: str = ""

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
class InboxSender:
    """A confirmed party or a separate readable handle's covered activity."""

    id: str
    handle: Any
    party: Any | None
    count: int
    latest: datetime
    first: datetime
    preview: str


@dataclass(frozen=True)
class InboxPartUse:
    """One readable selected-message target and its distinct personal-message uses."""

    part_id: str
    target: str
    count: int


class MessageInbox:
    """Query owner for personal inbox reads, composed from a MessageQuerySet."""

    def __init__(self, queryset: Any) -> None:
        self.actor = queryset.actor() or current_actor()
        self.user_id = actor_user_id(self.actor) if is_user_actor(self.actor) else None
        self.messages = (
            queryset.inbox()
            .filter(status__in=("synced", "edited", "sent"))
            .exclude(thread__modality="public_thread")
            .for_feed()
        )
        self.handles = self.collection("parties", "Handle").with_sender_name()
        self.parties = self.collection("parties", "Party")
        self.threads = self.collection("messaging", "Thread").inbox()
        self.parts = self.collection("messaging", "Part").filter(
            message_id__in=Subquery(self.messages.order_by().values("pk"))
        )
        self.participants = self.collection("messaging", "Participant")

    def collection(self, app: str, model: str) -> Any:
        """Propagate the message collection actor to every joined read owner."""

        rows = apps.get_model(app, model).objects.all()
        return rows.with_actor(self.actor).scoped_for_aggregate() if self.actor is not None else rows.none()

    @staticmethod
    def _window(page: int, size: int) -> slice:
        if page < 1 or size < 1 or size > 100:
            raise ValueError("Page must be positive and page size must be between 1 and 100.")
        return slice((page - 1) * size, page * size)

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
        return rows

    def involving_handles(self, rows: Any, handles: Any) -> Any:
        """Authorship or explicit message recipients; thread membership is insufficient."""

        ids = handles.order_by().values("pk")
        recipients = self.participants.filter(
            handle_id__in=Subquery(ids), role__in=("to", "cc", "bcc"), message_id=OuterRef("pk")
        )
        return rows.filter(Q(sender_id__in=Subquery(ids)) | Exists(recipients))

    def scoped(self, rows: Any, *, sender: str = "", circle: str = "") -> Any:
        """Resolve public selection IDs through their current readable owner."""

        if sender and circle:
            raise ValueError("Choose one sender or one circle.")
        if sender:
            kind, _, public_id = sender.partition(":")
            if kind == "party":
                party = self.parties.from_public_id(public_id)
                if party is None:
                    raise ValueError("Sender unavailable.")
                handles = self.handles.filter(party=party, party_link_confirmed=True)
            elif kind == "handle":
                handle = self.handles.from_public_id(public_id)
                if handle is None:
                    raise ValueError("Sender unavailable.")
                handles = self.handles.filter(pk=handle.pk)
            else:
                raise ValueError("Unknown sender selection.")
            return self.involving_handles(rows, handles)
        if circle:
            selected = self.collection("parties", "Circle").from_public_id(circle)
            if selected is None:
                raise ValueError("Circle unavailable.")
            members = self.parties.in_circle(selected)
            return self.involving_handles(
                rows, self.handles.filter(party_id__in=Subquery(members.values("pk")), party_link_confirmed=True)
            )
        return rows

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
            attachments = parts.filter(disposition="attachment", file__isnull=False)
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
            matches = parts.filter((Q(role__in=roles) & text) | Q(name__icontains=term, disposition="attachment"))
            rows = rows.filter(Exists(matches))
        return rows

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
        selected = list(groups.order_by("latest" if oldest else "-latest", "_conversation")[self._window(page, size)])
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

    def senders(
        self,
        coverage: InboxCoverage,
        *,
        text: str = "",
        include_sent: bool = False,
        sort: str = "recent",
        link: str = "",
        page: int = 1,
        size: int = 25,
    ) -> InboxPage:
        """Aggregate covered activity once, then fetch bounded sender identities.

        Explicit outbound recipients join only when requested. COUNT DISTINCT
        protects parties with several addressed handles and inbound envelope joins.
        """

        handles = self.handles.exclude(owner_id=self.user_id) if self.user_id is not None else self.handles
        if self.user_id is not None:
            identity = apps.get_model("parties", "Party").objects.identity_for_user_id(self.user_id)
            if identity is not None:
                handles = handles.exclude(party=identity, party_link_confirmed=True)
        party_ids = Subquery(self.parties.order_by().values("pk"))
        activity = self.coverage(coverage)
        if include_sent:
            activity = activity.filter(
                Q(direction="inbound")
                | Q(
                    direction="outbound",
                    participants__role__in=("to", "cc", "bcc"),
                    participants__id__in=Subquery(self.participants.order_by().values("pk")),
                )
            ).annotate(
                _candidate=Case(When(direction="inbound", then=F("sender_id")), default=F("participants__handle_id")),
                _party=Case(
                    When(
                        direction="inbound",
                        sender__party_link_confirmed=True,
                        sender__party_id__in=party_ids,
                        then=F("sender__party_id"),
                    ),
                    When(
                        direction="outbound",
                        participants__handle__party_link_confirmed=True,
                        participants__handle__party_id__in=party_ids,
                        then=F("participants__handle__party_id"),
                    ),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                ),
            )
        else:
            activity = activity.filter(direction="inbound").annotate(
                _candidate=F("sender_id"),
                _party=Case(
                    When(sender__party_link_confirmed=True, sender__party_id__in=party_ids, then=F("sender__party_id")),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                ),
            )
        activity = activity.filter(_candidate__in=Subquery(handles.order_by().values("pk"))).annotate(
            _identity=Coalesce("_party", -F("_candidate")),
        )
        if text.strip():
            found = handles.filter(
                Q(_sender_name__icontains=text.strip())
                | Q(value__icontains=text.strip())
                | Q(normalized_value__icontains=text.strip())
            )
            activity = activity.filter(
                Q(_candidate__in=Subquery(found.order_by().values("pk")))
                | Q(_party__in=Subquery(found.filter(party_link_confirmed=True).order_by().values("party_id")))
            )
        if link == "confirmed":
            activity = activity.filter(_party__isnull=False)
        elif link in ("suggested", "unlinked"):
            candidates = (
                handles.filter(party_id__in=party_ids) if link == "suggested" else handles.filter(party_id__isnull=True)
            )
            activity = activity.filter(_party__isnull=True, _candidate__in=Subquery(candidates.order_by().values("pk")))
        elif link:
            raise ValueError("Unknown identity link state.")
        groups = (
            activity.scoped_for_aggregate()
            .order_by()
            .values("_identity")
            .annotate(
                total=Count("pk", distinct=True),
                latest=Max("_order_at"),
                first=Min("_order_at"),
                handle_pk=Min("_candidate"),
            )
        )
        order = {"recent": "-latest", "name": "_name", "count": "-total", "first": "first"}.get(sort)
        if order is None:
            raise ValueError("Unknown sender order.")
        if sort == "name":
            groups = groups.annotate(
                _name=Min(Subquery(handles.filter(pk=OuterRef("_candidate")).values("_sender_name")[:1]))
            )
        count = groups.count()
        selected = list(groups.order_by(order, "_identity")[self._window(page, size)])
        keys = [row["_identity"] for row in selected]
        previews = (
            activity.filter(_identity__in=keys)
            .annotate(
                _rank=Window(
                    RowNumber(),
                    partition_by=[F("_identity")],
                    order_by=[F("_order_at").desc(), F("pk").desc()],
                )
            )
            .filter(_rank=1)
            .values_list("_identity", "preview")
        )
        preview_by_identity = dict(previews)
        parties = {party.pk: party for party in self.parties.filter(pk__in=[key for key in keys if key > 0])}
        selected_handles = {
            handle.pk: handle for handle in handles.filter(pk__in=[row["handle_pk"] for row in selected])
        }
        return InboxPage(
            [
                InboxSender(
                    id=f"party:{parties[key].sqid}"
                    if key in parties
                    else f"handle:{selected_handles[row['handle_pk']].sqid}",
                    handle=selected_handles[row["handle_pk"]],
                    party=parties.get(key),
                    count=row["total"],
                    latest=row["latest"],
                    first=row["first"],
                    preview=preview_by_identity.get(key, ""),
                )
                for row in selected
                for key in [row["_identity"]]
            ],
            count,
        )

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
            list(rows.order_by("_order_at" if oldest else "-_order_at", "pk")[self._window(page, size)]),
            rows.count(),
            all_uses.count(),
        )
