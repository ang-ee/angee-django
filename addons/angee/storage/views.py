"""HTTP view for the storage upload proxy."""

from __future__ import annotations

from django.apps import apps
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rebac import bearer_token

from angee.storage import exceptions
from angee.storage.uploads import UPLOAD_TOKEN_HEADER


@csrf_exempt
@require_http_methods(["PUT"])
def upload(request: HttpRequest) -> JsonResponse:
    """Accept one raw upload body for a DRAFT file row.

    Proxy mode only: the body is raw bytes, never multipart. The one-shot
    signed token (``?token=``, the ``X-Angee-Upload-Token`` header, or
    ``Authorization: Bearer``) binds the PUT to a single draft row and is
    unforgeable + single-use — the CSRF property this endpoint relies on in
    place of the cookie token. Identity is still the request actor:
    :meth:`File.receive_bytes` requires an authenticated uploader (the row's
    ``created_by``) or a drive writer, so the request must carry the session
    cookie (or a credential the actor middleware resolves).
    """

    # The explicit carriers win over the Authorization header — a client
    # PUTting to the provided upload_url with its normal bearer auth attached
    # must not have the JWT mistaken for the upload token.
    token = (
        request.headers.get(UPLOAD_TOKEN_HEADER, "")
        or str(request.GET.get("token") or "")
        or bearer_token(request)
    )
    file_model = apps.get_model("storage", "File")
    try:
        row = file_model.objects.for_upload_token(token)
        row.receive_bytes(request)
    except exceptions.UploadError as error:
        return JsonResponse({"error": str(error), "code": error.code}, status=error.status_code)
    return JsonResponse({"id": str(row.sqid)})
