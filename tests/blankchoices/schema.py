"""Small grouped surfaces over the composed country-field owners."""

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data.hasura import hasura_model_resource
from angee.iam.graphql.node import ProtectedNode

Party = apps.get_model("parties", "Party")
Address = apps.get_model("parties", "Address")


@strawberry_django.type(Party)
class BlankChoicePartyType(ProtectedNode):
    blank_choice_country: auto


@strawberry_django.type(Address)
class CountryAddressType(ProtectedNode):
    country: auto


party_resource = hasura_model_resource(
    BlankChoicePartyType, model=Party, name="blank_choice_parties",
    filterable=["blank_choice_country"], sortable=["blank_choice_country"], groupable=["blank_choice_country"],
    aggregatable=["id"], insert=False, update=False, delete=False,
)
address_resource = hasura_model_resource(
    CountryAddressType, model=Address, name="country_addresses",
    filterable=["country"], sortable=["country"], groupable=["country"],
    aggregatable=["id"], insert=False, update=False, delete=False,
)
schemas = {"blank_choices": {
    "query": [party_resource.query, address_resource.query],
    "types": [BlankChoicePartyType, CountryAddressType, *party_resource.types, *address_resource.types],
}}
