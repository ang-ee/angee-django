"""GraphQL schema contributions for Angee money.

Both models are exposed on the admin console only. Currency reads remain open to
authenticated actors; rate reads require their persisted shared or composed
context reader. Writes are admin-gated by ``permissions.zed`` and the resource
write backend. Console-only is the deliberate default: unlike ``parties``/``storage``
(which publish a ``public`` bucket for outward-facing directory/file reads), a
currency catalogue has no public consumer yet — a storefront that needs
public prices can add a ``public`` projection later. The ``currency`` foreign key
projects and accepts the related currency's public id, like uom's unit→category
relation.
"""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode

Currency = apps.get_model("money", "Currency")
CurrencyRate = apps.get_model("money", "CurrencyRate")


@strawberry_django.type(Currency)
class CurrencyType(AngeeNode):
    """Admin projection of one currency in the catalogue."""

    code: auto
    name: auto
    symbol: auto
    decimal_places: auto
    is_archived: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["code", "name"])
    def display_name(self) -> str:
        """Return the ISO code and catalogue name used by human choosers."""

        return f"{self.code} — {self.name}"


@strawberry_django.type(CurrencyRate)
class CurrencyRateType(AngeeNode):
    """Admin projection of one dated exchange rate, with its currency as a display node."""

    currency: CurrencyType
    reference_currency: CurrencyType | None
    date: auto
    rate: auto
    source_priority: auto
    record_model_label: auto
    record_public_id: auto
    is_archived: auto
    created_at: auto
    updated_at: auto


_CURRENCY_RESOURCE = hasura_model_resource(
    CurrencyType,
    model=Currency,
    name="currencies",
    filterable=["id", "code", "name", "is_archived"],
    sortable=["code", "name", "decimal_places", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["decimal_places", "is_archived"],
    writable=["code", "name", "symbol", "decimal_places", "is_archived"],
    id_column="sqid",
    record_representation="display_name",
    record_search_fields=("code", "name"),
)

_RATE_RESOURCE = hasura_model_resource(
    CurrencyRateType,
    model=CurrencyRate,
    name="currency_rates",
    filterable=[
        "id",
        "currency",
        "reference_currency",
        "date",
        "source_priority",
        "is_archived",
    ],
    sortable=["date", "source_priority", "rate", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["currency", "reference_currency", "source_priority", "is_archived"],
    writable=["currency", "date", "rate"],
    field_id_decode={"currency": public_pk_decoder(Currency)},
    write_backend=AngeeHasuraWriteBackend(CurrencyRate, public_id_fields=("currency",)),
    id_column="sqid",
)


schemas = {
    "console": {
        "query": [_CURRENCY_RESOURCE.query, _RATE_RESOURCE.query],
        "mutation": [_CURRENCY_RESOURCE.mutation, _RATE_RESOURCE.mutation],
        "types": [
            CurrencyType,
            CurrencyRateType,
            *_CURRENCY_RESOURCE.types,
            *_RATE_RESOURCE.types,
        ],
    },
}
