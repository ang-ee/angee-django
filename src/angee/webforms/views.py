"""The curated anonymous surface of a published web form.

One address serves two requests. ``GET`` describes the version the form serves
now, for the public page to render. ``POST`` keeps one submission, and is the
only anonymous write: the shared guard runs in full before anything is read,
then :meth:`~angee.webforms.models.ResponseManager.receive` decides whether the
form takes it, judges the answers by the published version and keeps the
response. Nothing here decides what a submission becomes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rebac import system_context

from angee.base.hooks import resolve_hook
from angee.server.ingress import (
    AnonymousIngressPolicy,
    AnonymousIngressRateLimit,
    AnonymousIngressRejected,
    AnonymousIngressTokenHook,
    AnonymousIngressUnavailable,
    guard_anonymous_ingress,
)
from angee.webforms.models import FormChanged, FormUnavailable
from angee.webforms.validators import ENVELOPE_FIELDS, validate_submission_id


@csrf_exempt
@require_http_methods(["GET", "POST"])
def public_form(request: HttpRequest, alias: str) -> JsonResponse:
    """Describe the form at ``alias``, or keep one submission to it."""

    if request.method == "GET":
        served = _address(alias, serving=True)
        version = served.form.published_version
        response = JsonResponse(
            {
                "form": served.form.sqid,
                "alias": served.slug,
                "version": version.number,
                "definition": version.definition,
                "languages": version.languages,
                "default_language": version.default_language,
                "language": served.language if served.language in version.languages else "",
                "theme": served.form.theme_id,
                "honeypot_field": _policy(alias).honeypot_field or "",
            }
        )
        response["Cache-Control"] = "no-store"
        return response

    try:
        checked = guard_anonymous_ingress(request, _policy(alias))
        submission_id = validate_submission_id(checked.payload.get("submission_id"))
        number = checked.payload.get("version")
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValidationError({"version": "Send the number of the version the answers are to."})
    except AnonymousIngressRejected as error:
        return _ingress_error(error)
    except ValidationError as error:
        return _validation_error(error)
    if checked.dropped:
        return JsonResponse({"submission_id": submission_id}, status=202)

    # Whether the form takes this submission is its owner's to say, so the address alone is resolved here.
    form = _address(alias, serving=False).form
    try:
        apps.get_model("webforms", "Response").objects.receive(
            form, version=number, submission_id=submission_id,
            language=checked.payload.get("language"), answers=checked.payload.get("answers"),
        )
    except ValidationError as error:
        return _validation_error(error)
    except FormUnavailable as error:
        raise Http404 from error
    except FormChanged:
        return JsonResponse(
            {"error": "form_changed", "message": "The form changed; reload and submit again."}, status=409,
        )
    return JsonResponse({"submission_id": submission_id}, status=202)


def _address(alias: str, *, serving: bool) -> Any:
    """Return the address ``alias`` with its form; with ``serving``, only while that form takes submissions."""

    aliases = apps.get_model("webforms", "FormAlias").objects
    with system_context(reason=f"webforms.public_form.{alias}.resolve"):
        found = (aliases.serving() if serving else aliases.all()).select_related(
            "form", "form__published_version",
        ).filter(slug=alias).first()
    if found is None:
        raise Http404
    return found


def _policy(alias: str) -> AnonymousIngressPolicy:
    """Return the guard's bounds for one address, from the ``ANGEE_WEBFORM_*`` settings."""

    honeypot = str(settings.ANGEE_WEBFORM_HONEYPOT_FIELD or "")
    return AnonymousIngressPolicy(
        max_body_bytes=int(settings.ANGEE_WEBFORM_MAX_BODY_BYTES),
        allowed_fields=frozenset({*ENVELOPE_FIELDS, honeypot} if honeypot else ENVELOPE_FIELDS),
        honeypot_field=honeypot or None,
        rate_limit=AnonymousIngressRateLimit(
            scope=f"webforms.public_form.{alias}",
            burst=int(settings.ANGEE_WEBFORM_RATE_LIMIT_BURST),
            window_seconds=int(settings.ANGEE_WEBFORM_RATE_LIMIT_WINDOW),
        ),
        token_hook=_token_hook(),
    )


def _token_hook() -> AnonymousIngressTokenHook | None:
    try:
        return resolve_hook("ANGEE_WEBFORM_TOKEN_HOOK")
    except ImproperlyConfigured as error:
        raise AnonymousIngressUnavailable("The configured webform token hook is unavailable.") from error


def _ingress_error(error: AnonymousIngressRejected) -> JsonResponse:
    response = JsonResponse({"error": error.code, "message": str(error)}, status=error.status_code)
    if error.retry_after is not None:
        response["Retry-After"] = str(error.retry_after)
    return response


def _validation_error(error: ValidationError) -> JsonResponse:
    """Answer a refused submission with each refusal under the question (or envelope field) it is about."""

    if hasattr(error, "message_dict"):
        fields: Mapping[str, Any] = {
            name: [str(message) for message in messages] for name, messages in error.message_dict.items()
        }
    else:
        fields = {"answers": [str(message) for message in error.messages]}
    return JsonResponse(
        {"error": "invalid_submission", "message": "The submission is invalid.", "fields": fields}, status=400,
    )
