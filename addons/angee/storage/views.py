"""HTTP views for the storage proxy: upload (PUT) and download (GET)."""

from __future__ import annotations

from django.apps import apps
from django.http import FileResponse, HttpRequest, JsonResponse
from django.http.response import HttpResponseBase
from django.utils.cache import get_conditional_response, patch_cache_control, patch_vary_headers, quote_etag
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rebac import bearer_token

from angee.storage import exceptions
from angee.storage.uploads import (
    DOWNLOAD_TOKEN_HEADER,
    DOWNLOAD_TOKEN_MAX_AGE,
    FALLBACK_MIME,
    UPLOAD_TOKEN_HEADER,
)


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
    token = request.headers.get(UPLOAD_TOKEN_HEADER, "") or str(request.GET.get("token") or "") or bearer_token(request)
    file_model = apps.get_model("storage", "File")
    try:
        row = file_model.objects.for_upload_token(token)
        row.receive_bytes(request)
    except exceptions.UploadError as error:
        return JsonResponse({"error": str(error), "code": error.code}, status=error.status_code)
    return JsonResponse({"id": str(row.sqid)})


@require_http_methods(["GET"])
def download(request: HttpRequest, filename: str) -> HttpResponseBase:
    """Stream one READY file's bytes for a signed proxy download token.

    The mirror of :func:`upload`: the token (``?token=``, the
    ``X-Angee-Download-Token`` header, or ``Authorization: Bearer``) is the
    capability — minted on the file's ``url`` field for a reader, unforgeable,
    and TTL-bound — so no session is needed. The ``filename`` in the path is the
    save-as name only; the authoritative name and content type come from the row.
    """

    del filename  # cosmetic save-as name; the token identifies the file
    token = (
        request.headers.get(DOWNLOAD_TOKEN_HEADER, "") or str(request.GET.get("token") or "") or bearer_token(request)
    )
    file_model = apps.get_model("storage", "File")
    try:
        row = file_model.objects.for_download_token(token)
    except exceptions.UploadError as error:
        return JsonResponse({"error": str(error), "code": error.code}, status=error.status_code)
    etag = quote_etag(row.content_hash)
    if conditional := get_conditional_response(request, etag=etag):
        return _download_cache_response(conditional, etag)
    stream = row.open_stream()
    content_type = row.mime_type.mime_type if row.mime_type_id else FALLBACK_MIME
    response = FileResponse(stream, filename=row.filename, content_type=content_type)
    return _download_cache_response(response, etag)


def _download_cache_response(response: HttpResponseBase, etag: str) -> HttpResponseBase:
    response["ETag"] = etag
    if response.status_code in {200, 304}:
        patch_cache_control(response, private=True, immutable=True, max_age=DOWNLOAD_TOKEN_MAX_AGE)
        patch_vary_headers(response, [DOWNLOAD_TOKEN_HEADER, "Authorization"])
    return response
