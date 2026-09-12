"""Server-paged navigator identities and their covered message activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.apps import apps
from django.db import models
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    FilteredRelation,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone
from pydantic import BaseModel, ConfigDict

from angee.base.pagination import WindowSum
from angee.messaging.inbox import InboxCoverage, InboxPage, MessageInbox
from angee.messaging.inbox_groups import InboxGroup, InboxGroupPage, InboxGroups, InboxGroupScope


@dataclass(frozen=True)
class InboxNavigatorRow:
    """One readable navigator identity with activity measured inside its current scope."""

    id: str
    label: str
    count: int
    latest: datetime | None
    first: datetime | None
    preview: str
    handle: Any | None = None
    party: Any | None = None
    thread: Any | None = None
    circle: Any | None = None
    parent_id: str | None = None
    has_children: bool = False


class InboxNavigatorOptions(BaseModel):
    """Finder predicates and order, independent of matching-message filters."""

    model_config = ConfigDict(frozen=True)
    lens: str = "senders"
    text: str = ""
    include_sent: bool = False
    sort: str = "recent"
    link: str = ""

    @property
    def recipient_activity(self) -> bool:
        """Whether one message can contribute multiple recipient identities."""

        return self.lens != "groups" and (self.include_sent or self.lens in ("recipients", "circles"))


class InboxNavigator(MessageInbox):
    """Project canonical people or exact handles from explicit covered activity."""

    def __init__(self, queryset: Any, coverage: InboxCoverage, options: InboxNavigatorOptions) -> None:
        super().__init__(queryset)
        self.coverage_input = coverage
        self.options = options

    def activity(self) -> Any:
        """One row per eligible author/recipient, with authorized canonical identity."""

        if self.options.lens == "groups":
            threads = self.threads.filter(modality="group")
            if self.options.text.strip():
                threads = threads.filter(title__text__icontains=self.options.text.strip())
            return (
                self.coverage(self.coverage_input)
                .filter(thread_id__in=Subquery(threads.values("pk")))
                .annotate(
                    _identity=F("thread_id"),
                    _candidate=F("thread_id"),
                    _party=Value(None, output_field=models.BigIntegerField()),
                )
            )
        handles = self.handles.exclude(owner_id=self.user_id) if self.user_id is not None else self.handles
        if self.user_id is not None:
            identity = apps.get_model("parties", "Party").objects.identity_for_user_id(self.user_id)
            if identity is not None:
                handles = handles.exclude(party=identity, party_link_confirmed=True)
        party_ids = Subquery(self.parties.order_by().values("pk"))
        activity = self.coverage(self.coverage_input)
        if self.options.recipient_activity:
            activity = activity.annotate(
                _recipient=FilteredRelation(
                    "participants",
                    condition=Q(direction="outbound", participants__role__in=("to", "cc", "bcc")),
                ),
            ).annotate(
                _recipient_readable=Exists(self.participants.filter(pk=OuterRef("_recipient__pk"))),
            ).filter(
                Q(direction="inbound") | Q(direction="outbound", _recipient_readable=True)
            ).annotate(
                _candidate=Case(When(direction="inbound", then=F("sender_id")), default=F("_recipient__handle_id")),
                _party=Case(
                    When(
                        direction="inbound",
                        sender__party_link_confirmed=True,
                        sender__party_id__in=party_ids,
                        then=F("sender__party_id"),
                    ),
                    When(
                        direction="outbound",
                        _recipient__handle__party_link_confirmed=True,
                        _recipient__handle__party_id__in=party_ids,
                        then=F("_recipient__handle__party_id"),
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
        if self.options.lens == "recipients":
            activity = activity.filter(direction="outbound")
        if self.options.lens == "handles":
            activity = activity.annotate(_identity=-F("_candidate"))
        text, link = ("", "") if self.options.lens == "circles" else (self.options.text, self.options.link)
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
                handles.filter(party_id__in=party_ids)
                if link == "suggested"
                else handles.exclude(party_id__in=party_ids)
            )
            activity = activity.filter(_party__isnull=True, _candidate__in=Subquery(candidates.order_by().values("pk")))
        elif link:
            raise ValueError("Unknown identity link state.")
        return activity

    def groups(self, axis: str) -> InboxGroups | InboxMembershipGroups:
        """Bind a navigator axis to its covered activity and readable identities."""

        if self.options.lens == "groups" and axis not in ("platform", "account", "recency"):
            raise ValueError("This grouping does not apply to group chats.")
        activity = self.activity()
        if axis in ("circle", "organization"):
            return InboxMembershipGroups(self, axis)
        if axis == "platform":
            return InboxGroups(
                activity.annotate(_bucket=F("platform")), identity="_identity", empty_label="Unknown platform"
            )
        if axis == "account":
            accounts = self.collection("integrate", "Integration")
            activity = activity.annotate(
                _bucket=Case(
                    When(channel_id__in=Subquery(accounts.values("pk")), then=F("channel_id")),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                )
            )
            return InboxGroups(
                activity, identity="_identity", objects=accounts, scope_field="channel_id", empty_label="No account"
            )
        if axis == "group":
            total_rows = activity
            threads = self.threads.filter(modality="group")
            group_activity = activity.filter(thread_id__in=Subquery(threads.values("pk")))
            activity = activity.annotate(
                _bucket=Case(
                    When(thread_id__in=Subquery(threads.values("pk")), then=F("thread_id")),
                    default=Value(None),
                    output_field=models.BigIntegerField(),
                )
            ).filter(Q(_bucket__isnull=False) | ~Exists(group_activity.filter(_identity=OuterRef("_identity"))))
            return InboxGroups(
                activity,
                identity="_identity",
                total_rows=total_rows,
                objects=threads,
                label_field="title__text",
                scope_field="thread_id",
                empty_label="Direct only",
            )
        if axis == "link":
            suggested = self.handles.filter(party_id__in=Subquery(self.parties.values("pk")))
            activity = activity.annotate(
                _bucket=Case(
                    When(_party__isnull=False, then=Value("confirmed")),
                    When(_candidate__in=Subquery(suggested.values("pk")), then=Value("suggested")),
                    default=Value("unlinked"),
                    output_field=models.TextField(),
                )
            )
            return InboxGroups(
                activity,
                identity="_identity",
                choices=(
                    ("confirmed", "Confirmed"),
                    ("suggested", "Suggested"),
                    ("unlinked", "Unlinked"),
                ),
            )
        if axis == "recency":
            zone = ZoneInfo(self.coverage_input.timezone)
            today = timezone.now().astimezone(zone).date()
            total_rows = activity
            starts = (
                ("today", today),
                ("yesterday", today - timedelta(days=1)),
                ("week", today - timedelta(days=today.weekday())),
                ("month", today.replace(day=1)),
            )
            # Latest activity >= a boundary iff at least one covered message
            # reaches it. Filter by the indexed time before projecting identities,
            # then retain their full history inside the first matching bucket.
            activity = activity.annotate(
                _bucket=Case(
                    *(
                        When(
                            _identity__in=Subquery(
                                activity.filter(_order_at__gte=datetime.combine(start, time.min, zone))
                                .order_by().values("_identity")
                            ),
                            then=Value(key),
                        )
                        for key, start in starts
                    ),
                    default=Value("older"),
                    output_field=models.TextField(),
                )
            )
            return InboxGroups(
                activity,
                identity="_identity",
                total_rows=total_rows,
                choices=(
                    ("today", "Today"),
                    ("yesterday", "Yesterday"),
                    ("week", "This week"),
                    ("month", "This month"),
                    ("older", "Older"),
                ),
            )
        raise ValueError("Unknown navigator grouping.")

    def page(
        self, *, scope: InboxGroupScope | None = None, parent: str | None = None, page: int = 1, size: int = 25
    ) -> InboxPage:
        """Select identities in SQL before fetching their bounded previews."""

        if self.options.lens == "circles":
            return InboxMembershipGroups(self, "circle").circles(parent=parent, page=page, size=size)
        if self.options.lens not in ("senders", "recipients", "handles", "groups"):
            raise ValueError("Unknown navigator lens.")
        is_group = self.options.lens == "groups"
        records = self.threads if is_group else self.handles
        activity = self.groups(scope.axis).scope(scope.value) if scope else self.activity()
        message_count = Count("pk", distinct=self.options.recipient_activity)
        groups = (
            activity.scoped_for_aggregate()
            .order_by()
            .values("_identity")
            .annotate(
                total=message_count,
                latest=Max("_order_at"),
                first=Min("_order_at"),
                handle_pk=Min("_candidate"),
            )
        )
        order = {"recent": "-latest", "name": "_name", "count": "-total", "first": "first"}.get(self.options.sort)
        if order is None:
            raise ValueError("Unknown sender order.")
        name = F("thread__title__text") if is_group else self.handles.sender_name_expression("sender__")
        if self.options.recipient_activity:
            name = Case(
                When(direction="inbound", then=name),
                default=self.handles.sender_name_expression("_recipient__handle__"),
            )
        groups = groups.annotate(_name=Min(name), _group_count=Window(Count("*")))
        if not self.options.recipient_activity:
            groups = groups.annotate(_message_count=WindowSum(message_count))
        selected = list(
            groups.order_by(order, "_name", "_identity")[InboxPage.window(page, size)]
        )
        count = selected[0]["_group_count"] if selected else groups.count() if page > 1 else 0
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
        parties = {
            party.pk: party for party in self.parties.filter(pk__in=[key for key in keys if key > 0 and not is_group])
        }
        selected_records = {
            record.pk: record for record in records.filter(pk__in=[row["handle_pk"] for row in selected])
        }
        return InboxPage(
            [
                InboxNavigatorRow(
                    id=f"thread:{record.sqid}"
                    if is_group
                    else f"party:{parties[key].sqid}"
                    if key in parties
                    else f"handle:{record.sqid}",
                    label=row["_name"] or f"Group {record.sqid}",
                    handle=None if is_group else record,
                    party=parties.get(key),
                    thread=record if is_group else None,
                    count=row["total"],
                    latest=row["latest"],
                    first=row["first"],
                    preview=preview_by_identity.get(key, ""),
                )
                for row in selected
                for key in [row["_identity"]]
                for record in [selected_records[row["handle_pk"]]]
            ],
            count,
            selected[0]["_message_count"]
            if selected and not self.options.recipient_activity
            else activity.order_by().values("pk").distinct().count(),
        )


class InboxMembershipGroups:
    """Overlapping readable circle/organisation buckets, counted before pagination."""

    def __init__(self, navigator: InboxNavigator, axis: str) -> None:
        self.navigator = navigator
        self.axis = axis
        self.activity = navigator.activity()
        self.objects = navigator.collection("parties", "Circle" if axis == "circle" else "Organization")
        self.label_field = "name" if axis == "circle" else "display_name"
        self.memberships = (
            self.objects.memberships(confirmed_only=True)
            if axis == "circle"
            else navigator.parties.organization_memberships()
        )

    def members(self, record: Any | None = None) -> Any:
        """Correlate a header's subtree or organisation to authorized membership edges."""

        if self.axis == "circle":
            return self.memberships.filter(
                circle__path__startswith=record.path if record is not None else OuterRef(OuterRef("path")),
                circle__created_by_id=record.created_by_id
                if record is not None
                else OuterRef(OuterRef("created_by_id")),
            )
        return self.memberships.filter(other_party_id=record.pk if record is not None else OuterRef(OuterRef("pk")))

    def scope(self, value: str | None) -> Any:
        """Select all activity of confirmed members, preserving each identity once."""

        if value is None:
            return self.activity.exclude(_party__in=Subquery(self.memberships.order_by().values("party_id")))
        record = self.objects.from_public_id(value)
        if record is None:
            raise ValueError("Group unavailable.")
        return self.activity.filter(_party__in=Subquery(self.members(record).order_by().values("party_id")))

    def annotated(self) -> Any:
        """SQL-correlated counts retain overlap without loading the message corpus."""

        activity = self.activity.filter(_party__in=Subquery(self.members().order_by().values("party_id")))
        aggregate = (
            activity.scoped_for_aggregate()
            .order_by()
            .annotate(_all=Value(0))
            .values("_all")
            .annotate(records=Count("_identity", distinct=True), messages=Count("pk", distinct=True))
        )
        return self.objects.annotate(
            _records=Coalesce(Subquery(aggregate.values("records")[:1]), Value(0)),
            _messages=Coalesce(Subquery(aggregate.values("messages")[:1]), Value(0)),
            _latest=Subquery(activity.order_by("-_order_at", "-pk").values("_order_at")[:1]),
            _first=Subquery(activity.order_by("_order_at", "pk").values("_order_at")[:1]),
            _preview=Subquery(activity.order_by("-_order_at", "-pk").values("preview")[:1]),
        )

    def page(self, *, page: int = 1, size: int = 25) -> InboxGroupPage:
        """Page nonempty membership headers and one final unassigned bucket."""

        window = InboxPage.window(page, size)
        objects = self.annotated().filter(_records__gt=0).order_by(self.label_field, "pk")
        unassigned = (
            self.scope(None)
            .scoped_for_aggregate()
            .order_by()
            .aggregate(records=Count("_identity", distinct=True), messages=Count("pk", distinct=True))
        )
        object_count = objects.count()
        rows = [
            InboxGroup(str(record.sqid), getattr(record, self.label_field), record._records, record._messages)
            for record in objects[window]
        ]
        if unassigned["records"] and window.start <= object_count < window.stop:
            rows.append(
                InboxGroup(
                    None,
                    "No circle" if self.axis == "circle" else "No organisation",
                    unassigned["records"],
                    unassigned["messages"],
                )
            )
        totals = (
            self.activity.scoped_for_aggregate()
            .order_by()
            .aggregate(records=Count("_identity", distinct=True), messages=Count("pk", distinct=True))
        )
        return InboxGroupPage(rows, object_count + bool(unassigned["records"]), totals["records"], totals["messages"])

    def circles(self, *, parent: str | None, page: int, size: int) -> InboxPage:
        """Page readable siblings; empty circles remain selectable and expandable."""

        objects = self.annotated()
        if self.navigator.options.text.strip():
            objects = objects.filter(name__icontains=self.navigator.options.text.strip())
        elif parent:
            selected = self.objects.from_public_id(parent)
            if selected is None:
                raise ValueError("Circle unavailable.")
            objects = objects.filter(parent=selected)
        else:
            objects = objects.exclude(parent_id__in=Subquery(self.objects.order_by().values("pk")))
        order = {"name": "name", "recent": "-_latest", "count": "-_messages", "first": "_first"}.get(
            self.navigator.options.sort
        )
        if order is None:
            raise ValueError("Unknown circle order.")
        objects = objects.annotate(_children=Exists(self.objects.filter(parent_id=OuterRef("pk"))))
        rows = list(
            objects.order_by(
                F(order.lstrip("-")).desc(nulls_last=True) if order.startswith("-") else F(order).asc(nulls_last=True),
                "name",
                "pk",
            )[InboxPage.window(page, size)]
        )
        parents = {
            record.pk: str(record.sqid) for record in self.objects.filter(pk__in=[row.parent_id for row in rows])
        }
        return InboxPage(
            [
                InboxNavigatorRow(
                    id=f"circle:{record.sqid}",
                    label=record.name,
                    circle=record,
                    count=record._messages,
                    latest=record._latest,
                    first=record._first,
                    preview=record._preview or "",
                    parent_id=f"circle:{parents[record.parent_id]}" if record.parent_id in parents else None,
                    has_children=record._children and not self.navigator.options.text.strip(),
                )
                for record in rows
            ],
            objects.count(),
            self.activity.order_by().values("pk").distinct().count(),
        )
