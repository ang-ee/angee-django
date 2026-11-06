"""Nexus explorer projections; personal-message query policy belongs to Messaging."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

import strawberry
from django.apps import apps
from rebac.graphql.strawberry_django import optimize

from angee.integrate.schema import IntegrationType
from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.inbox_groups import InboxGroupScope
from angee.messaging.inbox_navigator import InboxNavigator, InboxNavigatorOptions
from angee.messaging.inbox_related import InboxRelated
from angee.messaging.inbox_results import InboxResultOptions, InboxResults
from angee.messaging.inbox_transcript import InboxTranscript
from angee.messaging.schema import FragmentType, HandleType, MessageType, PartType, ThreadType
from angee.nexus.inbox import NexusInboxNavigator, NexusInboxNavigatorOptions
from angee.parties.schema import CircleType, PartyType
from angee.storage.schema import FileType

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


@strawberry.experimental.pydantic.input(model=NexusInboxNavigatorOptions, all_fields=True)
class InboxNavigatorInput:
    """Finder intent for the navigator's own collection."""

    if TYPE_CHECKING:

        def to_pydantic(self) -> NexusInboxNavigatorOptions: ...


@strawberry.experimental.pydantic.input(model=InboxGroupScope, all_fields=True)
class InboxGroupScopeInput:
    """An independently paged, explicitly selected group bucket."""

    if TYPE_CHECKING:

        def to_pydantic(self) -> InboxGroupScope: ...


@strawberry.experimental.pydantic.input(model=InboxResultOptions, all_fields=True)
class InboxResultInput:
    """Result row and ordering declaration."""

    if TYPE_CHECKING:

        def to_pydantic(self) -> InboxResultOptions: ...


@strawberry.type
class InboxPage(Generic[Row]):
    """One typed projection for every server-paged explorer collection."""

    rows: list[Row]
    count: int
    message_count: int


@strawberry.type
class InboxPageWithMessages(Generic[Row]):
    """A server page whose rows name messages by public ID; each message loads once.

    ``messages`` is Messaging's readable queryset of the named messages, so the
    native optimizer loads every relation the query selects in one read per
    relation instead of one actor-scoped lookup per row.
    """

    rows: list[Row]
    count: int
    message_count: int
    messages: list[MessageType]


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
class InboxNavigatorRow:
    """Identity, group chat or circle with its covered activity facts."""

    id: str
    label: str
    count: int
    latest: datetime | None
    first: datetime | None
    preview: str
    handle: HandleType | None
    party: PartyType | None
    thread: ThreadType | None
    circle: CircleType | None
    parent_id: str | None
    has_children: bool


@strawberry.type
class InboxGroup(Generic[Row]):
    """A readable grouping key, counts inside that bucket, and its carried first page."""

    value: str | None
    label: str
    count: int
    message_count: int
    latest: datetime | None
    platform: str
    account: str
    modality: str
    rows: list[Row]


@strawberry.type
class InboxGroupPage(Generic[Row]):
    """Independent header page and distinct totals over its complete population."""

    rows: list[InboxGroup[Row]]
    count: int
    record_count: int
    message_count: int


@strawberry.type
class InboxPartUse:
    """Authorized exact-content use count for a selected message part."""

    part_id: str
    target: str
    count: int


@strawberry.type
class InboxResultRow:
    """One message, exact content target, or unavailable attachment use; its page carries the message."""

    id: str
    latest: datetime
    count: int
    part_count: int
    message_id: strawberry.ID
    part: PartType | None
    file: FileType | None
    fragment: FragmentType | None


@strawberry.type
class InboxResultGroupPage(InboxGroupPage[InboxResultRow]):
    """Result headers whose carried rows name messages; each message loads once."""

    messages: list[MessageType]


