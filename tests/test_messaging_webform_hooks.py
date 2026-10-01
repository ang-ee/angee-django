"""Public webform token hook composition and its fail-closed response."""

from __future__ import annotations

import json

from django.test import RequestFactory, override_settings

from angee.messaging.views import _webform_token_hook, public_webform


def _accept_token(request: object, payload: object) -> bool:
    return True


def test_webform_resolves_optional_token_hook() -> None:
    with override_settings(ANGEE_WEBFORM_TOKEN_HOOK=""):
        assert _webform_token_hook() is None
    with override_settings(ANGEE_WEBFORM_TOKEN_HOOK=f"{__name__}._accept_token"):
        assert _webform_token_hook() is _accept_token


def test_webform_bad_token_hook_returns_guard_unavailable() -> None:
    request = RequestFactory().post("/forms/example", data=json.dumps({}), content_type="application/json")
    with override_settings(ANGEE_WEBFORM_TOKEN_HOOK="builtins.Ellipsis"):
        response = public_webform(request, slug="example")
    assert response.status_code == 503
    assert json.loads(response.content)["error"] == "guard_unavailable"
