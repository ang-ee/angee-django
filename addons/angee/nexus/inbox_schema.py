"""Nexus explorer projections; personal-message query policy belongs to Messaging."""

from datetime import datetime
from typing import TYPE_CHECKING, Generic, TypeVar, cast

import strawberry
from django.apps import apps

from angee.integrate.schema import IntegrationType
from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.schema import HandleType, MessageType, ThreadType
from angee.parties.schema import PartyType

Message = apps.get_model("messaging", "Message")
Row = TypeVar("Row")


@strawberry.experimental.pydantic.input(model=InboxCoverage, all_fields=True)
class InboxCoverageInput:
    """GraphQL syntax for Messaging's shared source/date coverage."""

    if TYPE_CHECKING:

        def to_pydantic(self) -> InboxCoverage: ...


@strawberry.experimental.pydantic.input(model=InboxSearch, all_fields=True)
class InboxSearchInput:
    """GraphQL syntax for Messaging's same-message predicates."""

    if TYPE_CHECKING:

        def to_pydantic(self) -> InboxSearch: ...


@strawberry.type
class InboxPage(Generic[Row]):
    """One typed projection for every server-paged explorer collection."""

    rows: list[Row]
    count: int
    message_count: int


@strawberry.type
class InboxSender:
    """Confirmed party or unresolved handle and its covered activity."""

    id: str
    handle: HandleType
    party: PartyType | None
    count: int
    latest: datetime
    first: datetime
    preview: str


@strawberry.type
class InboxConversation:
    """One authorized logical conversation with at most three matching previews."""

    id: str
    thread: ThreadType | None
    latest: datetime
    matching_count: int
    total_count: int
    messages: list[MessageType]


@strawberry.type
class InboxPartUse:
    """Authorized exact-content use count for a selected message part."""

    part_id: str
    target: str
    count: int


@strawberry.type
class InboxMessage:
    """An independently readable message plus its current result membership."""

    message: MessageType
    matches: bool
    uses: list[InboxPartUse]


@strawberry.type
class NexusInboxQuery:
    """Thin dispatchers for the Messaging collection's personal explorer."""

    @strawberry.field
    def inbox_platforms(self) -> list[str]:
        """Source platforms present in the currently readable personal corpus."""

        return list(
            Message.objects.all()
            .explorer()
            .messages.scoped_for_aggregate()
            .order_by("platform")
            .values_list("platform", flat=True)
            .distinct()
        )

    @strawberry.field
    def inbox_accounts(self) -> list[IntegrationType]:
        """Readable source accounts with eligible personal messages."""

        inbox = Message.objects.all().explorer()
        return (
            inbox.collection("integrate", "Integration")
            .filter(pk__in=inbox.messages.order_by().values("channel_id"))
            .order_by("display_name", "pk")
        )

    @strawberry.field
    def inbox_senders(
        self,
        coverage: InboxCoverageInput | None = None,
        text: str = "",
        include_sent: bool = False,
        sort: str = "recent",
        link: str = "",
        page: int = 1,
    ) -> InboxPage[InboxSender]:
        """Page Messaging's canonical readable sender identities."""

        return cast(
            InboxPage[InboxSender],
            Message.objects.all()
            .explorer()
            .senders(
                coverage.to_pydantic() if coverage else InboxCoverage(),
                text=text,
                include_sent=include_sent,
                sort=sort,
                link=link,
                page=page,
            ),
        )

    @strawberry.field
    def inbox_conversations(
        self,
        coverage: InboxCoverageInput | None = None,
        search: InboxSearchInput | None = None,
        sender: str = "",
        circle: str = "",
        page: int = 1,
        oldest: bool = False,
    ) -> InboxPage[InboxConversation]:
        """Page conversation sections before fetching their bounded previews."""

        inbox = Message.objects.all().explorer()
        rows = inbox.results(
            coverage.to_pydantic() if coverage else InboxCoverage(),
            search.to_pydantic() if search else InboxSearch(),
            sender=sender,
            circle=circle,
        )
        return cast(InboxPage[InboxConversation], inbox.conversations(rows, page=page, oldest=oldest))

    @strawberry.field
    def inbox_message(
        self,
        id: strawberry.ID,
        coverage: InboxCoverageInput | None = None,
        search: InboxSearchInput | None = None,
        sender: str = "",
        circle: str = "",
    ) -> InboxMessage:
        """Keep the selected message readable when coverage or sender changes."""

        inbox = Message.objects.all().explorer()
        message = inbox.message(str(id))
        matches = (
            inbox.results(
                coverage.to_pydantic() if coverage else InboxCoverage(),
                search.to_pydantic() if search else InboxSearch(),
                sender=sender,
                circle=circle,
            )
            .filter(pk=message.pk)
            .exists()
        )
        return InboxMessage(message=message, matches=matches, uses=cast(list[InboxPartUse], inbox.part_uses(message)))

    @strawberry.field
    def inbox_related(self, target: str, text: str = "", page: int = 1, oldest: bool = False) -> InboxPage[MessageType]:
        """Exact file/shared-text uses, independent of the selected sender and coverage."""

        return cast(
            InboxPage[MessageType],
            Message.objects.all()
            .explorer()
            .related(
                target,
                text=text,
                page=page,
                oldest=oldest,
            ),
        )
