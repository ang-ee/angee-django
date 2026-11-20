"""GraphQL schema contributions for web forms.

The console manages forms, their versions and their addresses, and reads the
responses they keep. Nothing here is public. A form's status, a version's
publication and what became of a response change only through the verbs below;
none of them is a writable field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.webforms.models import Delivery, DeliveryFailed, FormQuerySet

Form = apps.get_model("webforms", "Form")
FormVersion = apps.get_model("webforms", "FormVersion")
FormAlias = apps.get_model("webforms", "FormAlias")
Response = apps.get_model("webforms", "Response")


@strawberry_django.type(Form)
class WebformType(AngeeNode):
    """Console projection of one form."""

    name: auto
    status: auto
    published_version: WebformVersionType | None
    target_class: auto
    target_config: auto
    theme_id: auto
    retention_days: auto
    is_archived: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(annotate=FormQuerySet.undelivered_expressions())
    def undelivered_count(self) -> int:
        """How many of the form's responses still wait for delivery (pending or failed)."""

        return cast(int, cast(Any, self).undelivered()[0])

    @strawberry_django.field(annotate=FormQuerySet.undelivered_expressions(), only=["retention_days"])
    def undelivered_expires_at(self) -> datetime | None:
        """When the first undelivered response is removed by the form's retention; null while none is."""

        return cast("datetime | None", cast(Any, self).undelivered()[1])


@strawberry_django.type(FormVersion)
class WebformVersionType(AngeeNode):
    """Console projection of one version of a form's questions."""

    form: WebformType
    number: auto
    definition: auto
    languages: auto
    default_language: auto
    personal_questions: auto
    contact_question: auto
    published_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(FormAlias)
class WebformAliasType(AngeeNode):
    """Console projection of one public address of a form."""

    form: WebformType
    slug: auto
    language: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Response)
class WebformResponseType(AngeeNode):
    """Console projection of one kept submission and what became of it."""

    form: WebformType
    version: WebformVersionType
    submission_id: auto
    language: auto
    answers: auto
    erased_at: auto
    delivery: auto
    delivery_error: auto
    delivery_attempts: auto
    retry_at: auto
    delivered_at: auto
    created_at: auto

    @strawberry_django.field(select_related=["form"], only=["created_at", "form__retention_days"])
    def expires_at(self) -> datetime | None:
        """When the form's retention removes this response, delivered or not; null while the form keeps it."""

        return cast("datetime | None", cast(Any, self).expires_at)


_FORM_DECODE = {"form": public_pk_decoder(Form)}
_VERSION_CONTENT = ["definition", "languages", "default_language", "personal_questions", "contact_question"]

