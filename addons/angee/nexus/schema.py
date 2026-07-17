"""GraphQL projections for viewer-relative party relationship analytics."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from rebac import current_actor
from strawberry import auto

from angee.base.actors import actor_user_id, is_user_actor
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.messaging.schema import MessageType
from angee.parties.schema import PartyType

Tie = apps.get_model("nexus", "Tie")
Cadence = apps.get_model("nexus", "Cadence")
Party = apps.get_model("parties", "Party")
Circle = apps.get_model("parties", "Circle")
Message = apps.get_model("messaging", "Message")


def _viewer_user_id() -> Any | None:
    """Return the authenticated REBAC actor's user id."""

    actor = current_actor()
    if not is_user_actor(actor):
        return None
    return actor_user_id(actor)


@strawberry_django.type(Tie)
class TieType(AngeeNode):
    """GraphQL projection of one fully derived canonical party edge."""

    party_a: PartyType
    party_b: PartyType
    a_to_b_count: auto
    b_to_a_count: auto
    message_count: auto
    thread_count: auto
    platforms: strawberry.scalars.JSON
    first_interaction_at: auto
    last_interaction_at: auto
    gravity: auto
    is_fading: auto
    updated_at: auto


@strawberry_django.type(Cadence)
class CadenceType(AngeeNode):
    """GraphQL projection of one viewer-owned party cadence."""

    party: PartyType
    cadence_days: auto
    touch_due_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Party, name="PartyType", extend=True)
class PartyNexusExtension:
    """Contribute viewer-relative edge and cadence fields to every Party subtype."""

    @strawberry_django.field(only=["id"])
    def tie(self) -> TieType | None:
        """Return the edge between this party and the viewer's Person party."""

        user_id = _viewer_user_id()
        if user_id is None:
            return None
        viewer = Party.objects.identity_for_user_id(user_id)
        if viewer is None or viewer.pk == cast(Any, self).pk:
            return None
        return cast(TieType | None, Tie.objects.for_parties(viewer, cast(Any, self)))

    @strawberry_django.field(only=["id"])
    def cadence(self) -> CadenceType | None:
        """Return the viewer's cadence row for this party."""

        user_id = _viewer_user_id()
        if user_id is None:
            return None
        return cast(
            CadenceType | None,
            Cadence.objects.filter(user_id=user_id, party_id=cast(Any, self).pk).first(),
        )


@strawberry.type
class PartyTimelinePayload:
    """One newest-first page of a party collection's cross-channel timeline."""

    messages: list[MessageType]
    count: int


@strawberry.type
class PartyGraphPayload:
    """A bounded, actor-visible party graph rooted at one party or circle."""

    nodes: strawberry.scalars.JSON
    edges: strawberry.scalars.JSON
    truncated: bool


@strawberry.type
class NexusOverviewPayload:
    """Viewer-relative fading ties and cadences due during the local day."""

    fading_ties: list[TieType]
    fading_count: int
    due_cadences: list[CadenceType]
    due_count: int


