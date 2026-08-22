"""GraphQL resources and authored actions for portfolio strategy."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from django.apps import apps
from django.contrib.auth import get_user_model
from strawberry import auto

from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.projects.schema import ProjectType, TaskType

Product = apps.get_model("portfolio", "Product")
Initiative = apps.get_model("portfolio", "Initiative")
InitiativeProject = apps.get_model("portfolio", "InitiativeProject")
Update = apps.get_model("portfolio", "Update")
Release = apps.get_model("portfolio", "Release")
Project = apps.get_model("projects", "Project")
Task = apps.get_model("projects", "Task")
User = get_user_model()

UpdateHealthChoice = Update._meta.get_field("health").choices_enum
strawberry.enum(cast(Any, UpdateHealthChoice))


@strawberry_django.type(Product)
class ProductType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a phasal Product."""

    name: auto
    body: auto
    lifecycle: auto
    created_at: auto
    updated_at: auto

    originated_from: ProjectType | None = actor_scoped_to_one("originated_from")

    @strawberry_django.field(only=["owner_id"])
    def owner(self) -> strawberry.ID | None:
        """Return the owner's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).owner_id))


@strawberry_django.type(Initiative)
class InitiativeType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one strategic tree node."""

    name: auto
    body: auto
    status: auto
    health: auto
    health_updated_at: auto
    target_date: auto
    target_date_resolution: auto
    priority: auto
    sort_order: auto
    created_at: auto
    updated_at: auto

    parent: "InitiativeType | None" = actor_scoped_to_one("parent")

    @strawberry_django.field(only=["owner_id"])
    def owner(self) -> strawberry.ID | None:
        """Return the owner's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).owner_id))


@strawberry_django.type(InitiativeProject)
class InitiativeProjectType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of an ordered Initiative-to-Project placement."""

    sort_order: auto
    created_at: auto
    updated_at: auto

    initiative: InitiativeType | None = actor_scoped_to_one("initiative")
    project: ProjectType | None = actor_scoped_to_one("project")


@strawberry_django.type(Update)
class PortfolioUpdateType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one Project/Initiative health assertion."""

    health: auto
    body: auto
    created_at: auto
    updated_at: auto

    @strawberry.field
    def target_model(self) -> str:
        """Return the target's Django model label."""

        return cast(Any, self).record_model_label

    @strawberry.field
    def target_id(self) -> strawberry.ID:
        """Return the target's stable public id."""

        return cast(strawberry.ID, cast(Any, self).record_public_id)


