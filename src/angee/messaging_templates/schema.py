"""GraphQL schema contributions for message templates.

Exposed on the console. A message template is an ordinary resource (list,
detail, insert, update, delete); deleting an override is resetting it to its
shipped default. ``override_message_template`` starts the deployment's override
of a shipped default and ``keep_message_template_override`` keeps an override
over a default that changed; both return an ``ActionResult`` naming the override.
Both refuse as the framework's actions do: a signed-out caller gets the session's
GraphQL error, and a caller who may not create or write the override gets a
``PERMISSION_DENIED`` GraphQL error; an unreadable or missing row, a row that is
not a shipped default and an override of none are refused in band (``ok: false``,
with the codes ``not_a_baseline`` and ``no_baseline``). A row's
``is_effective``, ``is_overridden`` and ``has_updated_default`` are read from its
siblings and filter through the model's ``hasura_filter_expressions``.
``message_template_preview`` renders a row with its sample for a caller who may
preview it (``MessageTemplate.preview``), in the kit it names or the default one.
"""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models.expressions import BaseExpression
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target, session_user
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.messaging_templates.models import MessageTemplate as MessageTemplateModel
from angee.messaging_templates.models import MessageTemplateQuerySet
from angee.messaging_templates.rendering import RenderedMessage, TemplatePreview, TemplateWarning, TemplateWarningCode

MessageTemplate = apps.get_model("messaging_templates", "MessageTemplate")
BrandKit = apps.get_model("brandkit", "BrandKit")

MessageTemplateWarningCode = strawberry.enum(TemplateWarningCode, name="MessageTemplateWarningCode")
MessageTemplateWarningType = strawberry.type(TemplateWarning, name="MessageTemplateWarningType")
RenderedMessageType = strawberry.type(RenderedMessage, name="RenderedMessageType")
MessageTemplatePreviewType = strawberry.type(TemplatePreview, name="MessageTemplatePreviewType")


def _row_fact(name: str) -> BaseExpression:
    """Build one row fact for the list's annotation, from the queryset's one mapping of them."""

    return MessageTemplateQuerySet.row_fact_expressions[name](MessageTemplate._base_manager.all())


@strawberry_django.type(MessageTemplate)
class MessageTemplateType(AngeeNode):
    """Console projection of one message template; its hashes stay on the server."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["name"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    kind: auto
    key: auto
    language: auto
    name: auto
    description: auto
    format: auto
    subject: auto
    preheader: auto
    source: auto
    text_source: auto
    layout: auto
    variables: auto
    sample: JSON | None
    is_baseline: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(annotate={"_is_effective": lambda info: _row_fact("is_effective")})
    def is_effective(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this row is what renders for its kind, key and language."""

        return parent.is_effective

    @strawberry_django.field(annotate={"_is_overridden": lambda info: _row_fact("is_overridden")})
    def is_overridden(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this shipped default has the deployment's override."""

        return parent.is_overridden

    @strawberry_django.field(annotate={"_has_updated_default": lambda info: _row_fact("has_updated_default")})
    def has_updated_default(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this override's shipped default changed since the override started or was last kept."""

        return parent.has_updated_default


_MESSAGE_TEMPLATE_RESOURCE = hasura_model_resource(
    MessageTemplateType,
    model=MessageTemplate,
    name="message_templates",
    filterable=["id", "kind", "key", "language", "format", "is_baseline"],
    sortable=["kind", "key", "language", "name", "updated_at"],
    aggregatable=["id"],
    groupable=["kind", "language", "format", "is_baseline"],
    insertable=["kind", "key", "language", *MessageTemplate.edited_fields],
    updatable=list(MessageTemplate.edited_fields),
    write_backend=AngeeHasuraWriteBackend(MessageTemplate),
    id_column="sqid",
)


@strawberry.type
class MessageTemplateMutation:
    """Verbs that start from a shipped default."""

    @strawberry.mutation
    @action_guard("Override message template failed.")
    def override_message_template(self, info: strawberry.Info, template: PublicID) -> ActionResult:
        """Start the deployment's override of a shipped default, or return the one it has."""

        baseline = authorized_permission_target(info, MessageTemplate, template, "read")
        override = MessageTemplate.objects.override(baseline)
        return ActionResult(ok=True, message="Message template overridden.", id=override.sqid)

    @strawberry.mutation
    @action_guard("Keep message template override failed.")
    def keep_message_template_override(self, info: strawberry.Info, template: PublicID) -> ActionResult:
        """Keep an override's text over its shipped default as it is now, which clears ``has_updated_default``."""

        override = authorized_permission_target(info, MessageTemplate, template, "read")
        MessageTemplate.objects.keep_override(override)
        return ActionResult(ok=True, message="Override kept.", id=override.sqid)


@strawberry.type
class MessageTemplatePreviewQuery:
    """A message template rendered with its sample, for a caller who may preview it."""

    @strawberry.field
    def message_template_preview(
        self, info: strawberry.Info, template: PublicID, brand_kit: PublicID | None = None,
    ) -> TemplatePreview:
        """Render the row with its sample in the named kit, or the default one, and say what it warns about."""

        row = authorized_permission_target(info, MessageTemplate, template, "preview")
        kit = None if brand_kit is None else require_instance_for_id(
            BrandKit, brand_kit, queryset=BrandKit.objects.with_actor(session_user(info)),
        )
        return row.preview(kit=kit)


schemas = {
    "console": {
        "query": [_MESSAGE_TEMPLATE_RESOURCE.query, MessageTemplatePreviewQuery],
        "mutation": [MessageTemplateMutation, _MESSAGE_TEMPLATE_RESOURCE.mutation],
        "types": [MessageTemplateType, MessageTemplatePreviewType, *_MESSAGE_TEMPLATE_RESOURCE.types],
    },
}