@strawberry.type
class InboxRelatedTarget:
    """Pinned target metadata, unaffected by the local connection search/page."""

    id: str
    kind: str
    label: str
    count: int
    unit: str
    source: MessageType | None
    part: PartType | None
    file: FileType | None
    fragment: FragmentType | None


@strawberry.type
class InboxConnection:
    """A typed use, participant, confirmed handle, or produced relation; its page carries the messages."""

    id: str
    label: str
    role: str
    provenance: str
    confidence: float | None
    message_id: strawberry.ID | None
    part: PartType | None
    handle: HandleType | None
    src_id: strawberry.ID | None
    dst_id: strawberry.ID | None


@strawberry.type
class InboxMessage:
    """An independently readable message plus its current result membership."""

    selected: strawberry.Private[Any]
    matches: bool
    uses: list[InboxPartUse]

    @strawberry.field
    def message(self, info: strawberry.Info) -> MessageType:
        """Read the selected message once with every relation the query selects."""

        return cast(MessageType, optimize(self.selected, info).get())


@strawberry.type
class InboxSelection:
    """The independently readable navigator selection."""

    id: str
    label: str
    party: PartyType | None
    handle: HandleType | None
    circle: CircleType | None


@strawberry.type
class InboxTranscriptMatches:
    """Match facts for a loaded transcript window and its nearest navigation anchors."""

    thread: ThreadType
    count: int
    total: int
    visible: list[str]
    previous: str | None
    next: str | None


