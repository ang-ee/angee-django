"""GraphQL schema contributions for message templates.

Exposed on the console. A message template is an ordinary resource (list,
detail, insert, update, delete); deleting an override is resetting it to its
shipped default. ``override_message_template`` starts the deployment's override
of a shipped default and ``keep_message_template_override`` keeps an override
over a default that changed; both return an ``ActionResult`` naming the override.
A row's ``is_effective``, ``is_overridden`` and ``default_updated`` are read from
its siblings and filter through the model's ``hasura_filter_expressions``.
"""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.messaging_templates.models import MessageTemplate as MessageTemplateModel
from angee.messaging_templates.models import MessageTemplateQuerySet

MessageTemplate = apps.get_model("messaging_templates", "MessageTemplate")


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

    @strawberry_django.field(
        annotate={"_is_effective": lambda info: MessageTemplateQuerySet.row_facts(MessageTemplate)["is_effective"]},
    )
    def is_effective(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this row is what renders for its kind, key and language."""

        return parent.is_effective

    @strawberry_django.field(
        annotate={"_is_overridden": lambda info: MessageTemplateQuerySet.row_facts(MessageTemplate)["is_overridden"]},
    )
    def is_overridden(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this shipped default has the deployment's override."""

        return parent.is_overridden

    @strawberry_django.field(
        annotate={
            "_default_updated": lambda info: MessageTemplateQuerySet.row_facts(MessageTemplate)["default_updated"],
        },
    )
    def default_updated(parent: strawberry.Parent[MessageTemplateModel]) -> bool:
        """Whether this override's shipped default changed since it started or was last kept."""

        return parent.default_updated


_MESSAGE_TEMPLATE_UPDATABLE_FIELDS = (
    "name", "description", "format", "subject", "preheader", "source", "text_source", "layout", "variables", "sample",
)

_MESSAGE_TEMPLATE_RESOURCE = hasura_model_resource(
    MessageTemplateType,
    model=MessageTemplate,
    name="message_templates",
    filterable=["id", "kind", "key", "language", "format", "is_baseline"],
    sortable=["kind", "key", "language", "name", "updated_at"],
    aggregatable=["id"],
    groupable=["kind", "language", "format", "is_baseline"],
    insertable=["kind", "key", "language", *_MESSAGE_TEMPLATE_UPDATABLE_FIELDS],
    updatable=list(_MESSAGE_TEMPLATE_UPDATABLE_FIELDS),
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
        """Keep an override's text over its shipped default as it is now, which clears "default updated"."""

        override = authorized_permission_target(info, MessageTemplate, template, "write")
        MessageTemplate.objects.keep_override(override)
        return ActionResult(ok=True, message="Override kept.", id=override.sqid)


schemas = {
    "console": {
        "query": [_MESSAGE_TEMPLATE_RESOURCE.query],
        "mutation": [MessageTemplateMutation, _MESSAGE_TEMPLATE_RESOURCE.mutation],
        "types": [MessageTemplateType, *_MESSAGE_TEMPLATE_RESOURCE.types],
    },
}
