"""Tests for parts-merge GraphQL schema composition."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import strawberry
import strawberry_django
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django_choices_field import IntegerChoicesField
from graphql import GraphQLEnumType, GraphQLObjectType, get_named_type
from rebac import MissingActorError, PermissionDenied, RebacMixin, SubjectRef
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.extensions import SchemaExtension

from angee.base.fields import StateField
from angee.base.mixins import RevisionMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.graphql.data import hasura as hasura_data
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.revisions import revisions
from angee.graphql.schema import (
    DEFAULT_SCHEMA_NAME,
    SCHEMA_PART_KEYS,
    GraphQLSchemas,
)
from tests.conftest import make_addon


@strawberry.type
class HelloQuery:
    @strawberry.field
    def hello(self) -> str:
        return "hi"


@strawberry.type
class WorldQuery:
    @strawberry.field
    def world(self) -> str:
        return "world"


@strawberry.type
class PingMutation:
    @strawberry.mutation
    def ping(self) -> str:
        return "pong"


class CustomExtension(SchemaExtension):
    """Sentinel extension contributed by an addon."""


class ManagedThing(RebacMixin):
    """Concrete REBAC model with the library manager intact."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the managed test model."""

        app_label = "tests"
        rebac_resource_type = "tests/managed"


class GatedWriteThingManager(AngeeManager):
    """Test manager recording create preflight relationships."""

    checked_relationships: dict[str, tuple[Any, ...]] | None = None

    def check_create(self, relationships: dict[str, tuple[Any, ...]] | None = None) -> SubjectRef:
        """Record the relationship map and return the verified actor."""

        self.checked_relationships = dict(relationships or {})
        return SubjectRef.of("auth/user", "verified")


class GatedWriteThing(AngeeModel):
    """Concrete Angee model used to exercise Hasura write authorization."""

    owner = models.ForeignKey(ManagedThing, on_delete=models.CASCADE)
    name = models.CharField(max_length=32, blank=True)

    objects = GatedWriteThingManager()

    class Meta:
        """Django model options for the gated write test model."""

        app_label = "tests"
        rebac_resource_type = "tests/gated-write"


class UngatedWriteThing(AngeeModel):
    """Concrete Angee model whose manager is missing the create gate."""

    objects = models.Manager()
    name = models.CharField(max_length=32, blank=True)

    class Meta:
        """Django model options for the ungated write test model."""

        app_label = "tests"
        rebac_resource_type = "tests/ungated-write"