@strawberry.type
class NexusInboxQuery:
    """Thin dispatchers for the Messaging collection's personal explorer."""

    @strawberry.field
    def inbox_selection(self, sender: str = "", circle: str = "") -> InboxSelection | None:
        """Keep the chosen identity visible even when a finder no longer includes it."""

        return cast(InboxSelection | None, Message.objects.all().explorer().selection(sender=sender, circle=circle))

    @strawberry.field
    def inbox_transcript_matches(
        self,
        thread: str,
        visible: list[str],
        at: str = "",
        coverage: InboxCoverageInput | None = None,
        search: InboxSearchInput | None = None,
        sender: str = "",
        circle: str = "",
    ) -> InboxTranscriptMatches:
        """Read match navigation without filtering the independently paged transcript."""

        inbox = Message.objects.all().explorer()
        matching = inbox.results(
            coverage.to_pydantic() if coverage else InboxCoverage(),
            search.to_pydantic() if search else InboxSearch(),
            sender=sender,
            circle=circle,
        )
        return cast(InboxTranscriptMatches, InboxTranscript(inbox, matching, thread).matches(visible=visible, at=at))

    @strawberry.field
    def inbox_related_target(self, target: str, source: str = "") -> InboxRelatedTarget:
        """Read a pinned target independently of its paged uses."""

        return cast(InboxRelatedTarget, InboxRelated(Message.objects.all().explorer(), target, source).summary())

    @strawberry.field
    def inbox_connections(
        self, target: str, text: str = "", page: int = 1, size: int = 25, oldest: bool = False
    ) -> InboxPageWithMessages[InboxConnection]:
        """Page the target's declared row unit, without main scope or coverage."""

        return cast(
            InboxPageWithMessages[InboxConnection],
            InboxRelated(Message.objects.all().explorer(), target).page(text=text, page=page, size=size, oldest=oldest),
        )

    @strawberry.field
    def inbox_results(
        self,
        coverage: InboxCoverageInput | None = None,
        search: InboxSearchInput | None = None,
        options: InboxResultInput | None = None,
        sender: str = "",
        circle: str = "",
        scope: InboxGroupScopeInput | None = None,
        page: int = 1,
        size: int = 25,
    ) -> InboxPageWithMessages[InboxResultRow]:
        """Page the chosen result unit inside the common matching-message population."""

        inbox = Message.objects.all().explorer()
        rows = inbox.results(
            coverage.to_pydantic() if coverage else InboxCoverage(),
            search.to_pydantic() if search else InboxSearch(),
            sender=sender,
            circle=circle,
        )
        result = InboxResults(inbox, rows, options.to_pydantic() if options else InboxResultOptions())
        return cast(
            InboxPageWithMessages[InboxResultRow],
            result.page(scope=scope.to_pydantic() if scope else None, page=page, size=size),
        )

    @strawberry.field
    def inbox_result_groups(
        self,
        axis: str,
        coverage: InboxCoverageInput | None = None,
        search: InboxSearchInput | None = None,
        options: InboxResultInput | None = None,
        sender: str = "",
        circle: str = "",
        page: int = 1,
        size: int = 25,
        leaf_size: int = 0,
    ) -> InboxResultGroupPage:
        """Page headers independently; with ``leaf_size`` each carries its first page of rows."""

        inbox = Message.objects.all().explorer()
        rows = inbox.results(
            coverage.to_pydantic() if coverage else InboxCoverage(),
            search.to_pydantic() if search else InboxSearch(),
            sender=sender,
            circle=circle,
        )
        result = InboxResults(inbox, rows, options.to_pydantic() if options else InboxResultOptions())
        return cast(InboxResultGroupPage, result.group_page(axis, page=page, size=size, leaf_size=leaf_size))

    @strawberry.field
    def inbox_navigator(
        self,
        coverage: InboxCoverageInput | None = None,
        options: InboxNavigatorInput | None = None,
        scope: InboxGroupScopeInput | None = None,
        parent: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> InboxPage[InboxNavigatorRow]:
        """Page the chosen navigator unit, optionally inside one group."""

        navigator = NexusInboxNavigator(
            Message.objects.all(),
            coverage.to_pydantic() if coverage else InboxCoverage(),
            options.to_pydantic() if options else NexusInboxNavigatorOptions(),
        )
        return cast(
            InboxPage[InboxNavigatorRow],
            navigator.page(
                scope=scope.to_pydantic() if scope else None,
                parent=parent,
                page=page,
                size=size,
            ),
        )

    @strawberry.field
    def inbox_navigator_groups(
        self,
        axis: str,
        coverage: InboxCoverageInput | None = None,
        options: InboxNavigatorInput | None = None,
        page: int = 1,
        size: int = 25,
        leaf_size: int = 0,
    ) -> InboxGroupPage[InboxNavigatorRow]:
        """Page all applicable group headers; with ``leaf_size`` each carries its first identity page."""

        navigator = NexusInboxNavigator(
            Message.objects.all(),
            coverage.to_pydantic() if coverage else InboxCoverage(),
            options.to_pydantic() if options else NexusInboxNavigatorOptions(),
        )
        return cast(
            InboxGroupPage[InboxNavigatorRow],
            navigator.group_page(axis, page=page, size=size, leaf_size=leaf_size),
        )

    @strawberry.field
    def inbox_relation_kinds(self) -> list[str]:
        """Produced readable relation kinds available for the message filter."""

        return Message.objects.all().explorer().relation_kinds()

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

        return Message.objects.all().explorer().accounts()

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
            InboxNavigator(
                Message.objects.all(),
                coverage.to_pydantic() if coverage else InboxCoverage(),
                InboxNavigatorOptions(text=text, include_sent=include_sent, sort=sort, link=link),
            ).page(page=page),
        )

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
        return InboxMessage(
            selected=inbox.page_messages([message.pk]),
            matches=matches,
            uses=cast(list[InboxPartUse], inbox.part_uses(message)),
        )

    @strawberry.field
    def inbox_related(
        self, target: str, text: str = "", page: int = 1, size: int = 25, oldest: bool = False
    ) -> InboxPage[MessageType]:
        """Exact file/shared-text uses, independent of the selected sender and coverage."""

        return cast(
            InboxPage[MessageType],
            Message.objects.all()
            .explorer()
            .related(
                target,
                text=text,
                page=page,
                size=size,
                oldest=oldest,
            ),
        )
