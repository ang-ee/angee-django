"""Small grouped surfaces over the composed country-field owners."""

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode

Party = apps.get_model("parties", "Party")
Address = apps.get_model("parties", "Address")


@strawberry_django.type(Party)
class BillingPartyType(AngeeNode):
    tax_country: auto


@strawberry_django.type(Address)
class CountryAddressType(AngeeNode):
    country: auto


party_resource = hasura_model_resource(
    BillingPartyType, model=Party, name="billing_parties",
    filterable=["tax_country"], sortable=["tax_country"], groupable=["tax_country"],
    aggregatable=["id"], insert=False, update=False, delete=False,
)
address_resource = hasura_model_resource(
    CountryAddressType, model=Address, name="country_addresses",
    filterable=["country"], sortable=["country"], groupable=["country"],
    aggregatable=["id"], insert=False, update=False, delete=False,
)
schemas = {"blank_choices": {
    "query": [party_resource.query, address_resource.query],
    "types": [BillingPartyType, CountryAddressType, *party_resource.types, *address_resource.types],
}}
