"""Native IAM schema specialization over the shared GraphQL pipeline."""

from django.core.exceptions import ImproperlyConfigured
from graphql import GraphQLError
from rebac import MissingActorError, PermissionDenied
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.resources import model_resource_type

from angee.graphql.introspection import django_model
from angee.graphql.schema import AngeeSchema
from angee.iam.exceptions import classify_error
from angee.iam.graphql.view_as import ViewAsReadOnlyExtension
from angee.iam.permissions import require_rebac_queryset


class IAMSchema(AngeeSchema):
    classify_error = staticmethod(classify_error)
    optimizer_extension = RebacDjangoOptimizerExtension
    framework_extensions = (ViewAsReadOnlyExtension, RebacExtension)

    def __init__(self, *args, **kwargs):

        for surface in kwargs.get("types", ()):
            try:
                model = django_model(surface)
            except ImproperlyConfigured:
                continue
            if model_resource_type(model):
                require_rebac_queryset(model)
        super().__init__(*args, **kwargs)

    def _sanitize_unexpected_error(self, error: GraphQLError) -> None:
        """Keep the native expected-refusal message while sharing one sanitizer."""
        if isinstance(error.original_error, MissingActorError | PermissionDenied):
            return
        super()._sanitize_unexpected_error(error)
