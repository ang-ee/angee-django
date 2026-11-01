"""Authorized values under the native InputReference metadata dispatch."""

from dataclasses import dataclass
from typing import Any

import strawberry
from django.apps import apps

from angee.graphql.inputs import InputReference
from angee.iam.graphql.actions import authorized_permission_target


@dataclass(frozen=True)
class AuthorizedInputReference(InputReference):
    permission: str = "read"

    def resolve_reference(self, info: strawberry.Info, value: Any) -> Any:
        return authorized_permission_target(info, apps.get_model(self.model), value, self.permission)