class UnmanagedThing(RebacMixin):
    """Concrete REBAC model with an unsafe default manager."""

    objects = models.Manager()
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the unmanaged test model."""

        app_label = "tests"
        rebac_resource_type = "tests/unmanaged"


class WorkflowItem(models.Model):
    """Model exposing a choice enum through strawberry-django."""

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In Review"

    class Priority(models.IntegerChoices):
        LOW = 1, "Low"
        HIGH = 2, "High Priority"

    state = StateField(choices_enum=State)
    priority = IntegerChoicesField(choices_enum=Priority)

    class Meta:
        """Django model options for the enum-label test model."""

        app_label = "tests"


class RevisionEntry(RevisionMixin, models.Model):
    """Concrete model exposing versioned body snapshots in GraphQL tests."""

    revisioned_fields = ("body",)

    title = models.CharField(max_length=32)
    body = models.TextField(blank=True, default="")

    class Meta:
        """Django model options for the revision query test model."""

        app_label = "tests"


class FieldGatedRevisionEntry(RevisionMixin, models.Model):
    """Revisioned model with a field-gated value that must not be exposed."""

    revisioned_fields = ("secret",)

    secret = models.TextField(blank=True, default="")

    class Meta:
        """Django model options for the field-gated revision guard model."""

        app_label = "tests"


class RelatedRevisionEntry(RevisionMixin, models.Model):
    """Revisioned model declaring a relation field, which cannot round-trip."""

    revisioned_fields = ("owner",)

    owner = models.ForeignKey(WorkflowItem, on_delete=models.CASCADE)

    class Meta:
        """Django model options for the relation guard test model."""

        app_label = "tests"


@strawberry.type
class ThingType:
    """Plain surface reused across named schemas."""

    id: strawberry.ID


@strawberry.type
class NodeQuery:
    """Query exposing a field surface that two schemas share."""

    @strawberry.field
    def thing(self) -> ThingType | None:
        return None


@strawberry.type
class DenialQuery:
    @strawberry.field
    def missing_actor(self) -> str:
        raise MissingActorError("missing actor")

    @strawberry.field
    def permission_denied(self) -> str:
        raise PermissionDenied("denied")


@strawberry.type
class ValidationQuery:
    @strawberry.field
    def field_errors(self) -> str:
        raise ValidationError(
            {
                "display_name": ["This field cannot be blank."],
                "client_id": ["This field cannot be blank."],
                "__all__": ["Provider is misconfigured."],
            }
        )

    @strawberry.field
    def plain_error(self) -> str:
        raise ValidationError("Something went wrong.")


def test_hasura_write_backend_decodes_public_relations_through_write_queryset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relation public IDs in writes resolve through the actor-scoped write owner."""

    sentinel_queryset = object()
    related = SimpleNamespace(pk=7)
    calls: dict[str, Any] = {}

    def fake_write_queryset(model: type[models.Model]) -> object:
        calls["write_model"] = model
        return sentinel_queryset

    def fake_instance_from_public_id(
        model: type[models.Model],
        value: str,
        *,
        queryset: object | None = None,
    ) -> object:
        calls["decode"] = (model, value, queryset)
        return related

    monkeypatch.setattr(hasura_data, "write_queryset", fake_write_queryset)
    monkeypatch.setattr(hasura_data, "instance_from_public_id", fake_instance_from_public_id)

    backend = AngeeHasuraWriteBackend(GatedWriteThing, public_id_fields=("owner",))

    assert backend._decode_public_id_fields({"owner": "pub-owner", "name": "Row"}) == {
        "owner_id": related.pk,
        "name": "Row",
    }
    assert calls == {
        "write_model": ManagedThing,
        "decode": (ManagedThing, "pub-owner", sentinel_queryset),
    }


