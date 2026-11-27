"""The public address of a form: describe it, and keep one submission.

The view is driven directly with a request factory, as an anonymous client
would reach it. Every name and address is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import resolve
from rebac import system_context

from angee.base.serialization import MAX_JSON_DEPTH
from angee.webforms import views
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response
from angee.webforms.urls import PUBLIC_PREFIX

pytestmark = pytest.mark.django_db

DEFINITION = {
    "title": {"default": "Cuéntanos", "en": "Tell us"},
    "elements": [
        {"type": "text", "name": "name", "isRequired": True, "title": {"default": "Tu nombre", "en": "Your name"}},
        {"type": "text", "name": "email", "inputType": "email", "isRequired": True},
    ],
}
ANSWERS = {"name": "Ada Example", "email": "ada@example.test"}
ADDRESS = "/forms/semilla"
BOUNDS = {
    "ANGEE_WEBFORM_MAX_BODY_BYTES": 65_536,
    "ANGEE_WEBFORM_RATE_LIMIT_BURST": 10,
    "ANGEE_WEBFORM_RATE_LIMIT_WINDOW": 60,
    "ANGEE_WEBFORM_HONEYPOT_FIELD": "_website",
    "ANGEE_WEBFORM_TOKEN_HOOK": "",
}


@pytest.fixture(autouse=True)
def fresh_rate_limits() -> Any:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def form() -> Any:
    """A published form in Spanish and English, at a Spanish address."""

    with system_context(reason="webforms tests setup"):
        made = Form.objects.create(name="Seeds", theme_id="acme.paper")
        version = FormVersion.objects.create(
            form=made, definition=DEFINITION, languages=["es", "en"], default_language="es",
            personal_questions=["name", "email"], contact_question="email",
        )
        version.publish()
        FormAlias.objects.create(form=made, slug="semilla", language="es")
    made.refresh_from_db()
    return made


def describe(alias: str = "semilla") -> Any:
    return views.public_form(RequestFactory().get(f"/forms/{alias}", HTTP_ACCEPT="application/json"), alias=alias)


def submit(body: Any, alias: str = "semilla", **extra: Any) -> Any:
    request = RequestFactory().post(
        f"/forms/{alias}", data=json.dumps(body) if not isinstance(body, str) else body,
        content_type=extra.pop("content_type", "application/json"), **extra,
    )
    return views.public_form(request, alias=alias)


def envelope(**changed: Any) -> dict[str, Any]:
    return {"submission_id": "r-1", "version": 1, "language": "en", "answers": ANSWERS, **changed}


def admits_the_first_receipt(request: Any, payload: Any) -> bool:
    """A token hook, named by dotted path in the tests below."""

    del request
    return bool(payload["submission_id"] == "r-1")


def kept(form: Any) -> list[Any]:
    with system_context(reason="webforms tests read"):
        return list(Response._base_manager.filter(form=form).order_by("pk"))


def test_the_address_resolves_to_the_view() -> None:
    match = resolve(ADDRESS)

    assert (match.func, match.kwargs) == (views.public_form, {"alias": "semilla"})


def test_the_public_page_is_declared_on_the_prefix_the_address_answers_at() -> None:
    """One prefix has two declarations, the URL and the page's paths in the web package: they name the same one."""

    manifest = Path(views.__file__).parent / "web" / "package.json"
    if not manifest.is_file():
        pytest.skip("The web package that declares the public page (angee.pages) is not in this checkout.")

    page = json.loads(manifest.read_text(encoding="utf-8"))["angee"]["pages"]["webforms.form"]

    assert page["paths"] == [f"/{PUBLIC_PREFIX}/*"]


def test_a_published_form_is_described_for_its_page_and_never_cached(form: Any) -> None:
    response = describe()

    body = json.loads(response.content)
    assert response.status_code == 200
    assert body == {
        "form": form.sqid, "alias": "semilla", "version": 1, "definition": DEFINITION,
        "languages": ["es", "en"], "default_language": "es", "language": "es", "theme": "acme.paper",
        "honeypot_field": "_website",
    }
    assert response["Cache-Control"] == "no-store"