@strawberry_django.type(Release)
class ReleaseType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a Product release artifact."""

    name: auto
    status: auto
    target_date: auto
    shipped_on: auto
    notes: auto
    created_at: auto
    updated_at: auto

    product: ProductType | None = actor_scoped_to_one("product")


@strawberry_django.type(Project, name="ProjectType", extend=True)
class ProjectPortfolioExtension:
    """Contribute portfolio placement and health onto ProjectType."""

    health: auto
    health_updated_at: auto
    priority: auto
    sort_order: auto
    product: ProductType | None = actor_scoped_to_one("product")


@strawberry_django.type(Task, name="TaskType", extend=True)
class TaskPortfolioExtension:
    """Contribute fixed-in release attribution onto TaskType."""

    release: ReleaseType | None = actor_scoped_to_one("release")


@strawberry.type
class PortfolioActionMutation:
    """Row-authorized maturation and health-report actions."""

    @strawberry.mutation
    @action_guard("Promote project to product failed.")
    def promote_project_to_product(
        self,
        info: strawberry.Info,
        id: PublicID,
    ) -> ActionResult:
        """Promote one writable Project while retaining its provenance."""

        target = authorized_action_target(info, Project, id, "write")
        product = target.promote_to_product()
        return ActionResult(ok=True, message="Project promoted to product.", id=product.sqid)

    @strawberry.mutation
    @action_guard("Report project update failed.")
    def report_project_update(
        self,
        info: strawberry.Info,
        id: PublicID,
        health: UpdateHealthChoice,  # type: ignore[valid-type]
        body: str,
    ) -> ActionResult:
        """Create one required-health report on a writable Project."""

        target_record = authorized_action_target(info, Project, id, "write")
        report = Update.objects.report(target=target_record, health=health, body=body)
        return ActionResult(ok=True, message="Project update reported.", id=report.sqid)

    @strawberry.mutation
    @action_guard("Report initiative update failed.")
    def report_initiative_update(
        self,
        info: strawberry.Info,
        id: PublicID,
        health: UpdateHealthChoice,  # type: ignore[valid-type]
        body: str,
    ) -> ActionResult:
        """Create one required-health report on a writable Initiative."""

        target_record = authorized_action_target(info, Initiative, id, "write")
        report = Update.objects.report(target=target_record, health=health, body=body)
        return ActionResult(ok=True, message="Initiative update reported.", id=report.sqid)


_PRODUCT_RESOURCE = hasura_model_resource(
    ProductType,
    model=Product,
    name="portfolio_products",
    filterable=[
        "id",
        "name",
        "owner",
        "lifecycle",
        "originated_from",
        "created_at",
        "updated_at",
    ],
    sortable=["name", "lifecycle", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["owner", "lifecycle"],
    insertable=["name", "body", "owner", "lifecycle"],
    updatable=["name", "body", "owner", "lifecycle"],
    field_id_decode={
        "owner": public_pk_decoder(User),
        "originated_from": public_pk_decoder(Project),
    },
    write_backend=AngeeHasuraWriteBackend(Product, public_id_fields=("owner",)),
)

_INITIATIVE_RESOURCE = hasura_model_resource(
    InitiativeType,
    model=Initiative,
    name="portfolio_initiatives",
    filterable=[
        "id",
        "parent",
        "name",
        "owner",
        "status",
        "health",
        "health_updated_at",
        "target_date",
        "target_date_resolution",
        "priority",
        "created_at",
        "updated_at",
    ],
    sortable=[
        "parent",
        "sort_order",
        "name",
        "status",
        "health",
        "health_updated_at",
        "target_date",
        "priority",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "priority", "sort_order"],
    groupable=[
        "parent",
        "owner",
        "status",
        "health",
        "target_date",
        "target_date_resolution",
        "priority",
    ],
    insertable=[
        "parent",
        "name",
        "body",
        "owner",
        "status",
        "target_date",
        "target_date_resolution",
        "priority",
        "sort_order",
    ],
    updatable=[
        "parent",
        "name",
        "body",
        "owner",
        "status",
        "target_date",
        "target_date_resolution",
        "priority",
        "sort_order",
    ],
    field_id_decode={
        "parent": public_pk_decoder(Initiative),
        "owner": public_pk_decoder(User),
    },
    write_backend=AngeeHasuraWriteBackend(
        Initiative,
        public_id_fields=("parent", "owner"),
    ),
)

_INITIATIVE_PROJECT_RESOURCE = hasura_model_resource(
    InitiativeProjectType,
    model=InitiativeProject,
    name="portfolio_initiative_projects",
    filterable=["id", "initiative", "project", "created_at", "updated_at"],
    sortable=["initiative", "project", "sort_order", "created_at", "updated_at"],
    aggregatable=["id", "sort_order"],
    groupable=["initiative", "project", "project__product"],
    insertable=["initiative", "project", "sort_order"],
    updatable=["initiative", "project", "sort_order"],
    field_id_decode={
        "initiative": public_pk_decoder(Initiative),
        "project": public_pk_decoder(Project),
    },
    write_backend=AngeeHasuraWriteBackend(
        InitiativeProject,
        public_id_fields=("initiative", "project"),
    ),
)

_UPDATE_RESOURCE = hasura_model_resource(
    PortfolioUpdateType,
    model=Update,
    name="portfolio_updates",
    filterable=["id", "health", "created_at", "updated_at"],
    sortable=["health", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["health"],
    insert=False,
    updatable=["health", "body"],
    write_backend=AngeeHasuraWriteBackend(Update),
)

_RELEASE_RESOURCE = hasura_model_resource(
    ReleaseType,
    model=Release,
    name="portfolio_releases",
    filterable=[
        "id",
        "product",
        "name",
        "status",
        "target_date",
        "shipped_on",
        "created_at",
        "updated_at",
    ],
    sortable=[
        "product",
        "name",
        "status",
        "target_date",
        "shipped_on",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id"],
    groupable=["product", "status", "target_date", "shipped_on"],
    insertable=["product", "name", "status", "target_date", "shipped_on", "notes"],
    updatable=["product", "name", "status", "target_date", "shipped_on", "notes"],
    field_id_decode={"product": public_pk_decoder(Product)},
    write_backend=AngeeHasuraWriteBackend(Release, public_id_fields=("product",)),
)

_RESOURCE_TYPES = [
    *_PRODUCT_RESOURCE.types,
    *_INITIATIVE_RESOURCE.types,
    *_INITIATIVE_PROJECT_RESOURCE.types,
    *_UPDATE_RESOURCE.types,
    *_RELEASE_RESOURCE.types,
]

_PORTFOLIO_SCHEMA_BUCKET = {
    "query": [
        _PRODUCT_RESOURCE.query,
        _INITIATIVE_RESOURCE.query,
        _INITIATIVE_PROJECT_RESOURCE.query,
        _UPDATE_RESOURCE.query,
        _RELEASE_RESOURCE.query,
    ],
    "mutation": [
        PortfolioActionMutation,
        _PRODUCT_RESOURCE.mutation,
        _INITIATIVE_RESOURCE.mutation,
        _INITIATIVE_PROJECT_RESOURCE.mutation,
        _UPDATE_RESOURCE.mutation,
        _RELEASE_RESOURCE.mutation,
    ],
    "types": [
        ProductType,
        InitiativeType,
        InitiativeProjectType,
        PortfolioUpdateType,
        ReleaseType,
        ProjectType,
        TaskType,
        *_RESOURCE_TYPES,
    ],
    "type_extensions": [ProjectPortfolioExtension, TaskPortfolioExtension],
}

schemas = {
    "public": {**_PORTFOLIO_SCHEMA_BUCKET},
    "console": {
        **_PORTFOLIO_SCHEMA_BUCKET,
        "subscription": [
            changes(Product, field="portfolioProductChanged"),
            changes(Initiative, field="portfolioInitiativeChanged"),
            changes(InitiativeProject, field="portfolioInitiativeProjectChanged"),
            changes(Update, field="portfolioUpdateChanged"),
            changes(Release, field="portfolioReleaseChanged"),
        ],
    },
}