def test_hasura_write_backend_create_uses_manager_create_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hasura create preflights relationship-conditioned create rules on the manager."""

    related = SimpleNamespace(pk=7)
    manager = GatedWriteThing._default_manager
    manager.checked_relationships = None
    created: dict[str, Any] = {}

    def fake_instance_from_public_id(
        model: type[models.Model],
        value: str,
        *,
        queryset: object | None = None,
    ) -> object:
        del model, value, queryset
        return related

    def fake_create(
        info: Any,
        model: type[models.Model],
        data: dict[str, Any],
        **kwargs: Any,
    ) -> models.Model:
        del info
        assert model is GatedWriteThing
        assert data == {"owner_id": related.pk, "name": "Row"}
        pre_save_hook = kwargs["pre_save_hook"]
        instance = model(owner_id=data["owner_id"], name=data["name"])
        pre_save_hook(instance)
        assert instance.is_sudo()
        created["row"] = instance
        return instance

    monkeypatch.setattr(hasura_data, "instance_from_public_id", fake_instance_from_public_id)
    monkeypatch.setattr(hasura_data.mutation_resolvers, "create", fake_create)

    backend = AngeeHasuraWriteBackend(GatedWriteThing, public_id_fields=("owner",))

    result = backend.create(cast(Any, SimpleNamespace()), {"owner": "pub-owner", "name": "Row"})

    assert result is created["row"]
    assert manager.checked_relationships == {"owner": (related,)}
    assert result.actor() == SubjectRef.of("auth/user", "verified")
    assert not result.is_sudo()


def test_hasura_write_backend_create_requires_gate_for_angee_rebac_models() -> None:
    """Angee REBAC models fail closed when their manager lacks ``check_create``."""

    backend = AngeeHasuraWriteBackend(UngatedWriteThing)

    with pytest.raises(ImproperlyConfigured, match="check_create"):
        backend.create(cast(Any, SimpleNamespace()), {"name": "unguarded"})


@strawberry.type
class ManagedThingType:
    """GraphQL type exposing a safely managed REBAC model."""

    name: str
    __strawberry_django_definition__ = type(
        "DjangoDefinition",
        (),
        {"model": ManagedThing},
    )()


@strawberry.type
class UnmanagedThingType:
    """GraphQL type exposing an unsafely managed REBAC model."""

    name: str
    __strawberry_django_definition__ = type(
        "DjangoDefinition",
        (),
        {"model": UnmanagedThing},
    )()


@strawberry_django.type(WorkflowItem)
class WorkflowItemType:
    """GraphQL type exposing workflow choice enums."""

    state: strawberry.auto
    priority: strawberry.auto


@strawberry_django.type(RevisionEntry)
class RevisionEntryType:
    """GraphQL type exposing a revision-tracked model."""

    title: strawberry.auto
    body: strawberry.auto


@strawberry_django.type(FieldGatedRevisionEntry)
class FieldGatedRevisionEntryType:
    """GraphQL type exposing the field-gated revision guard model."""

    secret: strawberry.auto


@strawberry_django.type(RelatedRevisionEntry)
class RelatedRevisionEntryType:
    """GraphQL type exposing the relation revision guard model."""

    owner: strawberry.auto


def _parts(**buckets: list) -> dict[str, tuple]:
    """Return a raw schema declaration with all known buckets."""

    return {key: tuple(buckets.get(key, ())) for key in SCHEMA_PART_KEYS}


def _module(name: str) -> ModuleType:
    """Return a synthetic module with a Django app filesystem path."""

    module = ModuleType(name)
    module.__file__ = __file__
    return module


def addon(**name_to_parts: dict[str, list]) -> AppConfig:
    """Build a fake addon contributing parts to one or more schema names."""

    schemas = {name: _parts(**parts) for name, parts in name_to_parts.items()}
    return make_addon(schemas=schemas)


def test_collect_folds_addons_in_order() -> None:
    """Parts for one name accumulate across addons, deterministically."""

    first = addon(public={"query": [HelloQuery]})
    second = addon(public={"query": [WorldQuery]}, console={"query": [HelloQuery]})

    schemas = GraphQLSchemas([first, second])
    collected = schemas.parts

    assert collected["public"].query == (HelloQuery, WorldQuery)
    assert set(collected) == {"public", "console"}
    assert schemas.names() == ("console", "public")


def test_collect_dedupes_by_identity() -> None:
    """A surface contributed twice is folded once."""

    collected = GraphQLSchemas(
        [
            addon(public={"query": [HelloQuery]}),
            addon(public={"query": [HelloQuery]}),
        ]
    ).parts

    assert collected["public"].query == (HelloQuery,)


@strawberry.type
class Widget:
    """Upstream type that a downstream addon extends (module-scope for resolution)."""

    name: str


@strawberry.type(name="Widget", extend=True)
class WidgetExtra:
    """Downstream donor contributing ``extra`` onto :class:`Widget`."""

    extra: int


@strawberry.type
class WidgetQuery:
    @strawberry.field
    def widget(self) -> Widget:
        return Widget(name="x")


@strawberry.input
class InputExtensionBaseInput:
    """Upstream input for additive input-extension tests."""

    name: str


@strawberry.input(name="InputExtensionBaseInput", extend=True)
class InputExtensionFirstInput:
    """First downstream input donor."""

    first: str = ""


@strawberry.input(name="InputExtensionBaseInput", extend=True)
class InputExtensionSecondInput:
    """Second downstream input donor."""

    second: str = ""


@strawberry.type
class InputExtensionMutation:
    """Mutation that receives the upstream input after donors are merged."""

    @strawberry.mutation
    def save_input(self, data: InputExtensionBaseInput) -> str:
        return f"{data.name}:{getattr(data, 'first', '')}:{getattr(data, 'second', '')}"


@strawberry.input
class InputExtensionCollisionBaseInput:
    """Upstream input for collision tests."""

    name: str


@strawberry.input(name="InputExtensionCollisionBaseInput", extend=True)
class InputExtensionCollisionFirstInput:
    """First donor defining the colliding field."""

    shared: str = ""


@strawberry.input(name="InputExtensionCollisionBaseInput", extend=True)
class InputExtensionCollisionSecondInput:
    """Second donor defining the colliding field."""

    shared: int = 0


@strawberry.type
class InputExtensionCollisionMutation:
    """Mutation that anchors the collision-test input in the schema."""

    @strawberry.mutation
    def save_input(self, data: InputExtensionCollisionBaseInput) -> str:
        return data.name


def test_type_extension_merges_downstream_fields_onto_upstream_type() -> None:
    """A downstream ``type_extensions`` donor adds its fields to the upstream type."""

    schema = GraphQLSchemas(
        [
            addon(public={"query": [WidgetQuery], "types": [Widget]}),
            addon(public={"type_extensions": [WidgetExtra]}),
        ]
    ).build("public")

    block = schema.as_str()
    assert "name: String!" in block
    assert "extra: Int!" in block


def test_type_extension_is_idempotent_across_collections() -> None:
    """The same donor applied by a second collection does not re-add or error."""

    for _ in range(2):
        schema = GraphQLSchemas(
            [
                addon(public={"query": [WidgetQuery], "types": [Widget]}),
                addon(public={"type_extensions": [WidgetExtra]}),
            ]
        ).build("public")
    sdl = schema.as_str()
    # Exactly one `extra` field — the second collection skipped re-adding it.
    assert sdl.count("extra: Int!") == 1


def test_type_extension_rejects_field_collision() -> None:
    """A donor field already declared on the target fails fast."""

    @strawberry.type
    class Conflict:
        shared: str

    @strawberry.type(name="Conflict", extend=True)
    class ConflictExtra:
        shared: int

    @strawberry.type
    class ConflictQuery:
        @strawberry.field
        def conflict(self) -> Conflict:
            return Conflict(shared="x")

    with pytest.raises(TypeError, match="duplicate extension field"):
        GraphQLSchemas(
            [
                addon(public={"query": [ConflictQuery], "types": [Conflict]}),
                addon(public={"type_extensions": [ConflictExtra]}),
            ]
        ).build("public")


def test_input_extension_merges_multiple_donors() -> None:
    """Several downstream input donors add fields to one upstream input."""

    schema = GraphQLSchemas(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "mutation": [InputExtensionMutation],
                    "input_extensions": [InputExtensionFirstInput],
                }
            ),
            addon(public={"input_extensions": [InputExtensionSecondInput]}),
        ]
    ).build("public")

    sdl = schema.as_str()
    assert "first: String!" in sdl
    assert "second: String!" in sdl
    result = schema.execute_sync(
        """
        mutation {
          save_input(data: {name: "base", first: "one", second: "two"})
        }
        """
    )
    assert result.errors is None
    assert result.data == {"save_input": "base:one:two"}


def test_input_extension_rejects_field_collision() -> None:
    """Two input donors adding the same field to one base fail fast."""

    with pytest.raises(TypeError, match="duplicate extension field"):
        GraphQLSchemas(
            [
                addon(
                    public={
                        "query": [HelloQuery],
                        "mutation": [InputExtensionCollisionMutation],
                        "input_extensions": [InputExtensionCollisionFirstInput],
                    }
                ),
                addon(public={"input_extensions": [InputExtensionCollisionSecondInput]}),
            ]
        ).build("public")


def test_build_schema_merges_query_surfaces() -> None:
    """Query surfaces from several addons merge into one root."""

    from angee.graphql.schema import AngeeSchema

    schema = GraphQLSchemas(
        [
            addon(public={"query": [HelloQuery]}),
            addon(public={"query": [WorldQuery]}),
        ]
    ).build("public")

    result = schema.execute_sync("{ hello world }")

    assert result.errors is None
    assert result.data == {"hello": "hi", "world": "world"}
    assert isinstance(schema, AngeeSchema)


def test_build_schema_installs_universal_rebac_extensions() -> None:
    """REBAC brackets every schema while addon extensions keep their slot."""

    schema = GraphQLSchemas(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "extensions": [CustomExtension],
                }
            )
        ]
    ).build("public")

    assert schema.extensions == (
        RebacExtension,
        CustomExtension,
        RebacDjangoOptimizerExtension,
    )


def test_denial_errors_get_graphql_codes() -> None:
    """REBAC denials surface with stable GraphQL error codes."""

    schema = GraphQLSchemas([addon(public={"query": [DenialQuery]})]).build("public")

    missing_actor = schema.execute_sync("{ missing_actor }")
    denied = schema.execute_sync("{ permission_denied }")

    assert missing_actor.errors is not None
    assert denied.errors is not None
    missing_actor_extensions = missing_actor.errors[0].extensions
    denied_extensions = denied.errors[0].extensions
    assert missing_actor_extensions is not None
    assert denied_extensions is not None
    assert missing_actor_extensions["code"] == "UNAUTHENTICATED"
    assert denied_extensions["code"] == "PERMISSION_DENIED"


def test_validation_errors_surface_per_field_extensions() -> None:
    """Django model validation surfaces as camel-cased per-field extensions."""

    schema = GraphQLSchemas([addon(public={"query": [ValidationQuery]})]).build("public")

    field_result = schema.execute_sync("{ field_errors }")
    plain_result = schema.execute_sync("{ plain_error }")

    assert field_result.errors is not None
    extensions = field_result.errors[0].extensions
    assert extensions is not None
    assert extensions["code"] == "VALIDATION"
    assert extensions["validationErrors"] == {
        "displayName": ["This field cannot be blank."],
        "clientId": ["This field cannot be blank."],
    }
    assert extensions["formErrors"] == ["Provider is misconfigured."]

    # A non-dict ValidationError carries only a form-level message.
    assert plain_result.errors is not None
    plain_extensions = plain_result.errors[0].extensions
    assert plain_extensions is not None
    assert plain_extensions["validationErrors"] == {}
    assert plain_extensions["formErrors"] == ["Something went wrong."]


def test_graphql_identity_exports_public_node() -> None:
    """The framework exposes one public node seam."""

    from angee.graphql.node import AngeeNode

    definition = strawberry.types.get_object_definition(AngeeNode)
    assert definition is not None
    assert definition.name == "Node"


def test_rebac_graphql_types_require_rebac_default_manager() -> None:
    """GraphQL-exposed REBAC models fail fast without the library manager."""

    assert isinstance(ManagedThing._default_manager, RebacManager)
    GraphQLSchemas(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "types": [ManagedThingType],
                }
            )
        ]
    ).build("public")

    with pytest.raises(ImproperlyConfigured, match="RebacManager"):
        GraphQLSchemas(
            [
                addon(
                    public={
                        "query": [HelloQuery],
                        "types": [UnmanagedThingType],
                    }
                )
            ]
        ).build("public")


def test_choice_enum_value_descriptions_come_from_django_labels() -> None:
    """Django choice labels are copied onto Strawberry enum values before build."""

    schema = GraphQLSchemas(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "types": [WorkflowItemType],
                }
            )
        ]
    ).build("public")

    item_type = schema._schema.get_type("WorkflowItemType")
    assert isinstance(item_type, GraphQLObjectType)
    state_enum = get_named_type(item_type.fields["state"].type)
    assert isinstance(state_enum, GraphQLEnumType)
    assert state_enum.values["DRAFT"].description == "Draft"
    assert state_enum.values["IN_REVIEW"].description == "In Review"
    priority_enum = get_named_type(item_type.fields["priority"].type)
    assert isinstance(priority_enum, GraphQLEnumType)
    assert priority_enum.values["LOW"].description == "Low"
    assert priority_enum.values["HIGH"].description == "High Priority"


def test_state_field_accepts_graphql_enum_member_names() -> None:
    """StateField owns enum-name to stored-value normalization."""

    field = WorkflowItem._meta.get_field("state")

    assert field.to_python("DRAFT") == WorkflowItem.State.DRAFT
    assert field.to_python("IN_REVIEW") == WorkflowItem.State.IN_REVIEW
    assert field.to_python("draft") == WorkflowItem.State.DRAFT
    with pytest.raises(ValidationError):
        field.to_python("MISSING")


def test_revisions_query_surface_exposes_revision_mixin_versions() -> None:
    """A generated revision query replaces consumer-authored resolvers.

    Asserts the emitted schema surface — the bounded query field and its
    revisioned-field projection. The runtime data path (public id -> instance ->
    newest-first projection) is covered end-to-end against the real, reversion-
    registered notes.Note in examples/notes-angee/e2e (notes-form-interactions
    "the Activity tab renders the revision timeline"); reversion cannot resolve
    a ContentType for a schema_editor-created throwaway model, so the version
    round-trip is not exercised as a unit test.
    """

    surface = revisions(RevisionEntryType, name="revision_entry")
    schema = GraphQLSchemas(
        [
            addon(
                public={
                    "query": [HelloQuery, surface],
                    "types": [RevisionEntryType],
                }
            )
        ]
    ).build("public")

    query_type = schema._schema.query_type
    assert query_type is not None
    field = query_type.fields["revision_entry_revisions"]
    # Bounded surface: addressed by id, capped by a `first` argument.
    assert set(field.args) == {"id", "first"}
    projection = get_named_type(field.type)
    assert isinstance(projection, GraphQLObjectType)
    # Projects the model's revisioned field plus the revision metadata.
    assert {"body", "created_at", "comment"} <= set(projection.fields)


def test_revisions_rejects_field_gated_revision_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revision snapshots cannot expose fields hidden by field-level read rules."""

    monkeypatch.setattr(
        "angee.graphql.access.gated_read_fields",
        lambda model: frozenset({"secret", "visible"}),
    )

    with pytest.raises(ImproperlyConfigured, match=r"revisioned_fields.*secret"):
        revisions(FieldGatedRevisionEntryType, name="field_gated_revision_entry")


