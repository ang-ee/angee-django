"""Message and exact-content result lenses over one authorized matching population."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import models
from django.db.models import Case, Count, Exists, F, Max, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast, Coalesce, TruncDate
from pydantic import BaseModel, ConfigDict, Field

from angee.messaging.inbox import InboxPage, MessageInbox
from angee.messaging.inbox_groups import InboxGroupPage, InboxGroups, InboxGroupScope


class InboxResultOptions(BaseModel):
    """Row unit, text roles and order; grouping never changes message inclusion."""

    model_config = ConfigDict(frozen=True)
    lens: str = "conversations"
    roles: list[str] = Field(default_factory=list)
    oldest: bool = False
    timezone: str = "UTC"


@dataclass(frozen=True)
class InboxResultRow:
    """One message, exact file/fragment, or unavailable attachment use."""

    id: str
    latest: datetime
    count: int
    part_count: int
    message: Any
    part: Any | None = None
    file: Any | None = None
    fragment: Any | None = None


class InboxResults:
    """Project and page a MessageInbox result queryset without regrouping client pages."""

    def __init__(self, inbox: MessageInbox, messages: Any, options: InboxResultOptions) -> None:
        if options.lens not in ("conversations", "timeline", "attachments", "text"):
            raise ValueError("Unknown result lens.")
        self.inbox = inbox
        self.messages = messages
        self.options = options
        self.zone = ZoneInfo(options.timezone)
        self.is_content = options.lens in ("attachments", "text")

    def activity(self, *, require_shared: bool = True) -> Any:
        """A readable matching-use population; known shared keys can skip requalification."""

        if not self.is_content:
            return self.messages.annotate(_identity=F("pk"), _message=F("pk"))
        parts = self.inbox.parts_for(self.messages)
        parts = parts.annotate(
            _message=F("message_id"),
            _order_at=self.messages.chronological_time("message__"),
        )
        if self.options.lens == "attachments":
            files = self.inbox.collection("storage", "File")
            return parts.filter(disposition="attachment").annotate(
                _identity=Case(
                    When(file_id__in=Subquery(files.order_by().values("pk")), then=F("file_id")),
                    default=-F("pk"),
                    output_field=models.BigIntegerField(),
                )
            )
        if set(self.options.roles) - {"title", "body", "quoted", "signature", "attachment"}:
            raise ValueError("Unknown shared-text part role.")
        # Eligibility needs a second distinct readable message, not the full use
        # count. EXISTS can stop at the first other use of a ubiquitous fragment.
        # Check each candidate use's message through an indexed existence read.
        # A broad message-ID subquery can be aggregated again for every fragment.
        readable_message = self.inbox.messages.filter(pk=OuterRef("message_id"))
        other_message = self.inbox.collection("messaging", "Part").filter(
            Exists(readable_message), fragment_id=OuterRef("fragment_id"),
        ).exclude(
            message_id=OuterRef("message_id")
        )
        parts = parts.filter(fragment_id__isnull=False)
        if require_shared:
            parts = parts.filter(Exists(other_message))
        if self.options.roles:
            parts = parts.filter(role__in=self.options.roles)
        return parts.annotate(_identity=F("fragment_id"))

    def groups(self, axis: str, *, require_shared: bool = True) -> InboxGroups:
        """Declare each applicable axis over the full matching-use population."""

        allowed = (
            {"account", "platform", "conversation", "sender"}
            if self.is_content
            else {"account", "platform", "conversation", "day"}
        )
        if axis not in allowed:
            raise ValueError("This grouping does not apply to the result lens.")
        rows = self.activity(require_shared=require_shared)
        prefix = "message__" if self.is_content else ""
        kwargs: dict[str, Any] = {"identity": "_identity", "message": "_message"}
        if axis == "day":
            rows = rows.annotate(_bucket=Cast(TruncDate("_order_at", tzinfo=self.zone), models.TextField()))
            kwargs.update(timestamp="_order_at", oldest=self.options.oldest)
        elif axis == "platform":
            rows = rows.annotate(_bucket=F(f"{prefix}platform"))
            kwargs.update(empty_label="Unknown platform")
        elif axis == "account":
            accounts = self.inbox.collection("integrate", "Integration")
            rows = rows.annotate(
                _bucket=Case(
                    When(**{f"{prefix}channel_id__in": Subquery(accounts.values("pk"))}, then=F(f"{prefix}channel_id")),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                )
            )
            kwargs.update(objects=accounts, scope_field=f"{prefix}channel_id", empty_label="No account")
        elif axis == "sender":
            handles = self.inbox.handles
            rows = rows.annotate(
                _bucket=Case(
                    When(**{f"{prefix}sender_id__in": Subquery(handles.values("pk"))}, then=F(f"{prefix}sender_id")),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                )
            )
            kwargs.update(
                objects=handles, scope_field=f"{prefix}sender_id",
                label_field="_sender_name", empty_label="Unknown sender",
            )
        elif axis == "conversation":
            threads = self.inbox.threads
            readable_thread = {f"{prefix}thread_id__in": Subquery(threads.values("pk"))}
            rows = rows.annotate(
                _bucket=Case(
                    When(**readable_thread, then=F(f"{prefix}thread_id")),
                    default=-F("_message"),
                    output_field=models.BigIntegerField(),
                ),
                _group_label=Case(
                    When(**readable_thread, then=Coalesce(F(f"{prefix}thread__title__text"), Value("Conversation"))),
                    default=Value("Standalone message"),
                    output_field=models.TextField(),
                ),
            )
            kwargs.update(
                label_annotation="_group_label", scope_field=f"{prefix}thread_id",
                timestamp="_order_at", oldest=self.options.oldest,
            )
            return InboxConversationGroups(rows, inbox=self.inbox, **kwargs)
        return InboxGroups(rows, **kwargs)

    def scoped(self, scope: InboxGroupScope | None, *, require_shared: bool = True) -> Any:
        """Use indexed local-day bounds for leaf pages rather than filtering a date cast."""

        if scope is None:
            return self.activity(require_shared=require_shared)
        if scope.axis == "day":
            if self.is_content or scope.value is None:
                raise ValueError("Invalid day grouping.")
            day = date.fromisoformat(scope.value)
            return self.activity(require_shared=require_shared).filter(
                _order_at__gte=datetime.combine(day, time.min, self.zone),
                _order_at__lt=datetime.combine(day + timedelta(days=1), time.min, self.zone),
            )
        return self.groups(scope.axis, require_shared=require_shared).scope(scope.value)

    def page(self, *, scope: InboxGroupScope | None = None, page: int = 1, size: int = 25) -> InboxPage:
        """Page distinct content first, then fetch one bounded source use per row."""

        activity = self.scoped(scope)
        order = "_order_at" if self.options.oldest else "-_order_at"
        if not self.is_content:
            return InboxPage(
                [
                    InboxResultRow(f"message:{message.sqid}", message._order_at, 1, 0, message)
                    for message in activity.order_by(order, "pk")[InboxPage.window(page, size)]
                ],
                activity.count(),
                activity.count(),
            )
        groups = (
            activity.scoped_for_aggregate()
            .order_by()
            .values("_identity")
            .annotate(
                latest=Max("_order_at"), messages=Count("message_id", distinct=True), parts=Count("pk", distinct=True)
            )
        )
        selected = list(
            groups.order_by("latest" if self.options.oldest else "-latest", "_identity")[InboxPage.window(page, size)]
        )
        keys = [row["_identity"] for row in selected]
        # The header page already knows each content identity's latest time.
        # Resolve the deterministic part tie-break only at those bounded points;
        # ranking every historical use repeats expensive content predicates.
        source_ids = (
            dict(
                self.scoped(scope, require_shared=False)
                .filter(
                    Q(*(Q(_identity=row["_identity"], _order_at=row["latest"]) for row in selected), _connector=Q.OR)
                )
                .order_by()
                .values("_identity")
                .annotate(source_id=Max("pk"))
                .values_list("_identity", "source_id")
            )
            if selected
            else {}
        )
        parts = {
            part.pk: part for part in self.inbox.parts.filter(pk__in=source_ids.values()).select_related("fragment")
        }
        sources = {key: parts[pk] for key, pk in source_ids.items()}
        files = (
            {record.pk: record for record in self.inbox.collection("storage", "File").filter(pk__in=keys)}
            if self.options.lens == "attachments"
            else {}
        )
        messages = {
            record.pk: record
            for record in self.inbox.messages.filter(pk__in=[part.message_id for part in sources.values()])
        }
        rows = []
        for group in selected:
            key = group["_identity"]
            part = sources[key]
            file = files.get(key)
            fragment = part.fragment if self.options.lens == "text" else None
            target = f"file:{file.sqid}" if file else f"fragment:{fragment.sqid}" if fragment else f"part:{part.sqid}"
            rows.append(
                InboxResultRow(
                    target,
                    group["latest"],
                    group["messages"],
                    group["parts"],
                    messages[part.message_id],
                    part,
                    file,
                    fragment,
                )
            )
        totals = (
            activity.order_by()
            .values("_identity", "message_id")
            .aggregate(records=Count("_identity", distinct=True), messages=Count("message_id", distinct=True))
        )
        return InboxPage(rows, totals["records"], totals["messages"])


class InboxConversationGroups(InboxGroups):
    """A mixed thread/standalone axis; model owners encode and resolve public IDs."""

    def __init__(self, rows: Any, *, inbox: MessageInbox, **kwargs: Any) -> None:
        super().__init__(rows, **kwargs)
        self.inbox = inbox

    def page(self, *, page: int = 1, size: int = 25) -> InboxGroupPage:
        result = super().page(page=page, size=size)
        rows = []
        for row in result.rows:
            key = int(row.value)
            owner = self.inbox.threads.model if key > 0 else self.inbox.messages.model
            kind = "thread" if key > 0 else "message"
            rows.append(replace(row, value=f"{kind}:{owner.public_id_from_pk(abs(key))}"))
        return replace(result, rows=rows)

    def scope(self, value: str | None) -> Any:
        kind, _, public_id = (value or "").partition(":")
        if kind == "thread":
            record = self.inbox.threads.from_public_id(public_id)
        elif kind == "message":
            record = self.inbox.messages.from_public_id(public_id)
        else:
            raise ValueError("Unknown conversation selection.")
        if record is None:
            raise ValueError("Conversation unavailable.")
        return (
            self.rows.filter(**{self.scope_field: record.pk})
            if kind == "thread" else self.rows.filter(_bucket=-record.pk)
        )
