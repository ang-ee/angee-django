"""The one Google REST transport: a Google API called with one credential over integrate's HTTP owner.

Every Google API client (Drive, Calendar) sends through :class:`GoogleApi`. It
owns what all Google JSON APIs share:

- the authorization: ``Credential.ensure_fresh``, then
  ``Credential.auth_headers(scopes=...)``, so an OAuth token is refreshed before
  expiry and a minting credential (``jwt_bearer``) mints for the client's scopes;
- a token Google rejects (``401``) is renewed once through ``Credential.refresh_now``
  and the request sent again;
- the transport: ``angee.integrate.http.HttpClient`` (SSRF-pinned, byte-capped), whose
  ``HttpRefusal`` classifies a transport failure, an oversized or unreadable answer and
  a refused status;
- Google's error envelope (``{"error": {"errors": [{"reason": ...}]}}``) and the
  refusals that reach a whole account (:meth:`GoogleApi.refusal`): a rate limit (a
  ``403`` naming one too) or an outage is transient and waits for its ``Retry-After``;
  a token Google no longer accepts, or one without the API's grant, asks for a reconnect.

A client keeps only its API's own vocabulary: its resources, and the refusals that
reach one record (a file, an event). Every message names the API (``service``).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Collection, Mapping
from typing import Any

import httpx2

from angee.integrate.errors import IntegrationError
from angee.integrate.http import JSON_MAX_BYTES, HttpClient, HttpRefusal

_ERROR_BODY_MAX_BYTES = 16 * 1024
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_GRANT_REFUSALS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})


class GoogleApiError(IntegrationError):
    """A Google API refusal whose message is safe to show an operator."""


class GoogleApi:
    """One Google API called with one credential, asking it for ``scopes``.

    ``service`` names the API in every message (``"Google Calendar"``), ``grant``
    the access a reconnect must grant (``"Calendar"``), and ``error`` the refusal
    class the API's client raises (a :class:`GoogleApiError`).
    """

    def __init__(
        self,
        credential: Any,
        *,
        service: str,
        grant: str,
        scopes: Collection[str] = (),
        error: type[GoogleApiError] = GoogleApiError,
    ) -> None:
        """Bind the credential every request authenticates with."""

        self.credential = credential
        self.service = service
        self.grant = grant
        self.scopes = tuple(scopes)
        self.error = error
        self.http = HttpClient()
        self._renewed = False

    def send(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        max_bytes: int = JSON_MAX_BYTES,
    ) -> httpx2.Response:
        """Send one JSON request with a fresh authorization; a rejected token is renewed once and sent again.

        ``body`` is sent as JSON. The response is returned whatever its status: the
        client reads it, or raises :meth:`refusal` or its own.
        """

        response = self._send(method, url, body=body, headers=headers, max_bytes=max_bytes)
        if response.status_code == 401 and self._renew():
            response = self._send(method, url, body=body, headers=headers, max_bytes=max_bytes)
        return response

    def stream(self, url: str, stack: contextlib.ExitStack) -> httpx2.Response:
        """Open a streamed ``GET`` inside ``stack``, renewing a rejected token once; the caller reads its bytes."""

        response = stack.enter_context(self.http.stream("GET", url, headers=self.headers()))
        if response.status_code == 401 and self._renew():
            stack.close()
            response = stack.enter_context(self.http.stream("GET", url, headers=self.headers()))
        return response

    def json(self, response: httpx2.Response) -> dict[str, Any]:
        """Return a successful response's JSON object; an unreadable one is the API's refusal."""

        try:
            data = response.json()
        except ValueError as error:
            raise self._refused(HttpRefusal("unreadable")) from error
        if not isinstance(data, dict):
            raise self._refused(HttpRefusal("unreadable"))
        return data

    def refusal(self, response: httpx2.Response, body: bytes | None = None) -> GoogleApiError | None:
        """Return the refusal of a failed response that reaches the whole account, or ``None``.

        ``body`` is the response's content, or a bounded read of a streamed one
        (:func:`bounded_body`). A rate limit (a ``403`` whose reason names one too)
        or an outage is transient; a rejected token or a missing grant asks for a
        reconnect. Anything else is the request's own, for the client to name.
        """

        refused = HttpRefusal.of(response, body)
        reason = error_reason(response.content if body is None else body)
        if refused.status == 429 or (refused.status == 403 and reason in _RATE_LIMIT_REASONS):
            return self.error(
                f"{self.service} is rate limiting this account; try the sync again later.",
                transient=True,
                retry_after=refused.retry_after,
            )
        if refused.status == 401:
            return self.error(f"{self.service} no longer accepts this account; reconnect the Google account.")
        if refused.status == 403 and reason in _GRANT_REFUSALS:
            return self.error(
                f"This Google account has not granted {self.grant} access; reconnect it with {self.grant} access."
            )
        return self._refused(refused) if refused.transient else None

    def raise_for_status(self, response: httpx2.Response, body: bytes | None = None) -> None:
        """Raise the account-wide refusal of a failed response, else the API's generic refusal."""

        if response.is_success:
            return
        raise self.refusal(response, body) or self._refused(HttpRefusal.of(response, body))

    def headers(self) -> dict[str, str]:
        """Return a fresh authorization for the next request."""

        self.credential.ensure_fresh()
        return {**self.credential.auth_headers(scopes=self.scopes), "Accept": "application/json"}

    def _send(
        self, method: str, url: str, *, body: Any, headers: Mapping[str, str] | None, max_bytes: int
    ) -> httpx2.Response:
        """Send once; a transport failure or an oversized answer is the API's refusal."""

        content = None if body is None else json.dumps(body).encode()
        sent = {**self.headers(), **(headers or {}), **({"Content-Type": "application/json"} if content else {})}
        return self.http.request(method, url, headers=sent, body=content, max_bytes=max_bytes, error=self._refused)

    def _refused(self, refusal: HttpRefusal) -> GoogleApiError:
        """Return the API's refusal composed from ``refusal``'s facts alone."""

        return self.error(refusal.message(self.service), transient=refusal.transient, retry_after=refusal.retry_after)

    def _renew(self) -> bool:
        """Renew a token Google rejected, once, through the credential owner; whether a retry may help."""

        if self._renewed or not self.credential.refreshable:
            return False
        self._renewed = True
        try:
            self.credential.refresh_now()
        except ValueError:
            return False
        except IntegrationError as error:
            if error.transient:
                raise
            return False
        return True


def error_reason(body: bytes) -> str:
    """Return Google's error reason (``rateLimitExceeded``, ...) from a refusal body, or ``""``."""

    try:
        error = json.loads(body).get("error") or {}
        errors = error.get("errors") or ()
        return str(errors[0].get("reason") or "") if errors else str(error.get("status") or "")
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""


def bounded_body(response: httpx2.Response) -> bytes:
    """Read at most 16 KiB of a streamed refusal, enough for its reason."""

    body = b""
    for chunk in response.iter_bytes(chunk_size=_ERROR_BODY_MAX_BYTES):
        body += chunk
        if len(body) >= _ERROR_BODY_MAX_BYTES:
            break
    return body[:_ERROR_BODY_MAX_BYTES]
