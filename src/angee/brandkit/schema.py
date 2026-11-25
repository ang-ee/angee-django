"""GraphQL schema contributions for brand kits.

On the console, a kit is an ordinary resource (list, detail, insert, update,
delete); its logos are read there and written by no console root, since only
storage's ``FileManager.ingest_image`` puts files on the image drive a logo must
come from. A kit's ``contrast_warnings`` project
:meth:`~angee.brandkit.models.BrandKit.contrast_warnings` for the registered
form's read widget.

On the public schema, ``default_brand_kit_colors`` reads the default kit's ten
colours, and nothing else of any kit, for every caller signed in or not: the
``read_colors`` disclosure in ``permissions.zed``.
"""

from __future__ import annotations

from typing import cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.brandkit.models import BrandKit as BrandKitModel
from angee.brandkit.models import ContrastWarning, MailRole, MailScheme, MailSeed
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.storage.schema import FileType

BrandKit = apps.get_model("brandkit", "BrandKit")

strawberry.enum(MailScheme)
strawberry.enum(MailRole)
strawberry.enum(MailSeed)
ContrastWarningType = strawberry.type(ContrastWarning, name="ContrastWarningType")


@strawberry_django.type(BrandKit, fields=list(BrandKitModel.THEME_COLORS))
class BrandKitType(AngeeNode):
    """Console projection of one brand kit; its colours are ``BrandKit.THEME_COLORS``."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["name"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    name: auto
    is_default: auto
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
    def contrast_warnings(parent: strawberry.Parent[BrandKitModel]) -> list[ContrastWarning]:
        """Return the mail roles moved far from their seeds to stay readable: advice for the form, never a refusal."""

        return parent.contrast_warnings()


_BRAND_KIT_WRITABLE_FIELDS = (
    "name", "is_default", *BrandKitModel.THEME_COLORS,
    "font_heading", "font_body", "footer_organization", "footer_address", "voice",
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
    write_backend=AngeeHasuraWriteBackend(BrandKit),
    id_column="sqid",
)


@strawberry_django.type(BrandKit, name="BrandKitColors", fields=list(BrandKitModel.THEME_COLORS))
class BrandKitColorsType:
    """A kit's colours, ``BrandKit.THEME_COLORS`` and no other column, as everyone reads the default kit's."""


@strawberry.type
class BrandKitPublicQuery:
    """The default kit's colours, for every page a theme dresses, signed in or not."""

    @strawberry.field(name="default_brand_kit_colors")
    def default_brand_kit_colors(self) -> BrandKitColorsType | None:
        """Return the default kit's colours under the ``read_colors`` disclosure, or ``None`` with no default kit."""

        kits = BrandKit.objects.with_action("read_colors").only(*BrandKitModel.THEME_COLORS)
        return cast("BrandKitColorsType | None", kits.default())


schemas = {
    "public": {
        "query": [BrandKitPublicQuery],
        "types": [BrandKitColorsType],
    },
    "console": {
        "query": [_BRAND_KIT_RESOURCE.query],
        "mutation": [_BRAND_KIT_RESOURCE.mutation],
        "types": [BrandKitType, ContrastWarningType, *_BRAND_KIT_RESOURCE.types],
    },
}
