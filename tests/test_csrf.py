"""Tests for IAM-owned CSRF middleware behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings
from django.urls import path

from angee.graphql.views import csrf_token
from angee.iam.autoconfig import settings


def ok_view(request: HttpRequest) -> HttpResponse:
    """Return a simple response for CSRF middleware tests."""

    del request
    return HttpResponse("ok")


urlpatterns = [
    path("auth/csrf/", csrf_token),
    path("graphql/public/", ok_view),
]


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "angee.iam.middleware.BearerTokenCsrfExemptMiddleware",
    ],
)
def test_cookie_graphql_posts_need_csrf_but_bearer_posts_are_exempt() -> None:
    """Session-cookie posts need CSRF; bearer-token posts do not."""

    client = Client(enforce_csrf_checks=True)

    rejected = client.post("/graphql/public/", {}, content_type="application/json")
    token = client.get("/auth/csrf/").json()["token"]
    accepted = client.post(
        "/graphql/public/",
        {},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    bearer = Client(enforce_csrf_checks=True).post(
        "/graphql/public/",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer token",
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert bearer.status_code == 200


def test_development_cookie_names_are_stable_and_isolated(tmp_path: Path) -> None:
    """Local stacks isolate cookies without overriding explicit host settings."""

    first = settings({"DEBUG": True, "BASE_DIR": tmp_path / "first"})
    second = settings({"DEBUG": True, "BASE_DIR": tmp_path / "second"})
    assert first == settings({"DEBUG": True, "BASE_DIR": tmp_path / "first"})
    assert first["SESSION_COOKIE_NAME"] != second["SESSION_COOKIE_NAME"]
    assert first["CSRF_COOKIE_NAME"] != second["CSRF_COOKIE_NAME"]
    assert settings({"DEBUG": False, "BASE_DIR": tmp_path}) == {}
    assert (
        settings(
            {
                "DEBUG": True,
                "BASE_DIR": tmp_path,
                "SESSION_COOKIE_NAME": "explicit",
                "CSRF_COOKIE_NAME": "explicit_csrf",
            }
        )
        == {}
    )