def test_revisions_rejects_relation_revision_fields() -> None:
    """Revision snapshots only expose concrete value fields."""

    with pytest.raises(ImproperlyConfigured, match=r"relation field 'owner'"):
        revisions(RelatedRevisionEntryType, name="related_revision_entry")


def test_build_schema_includes_mutation_root() -> None:
    """A mutation bucket becomes the schema mutation root."""

    schema = GraphQLSchemas([addon(public={"query": [HelloQuery], "mutation": [PingMutation]})]).build(
        "public"
    )

    result = schema.execute_sync("mutation { ping }")

    assert result.errors is None
    assert result.data == {"ping": "pong"}


def test_render_sdl_prints_each_schema() -> None:
    """Named schemas render to deterministic SDL strings."""

    rendered = GraphQLSchemas(
        [
            addon(public={"query": [HelloQuery]}),
            addon(console={"query": [WorldQuery]}),
        ]
    ).render_sdl()

    assert set(rendered) == {"public", "console"}
    assert "hello: String!" in rendered["public"]
    assert "world: String!" in rendered["console"]


def test_surface_shared_across_named_schemas_renders() -> None:
    """A query surface reused in two schemas keeps independent fields.

    Field extensions may mutate their field in place when a schema is built; a
    surface contributed to more than one named schema must not hand the same
    field object to two builds.
    """

    rendered = GraphQLSchemas(
        [addon(public={"query": [NodeQuery]}, console={"query": [NodeQuery]})]
    ).render_sdl()

    assert "thing: ThingType" in rendered["public"]
    assert "thing: ThingType" in rendered["console"]