def test_an_address_that_leads_nowhere_is_not_found(form: Any) -> None:
    with pytest.raises(Http404):
        describe("nowhere")
    with pytest.raises(Http404):
        submit(envelope(), alias="nowhere")
    assert kept(form) == []


def test_a_closed_form_is_not_described_and_takes_nothing_new_but_still_answers_a_resend(form: Any) -> None:
    """After a form closed, a resend of a submission it kept gets its receipt alone: no answer, no response id."""

    first = submit(envelope())
    with system_context(reason="webforms tests setup"):
        form.close()

    with pytest.raises(Http404):
        describe()
    with pytest.raises(Http404):
        submit(envelope(submission_id="r-2"))
    again = submit(envelope(answers={**ANSWERS, "name": "Someone Else"}))

    assert (first.status_code, again.status_code) == (202, 202)
    assert json.loads(again.content) == {"submission_id": "r-1"}
    (row,) = kept(form)
    assert (row.submission_id, row.answers) == ("r-1", ANSWERS)


def test_a_submission_is_kept_once_and_a_resend_is_accepted_again(form: Any) -> None:
    first = submit(envelope())
    again = submit(envelope(answers={**ANSWERS, "name": "Someone Else"}))

    assert (first.status_code, again.status_code) == (202, 202)
    assert json.loads(first.content) == json.loads(again.content) == {"submission_id": "r-1"}
    (row,) = kept(form)
    assert (row.answers, row.language, row.submission_id) == (ANSWERS, "en", "r-1")


@pytest.mark.parametrize(("changed", "field"), [
    pytest.param({"answers": {"name": "Ada Example"}}, "email", id="a-required-answer-left-out"),
    pytest.param({"answers": {**ANSWERS, "email": "ada at example"}}, "email", id="not-an-email-address"),
    pytest.param({"answers": {**ANSWERS, "owner": "usr_1"}}, "owner", id="an-answer-never-asked"),
    pytest.param({"submission_id": "has spaces"}, "submission_id", id="a-malformed-receipt"),
    pytest.param({"version": "1"}, "version", id="a-version-that-is-not-a-number"),
])
def test_a_refused_submission_names_what_it_refuses_and_keeps_nothing(
    form: Any, changed: dict[str, Any], field: str,
) -> None:
    response = submit(envelope(**changed))

    body = json.loads(response.content)
    assert (response.status_code, body["error"], set(body["fields"])) == (400, "invalid_submission", {field})
    assert kept(form) == []


@pytest.fixture
def colours() -> Any:
    with system_context(reason="webforms tests setup"):
        made = Form.objects.create(name="Colours")
        version = FormVersion.objects.create(
            form=made, languages=["en"], default_language="en", personal_questions=[],
            definition={"elements": [{"type": "checkbox", "name": "colours", "choices": [
                {"value": "red"}, {"value": "blue"},
            ]}]},
        )
        version.publish()
        FormAlias.objects.create(form=made, slug="colours", language="en")
    return made


def deep_answers(levels: int) -> dict[str, Any]:
    """Two answers to the colours question, each ``"red"`` inside ``levels`` arrays."""

    deep: Any = "red"
    for _ in range(levels):
        deep = [deep]
    return {"colours": [deep, deep]}


def test_answers_nested_past_the_json_bound_are_refused_before_the_schema_compares_them(colours: Any) -> None:
    """Deep answers to a checkbox question are refused before the schema's ``uniqueItems`` comparison walks them."""

    response = submit(envelope(answers=deep_answers(MAX_JSON_DEPTH)), alias="colours")

    body = json.loads(response.content)
    assert (response.status_code, body["error"]) == (400, "invalid_submission")
    assert body["fields"] == {"__all__": [f"The value nests deeper than {MAX_JSON_DEPTH} levels."]}
    assert kept(colours) == []


