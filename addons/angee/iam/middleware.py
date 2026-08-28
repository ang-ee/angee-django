"""IAM HTTP middleware."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rebac import bearer_token


class BearerTokenCsrfExemptMiddleware:
    """Mark bearer-token requests as CSRF-exempt.

    Browser session requests remain protected by Django's CSRF middleware.
    Bearer clients present credentials explicitly through the Authorization
    header, so they follow the token-authentication CSRF shape.
    """

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        """Store the next middleware or view callable."""

        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        """Exempt syntactic Bearer requests before CSRF's view check runs."""

        if bearer_token(request):
            request._dont_enforce_csrf_checks = True
        return self.get_response(request)
