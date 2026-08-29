"""Curated anonymous HTTP surface for published webform channels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from angee.base.ingress import (
    AnonymousIngressPolicy,
    AnonymousIngressRateLimit,
    AnonymousIngressRejected,
    AnonymousIngressTokenHook,
    AnonymousIngressTooLarge,
    AnonymousIngressUnavailable,
    guard_anonymous_ingress,
)
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest, JsonResponse
from django.utils.module_loading import import_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rebac import system_context

from angee.messaging.webforms import validate_submission_id

WEBFORM_ENVELOPE_FIELDS = frozenset({"submission_id", "answers"})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def public_webform(request: HttpRequest, slug: str) -> JsonResponse:
    """Describe or submit one published public-form channel.

    ``GET`` is the narrow read needed by the public React route. ``POST`` is the
    only anonymous write: the framework guard runs in full before the first
    ``system_context``, then the row-owned schema validates answers and the
    message manager's one ingest path performs the durable idempotent write.
    """

    if request.method == "GET":
        channel = _published_webform(slug)
        try:
            channel.webform_spec()
        except ValidationError as error:
            raise Http404 from error
        response = JsonResponse(
            {
                "slug": channel.slug,
                "title": str(channel.display_name or channel.slug),
                "schema_version": int(channel.form_schema_version),
                "form_schema": channel.form_schema,
            }
        )
        response["Cache-Control"] = "no-store"
        return response

    try:
        checked = guard_anonymous_ingress(request, _webform_policy(slug))
        submission_id = validate_submission_id(checked.payload.get("submission_id"))
    except AnonymousIngressRejected as error:
        return _ingress_error(error)
    except ValidationError as error:
        return _validation_error(error)

    if checked.dropped:
        return JsonResponse({"submission_id": submission_id}, status=202)

    channel = _published_webform(slug)
    if checked.body_size > int(channel.max_body_bytes):
        return _ingress_error(
            AnonymousIngressTooLarge(f"Request body exceeds this form's {channel.max_body_bytes}-byte limit.")
        )
    try:
        spec = channel.webform_spec()
    except ValidationError:
        return JsonResponse(
            {"error": "form_unavailable", "message": "This form is not configured correctly."},
            status=503,
        )
    try:
        answers = spec.validate_answers(
            checked.payload.get("answers"),
            max_field_bytes=int(channel.max_field_bytes),
        )
    except ValidationError as error:
        return _validation_error(error)
    parsed = channel.webform_message(
        submission_id=submission_id,
        answers=answers,
    )

    channel_model = apps.get_model("messaging", "Channel")
    message_model = apps.get_model("messaging", "Message")
    with system_context(reason=f"public form {slug}"), transaction.atomic():
        locked = (
            channel_model.objects.select_for_update()
            .filter(
                pk=channel.pk,
                slug=slug,
                is_published=True,
                backend_class="webform",
            )
            .first()
        )
        if locked is None:
            raise Http404
        if (
            locked.form_schema_version != channel.form_schema_version
            or locked.form_schema != channel.form_schema
            or locked.max_body_bytes != channel.max_body_bytes
            or locked.max_field_bytes != channel.max_field_bytes
        ):
            return JsonResponse(
                {"error": "form_changed", "message": "The form changed; reload and submit again."},
                status=409,
            )
        message_model.objects.ingest(
            [parsed],
            channel=locked,
            quote_edges=False,
        )
    return JsonResponse({"submission_id": submission_id}, status=202)


def _published_webform(slug: str) -> Any:
    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason=f"public form {slug}.resolve"):
        channel = (
            channel_model.objects.filter(
                slug=slug,
                is_published=True,
                backend_class="webform",
            )
            .order_by("pk")
            .first()
        )
    if channel is None:
        raise Http404
    return channel


def _webform_policy(slug: str) -> AnonymousIngressPolicy:
    honeypot = str(getattr(settings, "ANGEE_WEBFORM_HONEYPOT_FIELD", "_website") or "")
    allowed_fields = set(WEBFORM_ENVELOPE_FIELDS)
    if honeypot:
        allowed_fields.add(honeypot)
    return AnonymousIngressPolicy(
        max_body_bytes=int(getattr(settings, "ANGEE_WEBFORM_MAX_BODY_BYTES", 65_536)),
        allowed_fields=frozenset(allowed_fields),
        honeypot_field=honeypot or None,
        rate_limit=AnonymousIngressRateLimit(
            scope=f"messaging.public_webform.{slug}",
            burst=int(getattr(settings, "ANGEE_WEBFORM_RATE_LIMIT_BURST", 10)),
            window_seconds=int(getattr(settings, "ANGEE_WEBFORM_RATE_LIMIT_WINDOW", 60)),
        ),
        token_hook=_webform_token_hook(),
    )


def _webform_token_hook() -> AnonymousIngressTokenHook | None:
    dotted = str(getattr(settings, "ANGEE_WEBFORM_TOKEN_HOOK", "") or "").strip()
    if not dotted:
        return None
    try:
        hook = import_string(dotted)
    except ImportError as error:
        raise AnonymousIngressUnavailable("The configured webform token hook is unavailable.") from error
    if not callable(hook):
        raise AnonymousIngressUnavailable(f"settings.ANGEE_WEBFORM_TOKEN_HOOK = {dotted!r} is not callable.")
    return hook


def _ingress_error(error: AnonymousIngressRejected) -> JsonResponse:
    response = JsonResponse(
        {"error": error.code, "message": str(error)},
        status=error.status_code,
    )
    if error.retry_after is not None:
        response["Retry-After"] = str(error.retry_after)
    return response


def _validation_error(error: ValidationError) -> JsonResponse:
    if hasattr(error, "message_dict"):
        fields: Mapping[str, Any] = {
            name: [str(message) for message in messages] for name, messages in error.message_dict.items()
        }
    else:
        fields = {"answers": [str(message) for message in error.messages]}
    return JsonResponse(
        {
            "error": "invalid_submission",
            "message": "The submission is invalid.",
            "fields": fields,
        },
        status=400,
    )