def test_a_submission_nested_past_the_document_bound_is_refused_before_its_answers_are_read(colours: Any) -> None:
    response = submit(envelope(answers=deep_answers(1_000)), alias="colours")

    assert (response.status_code, json.loads(response.content)) == (
        400, {"error": "invalid_request", "message": "Request body must be a valid UTF-8 JSON object."},
    )
    assert kept(colours) == []


def test_answers_to_a_version_the_form_no_longer_serves_are_told_to_reload(form: Any) -> None:
    response = submit(envelope(version=2))

    assert (response.status_code, json.loads(response.content)["error"]) == (409, "form_changed")
    assert kept(form) == []


@pytest.mark.parametrize(("body", "extra", "status", "code"), [
    pytest.param(envelope(owner="usr_1"), {}, 400, "invalid_request", id="a-key-the-envelope-does-not-have"),
    pytest.param("name=Ada", {"content_type": "application/x-www-form-urlencoded"}, 415, "unsupported_media_type",
                 id="not-json"),
    pytest.param("[]", {}, 400, "invalid_request", id="not-an-object"),
])
def test_the_guard_refuses_before_anything_is_read(
    form: Any, body: Any, extra: dict[str, Any], status: int, code: str,
) -> None:
    response = submit(body, **extra)

    assert (response.status_code, json.loads(response.content)["error"]) == (status, code)
    assert kept(form) == []


def test_a_filled_honeypot_is_answered_as_a_success_and_keeps_nothing(form: Any) -> None:
    response = submit(envelope(_website="https://spam.example.test"))

    assert (response.status_code, json.loads(response.content)) == (202, {"submission_id": "r-1"})
    assert kept(form) == []


@override_settings(ANGEE_WEBFORM_RATE_LIMIT_BURST=1)
def test_one_address_is_limited_per_sender_and_says_when_to_come_back(form: Any) -> None:
    first = submit(envelope(), REMOTE_ADDR="192.0.2.1")
    second = submit(envelope(submission_id="r-2"), REMOTE_ADDR="192.0.2.1")
    other = submit(envelope(submission_id="r-3"), REMOTE_ADDR="192.0.2.2")

    assert (first.status_code, second.status_code, other.status_code) == (202, 429, 202)
    assert second["Retry-After"] == "60"
    assert [row.submission_id for row in kept(form)] == ["r-1", "r-3"]


@override_settings(ANGEE_WEBFORM_MAX_BODY_BYTES=64)
def test_a_body_past_the_bound_is_refused(form: Any) -> None:
    response = submit(envelope())

    assert (response.status_code, json.loads(response.content)["error"]) == (413, "body_too_large")
    assert kept(form) == []


def test_a_declared_token_hook_decides_which_submissions_are_kept(form: Any) -> None:
    with override_settings(ANGEE_WEBFORM_TOKEN_HOOK=f"{__name__}.admits_the_first_receipt"):
        admitted = submit(envelope())
        refused = submit(envelope(submission_id="r-2"))

    assert (admitted.status_code, refused.status_code) == (202, 403)
    assert json.loads(refused.content)["error"] == "invalid_token"
    assert [row.submission_id for row in kept(form)] == ["r-1"]


def test_a_token_hook_that_cannot_be_loaded_closes_the_form(form: Any) -> None:
    with override_settings(ANGEE_WEBFORM_TOKEN_HOOK="builtins.Ellipsis"):
        response = submit(envelope())

    assert (response.status_code, json.loads(response.content)["error"]) == (503, "guard_unavailable")
    assert kept(form) == []


def test_each_bound_of_the_endpoint_is_declared_by_exactly_one_addon() -> None:
    """The addon whose address reads a bound declares it; messaging, which has no form address, declares none."""

    from angee.messaging import autoconfig as messaging
    from angee.webforms import autoconfig as webforms

    assert {name: value for name, value in webforms.SETTINGS.items() if name in BOUNDS} == BOUNDS
    assert not BOUNDS.keys() & messaging.SETTINGS.keys()
    assert {name: getattr(settings, name) for name in BOUNDS} == BOUNDS
