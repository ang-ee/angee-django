"""Upload-flow errors raised at the storage API boundary.

Raised by the file creation factory and byte-flow methods
(``File.objects.draft``, ``File.receive_bytes`` / ``File.finalize``) and
translated by the thin transport wrappers: the proxy view maps
:attr:`UploadError.status_code` to an HTTP status, the GraphQL mutations map
:attr:`UploadError.code` to a payload ``error_code``.
"""

from __future__ import annotations


class UploadError(Exception):
    """Common parent for upload-flow errors raised at the API boundary."""

    status_code: int = 400
    """HTTP status the proxy view answers with."""

    code: str = "invalid"
    """Stable machine-readable code carried on GraphQL error payloads."""


class UploadDenied(UploadError):
    """The actor may not perform this upload step."""

    status_code = 403
    code = "denied"


class UploadTargetNotFound(UploadError):
    """A drive, folder, or file addressed by the request does not exist."""

    status_code = 404
    code = "not_found"


class UploadConflict(UploadError):
    """The upload contradicts persisted state (dedup race, bad bytes, replay)."""

    status_code = 409
    code = "conflict"


class UploadTooLarge(UploadError):
    """The proxied body exceeds the configured byte cap."""

    status_code = 413
    code = "too_large"