_FORM_RESOURCE = hasura_model_resource(
    WebformType,
    model=Form,
    name="webforms",
    filterable=["id", "name", "status", "target_class", "is_archived"],
    sortable=["name", "status", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["status", "target_class", "is_archived"],
    writable=["name", "target_class", "target_config", "theme_id", "retention_days", "is_archived"],
    id_column="sqid",
    record_search_fields=("name",),
)

_VERSION_RESOURCE = hasura_model_resource(
    WebformVersionType,
    model=FormVersion,
    name="webform_versions",
    filterable=["id", "form", "number", "published_at"],
    sortable=["number", "published_at", "created_at"],
    aggregatable=["id"],
    groupable=["form"],
    insertable=["form", *_VERSION_CONTENT],
    updatable=_VERSION_CONTENT,
    field_id_decode=_FORM_DECODE,
    write_backend=AngeeHasuraWriteBackend(FormVersion, public_id_fields=("form",)),
    id_column="sqid",
)

_ALIAS_RESOURCE = hasura_model_resource(
    WebformAliasType,
    model=FormAlias,
    name="webform_aliases",
    filterable=["id", "form", "slug", "language"],
    sortable=["slug", "created_at"],
    aggregatable=["id"],
    groupable=["form", "language"],
    insertable=["form", "slug", "language"],
    updatable=["slug", "language"],
    field_id_decode=_FORM_DECODE,
    write_backend=AngeeHasuraWriteBackend(FormAlias, public_id_fields=("form",)),
    id_column="sqid",
    record_search_fields=("slug",),
)

_RESPONSE_RESOURCE = hasura_model_resource(
    WebformResponseType,
    model=Response,
    name="webform_responses",
    filterable=["id", "form", "version", "language", "delivery", "created_at", "delivered_at", "erased_at"],
    sortable=["created_at", "delivered_at"],
    aggregatable=["id"],
    groupable=["form", "version", "language", "delivery"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={**_FORM_DECODE, "version": public_pk_decoder(FormVersion)},
    id_column="sqid",
)


@strawberry.type
class WebformMutation:
    """The verbs that change what a form serves and what became of a response."""

    @strawberry.mutation
    @action_guard("Publish version failed.")
    def publish_webform_version(self, info: strawberry.Info, version: PublicID) -> ActionResult:
        """Publish one version: its form serves it from now on, and it never changes again."""

        target = authorized_action_target(info, FormVersion, version, "write")
        target.publish()
        return ActionResult(ok=True, message="Version published.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Open form failed.")
    def open_webform(self, info: strawberry.Info, form: PublicID) -> ActionResult:
        """Take submissions on the form's published version."""

        target = authorized_action_target(info, Form, form, "write")
        target.open()
        return ActionResult(ok=True, message="Form opened.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Close form failed.")
    def close_webform(self, info: strawberry.Info, form: PublicID) -> ActionResult:
        """Stop taking submissions; what was received stays."""

        target = authorized_action_target(info, Form, form, "write")
        target.close()
        return ActionResult(ok=True, message="Form closed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Deliver response failed.")
    def deliver_webform_response(self, info: strawberry.Info, response: PublicID) -> ActionResult:
        """Hand a pending or failed response to its form's target again."""

        target = authorized_action_target(info, Response, response, "write")
        try:
            target.deliver()
        except DeliveryFailed as failure:
            return ActionResult(ok=False, message=f"Delivery failed: {failure.reason}.", id=target.sqid)
        target.refresh_from_db(fields=["delivery"])
        if target.delivery != Delivery.DELIVERED:
            return ActionResult(ok=False, message="A dismissed response is not delivered.", id=target.sqid)
        return ActionResult(ok=True, message="Response delivered.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Dismiss response failed.")
    def dismiss_webform_response(self, info: strawberry.Info, response: PublicID) -> ActionResult:
        """Give up delivering a response that could not be."""

        target = authorized_action_target(info, Response, response, "write")
        dismissed = target.dismiss()
        return ActionResult(
            ok=True, message="Response dismissed." if dismissed else "Nothing left to dismiss.", id=target.sqid,
        )

    @strawberry.mutation
    @action_guard("Erase response failed.")
    def erase_webform_response(self, info: strawberry.Info, response: PublicID) -> ActionResult:
        """Blank the answers the response's version marks as personal."""

        target = authorized_action_target(info, Response, response, "write")
        erased = target.erase()
        return ActionResult(
            ok=True, message="Personal answers erased." if erased else "Already erased.", id=target.sqid,
        )


schemas = {
    "console": {
        "query": [
            _FORM_RESOURCE.query, _VERSION_RESOURCE.query, _ALIAS_RESOURCE.query, _RESPONSE_RESOURCE.query,
        ],
        "mutation": [
            _FORM_RESOURCE.mutation, _VERSION_RESOURCE.mutation, _ALIAS_RESOURCE.mutation, WebformMutation,
        ],
        "types": [
            WebformType, WebformVersionType, WebformAliasType, WebformResponseType,
            *_FORM_RESOURCE.types, *_VERSION_RESOURCE.types, *_ALIAS_RESOURCE.types, *_RESPONSE_RESOURCE.types,
        ],
    },
}