@strawberry.type
class NexusQuery:
    """Read surface for derived relationship intelligence."""

    @strawberry.field
    def party_network(self, party_id: strawberry.ID) -> list[TieType]:
        """Return actor-visible derived edges touching one readable party."""

        party = Party.objects.all().apply_ambient_scope().from_public_id(str(party_id))
        if party is None:
            raise ValueError("party not found")
        edges = Tie.objects.around_party(party).apply_ambient_scope()
        return cast("list[TieType]", list(edges))

    @strawberry.field
    def party_graph(
        self,
        root_id: strawberry.ID | None = None,
        circle_id: strawberry.ID | None = None,
        lenses: list[str] | None = None,
        depth: int = 1,
        limit: int = 60,
    ) -> PartyGraphPayload:
        """Delegate a bounded actor-scoped graph traversal to the Tie collection."""

        if (root_id is None) == (circle_id is None):
            raise ValueError("party_graph requires exactly one root_id or circle_id")
        party = (
            Party.objects.all().apply_ambient_scope().from_public_id(str(root_id))
            if root_id is not None
            else None
        )
        circle = (
            Circle.objects.all().apply_ambient_scope().from_public_id(str(circle_id))
            if circle_id is not None
            else None
        )
        if root_id is not None and party is None:
            raise ValueError("party not found")
        if circle_id is not None and circle is None:
            raise ValueError("circle not found")
        graph = Tie.objects.party_graph(
            root=party,
            circle=circle,
            lenses=lenses,
            depth=depth,
            limit=limit,
        )
        return PartyGraphPayload(
            nodes=cast(strawberry.scalars.JSON, list(graph.nodes)),
            edges=cast(strawberry.scalars.JSON, list(graph.edges)),
            truncated=graph.truncated,
        )

    @strawberry.field
    def nexus_overview(self, peek_limit: int = 6) -> NexusOverviewPayload:
        """Return bounded relationship health relative to the authenticated user."""

        user_id = _viewer_user_id()
        if user_id is None:
            return NexusOverviewPayload(
                fading_ties=[],
                fading_count=0,
                due_cadences=[],
                due_count=0,
            )
        viewer = Party.objects.identity_for_user_id(user_id)
        if viewer is None:
            return NexusOverviewPayload(
                fading_ties=[],
                fading_count=0,
                due_cadences=[],
                due_count=0,
            )
        overview = Tie.objects.overview_for(
            viewer,
            user_id=user_id,
            peek_limit=peek_limit,
        )
        return NexusOverviewPayload(
            fading_ties=cast("list[TieType]", list(overview.fading_ties)),
            fading_count=overview.fading_count,
            due_cadences=cast("list[CadenceType]", list(overview.due_cadences)),
            due_count=overview.due_count,
        )

    @strawberry.field
    def party_timeline(
        self,
        info: strawberry.Info,
        party_id: strawberry.ID,
        search: str = "",
        before: strawberry.ID | None = None,
        limit: int = 50,
    ) -> PartyTimelinePayload:
        """Delegate one actor-scoped timeline page to messaging's collection owner."""

        party = Party.objects.all().apply_ambient_scope().from_public_id(str(party_id))
        if party is None:
            raise ValueError("party not found")
        messages, count = Message.objects.timeline_for_parties(
            (party,),
            search=search,
            before=str(before) if before is not None else None,
            limit=limit,
        )
        return PartyTimelinePayload(messages=cast("list[MessageType]", messages), count=count)

    @strawberry.field
    def circle_timeline(
        self,
        info: strawberry.Info,
        circle_id: strawberry.ID,
        search: str = "",
        before: strawberry.ID | None = None,
        limit: int = 50,
    ) -> PartyTimelinePayload:
        """Return messages involving members of a readable circle subtree."""

        circle = Circle.objects.all().apply_ambient_scope().from_public_id(str(circle_id))
        if circle is None:
            raise ValueError("circle not found")
        parties = Party.objects.all().apply_ambient_scope().in_circle(circle)
        messages, count = Message.objects.timeline_for_parties(
            parties,
            search=search,
            before=str(before) if before is not None else None,
            limit=limit,
        )
        return PartyTimelinePayload(messages=cast("list[MessageType]", messages), count=count)


_TIE_RESOURCE = hasura_model_resource(
    TieType,
    model=Tie,
    name="ties",
    filterable=[
        "id",
        "party_a",
        "party_b",
        "gravity",
        "is_fading",
        "message_count",
        "last_interaction_at",
    ],
    sortable=["gravity", "message_count", "last_interaction_at", "updated_at"],
    aggregatable=["id", "message_count", "gravity"],
    groupable=["party_a", "party_b", "is_fading"],
    insert=False,
    update=False,
    delete=False,
)

_CADENCE_RESOURCE = hasura_model_resource(
    CadenceType,
    model=Cadence,
    name="cadences",
    filterable=["id", "party", "cadence_days", "touch_due_at"],
    sortable=["touch_due_at", "cadence_days", "updated_at"],
    aggregatable=["id"],
    groupable=["party", "cadence_days"],
    insertable=["party", "cadence_days"],
    updatable=["cadence_days"],
    field_id_decode={"party": public_pk_decoder(Party)},
    write_backend=AngeeHasuraWriteBackend(Cadence, public_id_fields=("party",)),
)

_NEXUS_SCHEMA_BUCKET = {
    "query": [NexusQuery, _TIE_RESOURCE.query, _CADENCE_RESOURCE.query],
    "mutation": [_TIE_RESOURCE.mutation, _CADENCE_RESOURCE.mutation],
    "types": [
        TieType,
        CadenceType,
        PartyTimelinePayload,
        PartyGraphPayload,
        NexusOverviewPayload,
        *_TIE_RESOURCE.types,
        *_CADENCE_RESOURCE.types,
    ],
    "type_extensions": [PartyNexusExtension],
}

schemas = {
    "public": {**_NEXUS_SCHEMA_BUCKET},
    "console": {
        **_NEXUS_SCHEMA_BUCKET,
        "subscription": [
            changes(Tie, field="tieChanged"),
            changes(Cadence, field="cadenceChanged"),
        ],
    },
}
