"""GraphQL schema contributions for brand kits.

Exposed on the console. A kit is an ordinary resource (list, detail, insert,
update, delete) whose logos are written by file public id; ``default_brand_kit``
reads the deployment's default kit; a kit's ``contrast_warnings`` project
:meth:`~angee.brandkit.models.BrandKit.contrast_warnings` for the registered
form's read widget.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.brandkit.models import MailRole, MailScheme
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.storage.schema import FileType

BrandKit = apps.get_model("brandkit", "BrandKit")

strawberry.enum(MailScheme)
strawberry.enum(MailRole)


@strawberry.type
class ContrastWarningType:
    """A mail role the kit's mail palette moved far from the colour it derives from, to keep it readable."""

    seed: str = strawberry.field(description="The kit's colour field the role derives from.")
    scheme: MailScheme
    role: MailRole


@strawberry_django.type(BrandKit)
class BrandKitType(AngeeNode):
    """Console projection of one brand kit."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["name"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    name: auto
    is_default: auto
    brand: auto
    accent: auto
    neutral: auto
    canvas: auto
    surface: auto
    rail: auto
    success: auto
    warning: auto
    danger: auto
    info: auto
    font_heading: auto
    font_body: auto
    logo: FileType | None = actor_scoped_to_one("logo")
    logo_dark: FileType | None = actor_scoped_to_one("logo_dark")
    footer_organization: auto
    footer_address: auto
    voice: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        only=["brand", "neutral", "canvas", "surface"],
        metadata={"angee_widget": "angee.brandkit.contrastWarnings"},
    )
    def contrast_warnings(self) -> list[ContrastWarningType]:
        """Return the mail roles moved far from their seeds to stay readable: advice for the form, never a refusal."""

        return cast(list[ContrastWarningType], cast(Any, self).contrast_warnings())


_BRAND_KIT_WRITABLE_FIELDS = (
    "name", "is_default",
    "brand", "accent", "neutral", "canvas", "surface", "rail", "success", "warning", "danger", "info",
    "font_heading", "font_body", "logo", "logo_dark", "footer_organization", "footer_address", "voice",
)

_BRAND_KIT_RESOURCE = hasura_model_resource(
    BrandKitType,
    model=BrandKit,
    name="brand_kits",
    filterable=["id", "name", "is_default"],
    sortable=["name", "is_default", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["is_default"],
    writable=list(_BRAND_KIT_WRITABLE_FIELDS),
    write_backend=AngeeHasuraWriteBackend(BrandKit, public_id_fields=("logo", "logo_dark")),
    id_column="sqid",
)


@strawberry.type
class BrandKitQuery:
    """Reads of the deployment's brand."""

    @strawberry.field(name="default_brand_kit")
    def default_brand_kit(self) -> BrandKitType | None:
        """Return the deployment's default brand kit, or ``None`` when no kit the actor reads is the default."""

        return cast("BrandKitType | None", BrandKit.objects.default())


schemas = {
    "console": {
        "query": [BrandKitQuery, _BRAND_KIT_RESOURCE.query],
        "mutation": [_BRAND_KIT_RESOURCE.mutation],
        "types": [BrandKitType, ContrastWarningType, *_BRAND_KIT_RESOURCE.types],
    },
}
