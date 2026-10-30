"""Native values for authored, extensible Strawberry action inputs."""

from dataclasses import dataclass
from typing import Any

import strawberry
from django.apps import apps
from strawberry.schema.schema_converter import GraphQLCoreConverter
from strawberry.types import get_object_definition

from angee.graphql.actions import authorized_permission_target
from graphql import GraphQLInputObjectType


@dataclass(frozen=True)
class InputReference:
    """Declare the model and permission of an input field's public references."""

    model: str
    permission: str = "read"

    def resolve(self, info: strawberry.Info, value: Any) -> Any:
        """Resolve nullable scalar or list references at the schema boundary."""

        if value is None:
            return None
        if isinstance(value, list):
            return [self.resolve(info, item) for item in value]
        return authorized_permission_target(info, apps.get_model(self.model), value, self.permission)


def input_values(info: strawberry.Info, value: Any) -> Any:
    """Convert composed input fields to native values using their declarations.

    Strawberry owns field merging and collision checks. Field metadata remains
    attached to donor fields, so the receiving addon never names its extensions.
    """

    if isinstance(value, list):
        return [input_values(info, item) for item in value]
    definition = get_object_definition(type(value))
    if definition is None or not definition.is_input:
        return value
    result = {}
    input_type = info.schema.schema_converter.type_map[definition.name].implementation
    assert isinstance(input_type, GraphQLInputObjectType)
    for graphql_field in input_type.fields.values():
        field = graphql_field.extensions[GraphQLCoreConverter.DEFINITION_BACKREF]
        item = getattr(value, field.python_name, strawberry.UNSET)
        if item is strawberry.UNSET:
            continue
        reference = field.metadata.get(InputReference)
        result[field.python_name] = reference.resolve(info, item) if reference else input_values(info, item)
    return result
