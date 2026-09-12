"""Independent server pages for the explorer's declared activity group axes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import (
    Case,
    Count,
    Expression,
    F,
    IntegerField,
    Max,
    OuterRef,
    Subquery,
    TextField,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce
from pydantic import BaseModel, ConfigDict

from angee.base.pagination import WindowSum
from angee.messaging.inbox import InboxPage


class InboxGroupScope(BaseModel):
    """A selected server bucket; null is an explicit empty bucket, not no scope."""

    model_config = ConfigDict(frozen=True)
    axis: str
    value: str | None


@dataclass(frozen=True)
class InboxGroup:
    """A readable bucket identity and counts inside that bucket."""

    value: str | None
    label: str
    count: int
    message_count: int


@dataclass(frozen=True)
class InboxGroupPage:
    """Group cardinality and distinct root rows; overlapping buckets are never summed."""

    rows: list[InboxGroup]
    count: int
    record_count: int
    message_count: int


class InboxGroups:
    """Page an annotated activity axis before requesting any expanded members.

    The caller declares the activity's ``_bucket`` annotation and count units.
    Model axes use their readable queryset for public IDs and labels. Enumerated
    axes declare their ordered choices. Neither path loads the unbounded corpus.
    ``partitioned_messages`` declares both count units to be unique message IDs,
    each assigned to exactly one bucket; their header counts add to root totals.
    """

    def __init__(
        self,
        rows: Any,
        *,
        identity: str,
        total_rows: Any | None = None,
        message: str = "pk",
        objects: Any | None = None,
        label_field: str = "display_name",
        scope_field: str = "_bucket",
        choices: tuple[tuple[str, str], ...] = (),
        empty_label: str = "None",
        label_expression: Expression | None = None,
        timestamp: str | None = None,
        oldest: bool = False,
        partitioned_messages: bool = False,
    ) -> None:
        self.rows = rows
        self.total_rows = rows if total_rows is None else total_rows
        self.identity = identity
        self.message = message
        self.objects = objects
        self.label_field = label_field
        self.scope_field = scope_field
        self.choices = choices
        self.empty_label = empty_label
        self.label_expression = label_expression
        self.timestamp = timestamp
        self.oldest = oldest
        self.partitioned_messages = partitioned_messages

    def scope(self, value: str | None) -> Any:
        """Resolve public IDs through the same read gate used for the group header."""

        if value is None:
            return self.rows.filter(_bucket__isnull=True)
        if self.objects is not None:
            record = self.objects.from_public_id(value)
            if record is None:
                raise ValueError("Group unavailable.")
            return self.rows.filter(**{self.scope_field: record.pk})
        if self.choices and value not in dict(self.choices):
            raise ValueError("Unknown group value.")
        return self.rows.filter(_bucket=value)

    def page(self, *, page: int = 1, size: int = 25) -> InboxGroupPage:
        """Count and page headers, retaining distinct totals across overlapping groups."""

        window = InboxPage.window(page, size)
        activity = self.rows.scoped_for_aggregate().order_by()
        groups = activity.values("_bucket").annotate(
            records=Count(self.identity, distinct=not self.partitioned_messages),
            messages=Count(self.message, distinct=not self.partitioned_messages),
        )
        if self.partitioned_messages:
            groups = groups.annotate(_message_count=WindowSum(Count(self.message)))
        if self.choices:
            # Enumerated axes have at most their declared number of headers.
            # Label and order this bounded result once; repeating an annotated
            # CASE in SELECT/ORDER BY can repeat expensive membership subqueries.
            labels = dict(self.choices)
            ranks = {value: index for index, (value, _) in enumerate(self.choices)}
            headers = list(groups)
            headers.sort(key=lambda row: ranks.get(row["_bucket"], len(ranks)))
            count = len(headers)
            selected = [
                {**row, "_label": labels.get(row["_bucket"], self.empty_label)} for row in headers[window]
            ]
        else:
            if self.objects is not None:
                label = Subquery(self.objects.filter(pk=OuterRef("_bucket")).values(self.label_field)[:1])
            elif self.label_expression is not None:
                label = self.label_expression
            else:
                label = F("_bucket")
            groups = groups.annotate(
                _label=Coalesce(label, Value(self.empty_label), output_field=TextField()),
            )
            if self.timestamp:
                groups = groups.annotate(_latest=Max(self.timestamp))
            else:
                groups = groups.annotate(
                    _rank=Case(
                        When(_bucket__isnull=True, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                )
            order = (
                ("_latest" if self.oldest else "-_latest", "_bucket")
                if self.timestamp else ("_rank", "_label", "_bucket")
            )
            selected = list(groups.annotate(_group_count=Window(Count("*"))).order_by(*order)[window])
            # An empty page has no window value; only out-of-range pages need
            # the separate count. Empty first pages already prove zero groups.
            count = selected[0]["_group_count"] if selected else groups.count() if page > 1 else 0
        public_ids = (
            {record.pk: str(record.sqid) for record in self.objects.filter(pk__in=[row["_bucket"] for row in selected])}
            if self.objects is not None
            else {}
        )
        if self.partitioned_messages and (selected or count == 0):
            total = selected[0]["_message_count"] if selected else 0
            totals = {"records": total, "messages": total}
        else:
            totals = (
                self.total_rows.scoped_for_aggregate()
                .order_by()
                .aggregate(records=Count(self.identity, distinct=True), messages=Count(self.message, distinct=True))
            )
        return InboxGroupPage(
            rows=[
                InboxGroup(
                    value=(public_ids[row["_bucket"]] if self.objects is not None else str(row["_bucket"]))
                    if row["_bucket"] is not None
                    else None,
                    label=row["_label"],
                    count=row["records"],
                    message_count=row["messages"],
                )
                for row in selected
            ],
            count=count,
            record_count=totals["records"],
            message_count=totals["messages"],
        )
