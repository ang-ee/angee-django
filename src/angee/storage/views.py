"""HTTP views for the storage proxy: upload (PUT) and download (GET)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Collection
from typing import IO, Any, Literal

from asgiref.sync import sync_to_async
from django.apps import apps
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils.cache import get_conditional_response, patch_cache_control, patch_vary_headers
from django.utils.csp import CSP, build_policy
from django.utils.http import content_disposition_header, quote_etag
from django.views.decorators.csp import csp_override
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rebac import bearer_token

from angee.storage import exceptions
from angee.storage.uploads import (
    DOWNLOAD_TOKEN_HEADER,
    DOWNLOAD_TOKEN_MAX_AGE,
    FALLBACK_MIME,
    UPLOAD_TOKEN_HEADER,
    opens_in_viewer,
    renders_inline,
)

logger = logging.getLogger(__name__)

DOWNLOAD_CSP: dict[str, Collection[str] | str | Literal[True]] = {
    "default-src": [CSP.NONE],
    "style-src": [CSP.UNSAFE_INLINE],
    "sandbox": True,
}
"""The policy on a download: opened directly, served bytes load nothing and run nothing.

``sandbox`` gives the document an opaque origin; the inline style a browser's own
image page uses is all it allows. A page embedding the file is unaffected.
"""

DOWNLOAD_BLOCK_BYTES = 64 * 1024
"""Bytes read from storage per streamed download block."""

VIEWER_CSP = {"default-src": [CSP.NONE], "media-src": [CSP.SELF], "style-src": [CSP.UNSAFE_INLINE]}
"""The policy on a type the browser opens in its own viewer (PDF, text, audio, video).

Nothing script-capable loads, as under :data:`DOWNLOAD_CSP`, but without
``sandbox``: a media page loads its own bytes, which an opaque origin is refused.
"""


@csrf_exempt
@require_http_methods(["PUT"])
def upload(request: HttpRequest) -> JsonResponse:
    """Accept one raw upload body for a DRAFT file row.

    Proxy mode only: the body is raw bytes, never multipart. The one-shot
    signed token (``?token=``, the ``X-Angee-Upload-Token`` header, or
    ``Authorization: Bearer``) binds the PUT to a single draft row and is
    unforgeable + single-use — the CSRF property this endpoint relies on in
    place of the cookie token. Identity is still the request actor:
    :meth:`File.receive_bytes` requires an authenticated file writer,
    so the request must carry the session
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


# Django handles True as a bare directive; django-stubs omits that supported value.
@require_http_methods(["GET"])
@csp_override(DOWNLOAD_CSP)  # type: ignore[arg-type]
def download(request: HttpRequest, filename: str) -> HttpResponseBase:
    """Stream one READY file's bytes for a signed proxy download token.

    The mirror of :func:`upload`: the token (``?token=``, the
    ``X-Angee-Download-Token`` header, or ``Authorization: Bearer``) is the
    bearer capability. The manager checks its signature, expiry and the issuing
    actor's current read permission before serving bytes or answering 304; no
    request credentials are needed. The ``filename`` in the path is the save-as
    name only; the authoritative name and content type come from the row. Raster
    images and the types a browser opens in its own viewer render in place
    (:func:`~angee.storage.uploads.renders_inline`); every other type downloads as
    an attachment. A viewer type carries :data:`VIEWER_CSP`, every other response
    :data:`DOWNLOAD_CSP`, and all of them ``nosniff`` from Django's
    ``SecurityMiddleware``. The bytes stream as an async iterator reading one
    block at a time in a worker thread, so a slow or remote backend never blocks
    the event loop nor buffers the whole file; the row's size is the length. A
    backend that refuses to open the bytes is answered 404, 403 or 502
    (:func:`_storage_refusal`), never 500.
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
    mime_type = row.mime_type
    content_type = mime_type.mime_type if mime_type is not None else FALLBACK_MIME
    if conditional := get_conditional_response(request, etag=etag):
        return _download_cache_response(conditional, etag, content_type)
    try:
        stream = row.open_stream()
    except OSError as error:
        return _storage_refusal(row, error)
    response = StreamingHttpResponse(_stream_blocks(stream), content_type=content_type)
    response["Content-Length"] = str(row.size_bytes)
    disposition = content_disposition_header(not renders_inline(content_type), row.filename)
    if disposition:
        response["Content-Disposition"] = disposition
    return _download_cache_response(response, etag, content_type)


def _storage_refusal(row: Any, error: OSError) -> JsonResponse:
    """Answer a backend's refusal to open stored bytes: missing 404, refused 403, otherwise 502.

    The status and code follow the upload flow's errors; the backend's own
    message, which may name a path or a remote reason, goes to the log only.
    """

    refusal: type[exceptions.UploadError] | None = None
    if isinstance(error, FileNotFoundError):
        refusal, message = exceptions.UploadTargetNotFound, "The stored file is missing."
    elif isinstance(error, PermissionError):
        refusal, message = exceptions.UploadDenied, "The storage refused to serve this file."
    else:
        message = "The storage could not serve this file."
    status, code = (refusal.status_code, refusal.code) if refusal is not None else (502, "unavailable")
    logger.warning("storage.download: %s refused %s: %s", type(error).__name__, row.sqid, error)
    return JsonResponse({"error": message, "code": code}, status=status)


async def _stream_blocks(stream: IO[bytes]) -> AsyncIterator[bytes]:
    """Yield stored bytes block by block from a worker thread, closing the stream however the response ends."""

    read = sync_to_async(stream.read, thread_sensitive=False)
    try:
        while block := await read(DOWNLOAD_BLOCK_BYTES):
            yield block
    finally:
        await sync_to_async(stream.close, thread_sensitive=False)()


def _download_cache_response(response: HttpResponseBase, etag: str, content_type: str) -> HttpResponseBase:
    response["ETag"] = etag
    if opens_in_viewer(content_type):
        # Set before the CSP middleware runs, which keeps a policy already present.
        # A 304 carries it too, since its headers update the cached response.
        response.headers[CSP.HEADER_ENFORCE] = build_policy(VIEWER_CSP)
    if response.status_code in {200, 304}:
        patch_cache_control(response, private=True, immutable=True, max_age=DOWNLOAD_TOKEN_MAX_AGE)
        patch_vary_headers(response, [DOWNLOAD_TOKEN_HEADER, "Authorization"])
    return response