def test_build_schema_unknown_name_lists_available() -> None:
    """An unknown schema name reports the names that do exist."""

    with pytest.raises(ImproperlyConfigured, match="available schemas"):
        GraphQLSchemas([addon(public={"query": [HelloQuery]})]).build("console")


def test_build_schema_requires_query_root() -> None:
    """A schema without any query contribution fails fast."""

    with pytest.raises(ImproperlyConfigured, match="no query root"):
        GraphQLSchemas([addon(public={"mutation": [PingMutation]})]).build("public")


def test_merge_root_field_collision() -> None:
    """Two surfaces claiming the same root field fail fast."""

    @strawberry.type
    class OtherHello:
        @strawberry.field
        def hello(self) -> str:
            return "shadow"

    with pytest.raises(ImproperlyConfigured, match="contributed by both"):
        GraphQLSchemas(
            [
                addon(public={"query": [HelloQuery]}),
                addon(public={"query": [OtherHello]}),
            ]
        ).build("public")


def test_default_schema_name_is_public() -> None:
    """The default live schema remains the public schema."""

    assert DEFAULT_SCHEMA_NAME == "public"


def test_empty_addons_contribute_no_schemas() -> None:
    """No contributions means no names and no parts."""

    empty: list[Any] = []
    schemas = GraphQLSchemas(empty)
    assert schemas.parts == {}
    assert schemas.names() == ()
