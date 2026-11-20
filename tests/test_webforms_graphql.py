"""The console's forms, versions, addresses and responses, per actor.

A platform admin and a holder of the ``webforms_admin`` role manage forms and
read what they keep; anyone else reads nothing and changes nothing. Every name
and address is invented.
"""

from __future__ import annotations

from typing import Any

import pytest
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.graphql.testing.documents import execute_schema, result_data
from angee.webforms import schema as webforms_schema
from angee.webforms.models import Delivery, FormStatus
from angee.webforms.testing.models import Form, FormVersion, Response
from tests.conftest import addon_schema, create_platform_admin, create_user

pytestmark = pytest.mark.django_db

DEFINITION = {
    "elements": [
        {"type": "text", "name": "name", "isRequired": True},
        {"type": "text", "name": "email", "inputType": "email", "isRequired": True},
    ],
}
ANSWERS = {"name": "Ada Example", "email": "ada@example.test"}
FORMS = "{ webforms { name status } }"
RESPONSES = "{ webform_responses { submission_id delivery answers form { name } version { number } } }"
PUBLISH = "mutation($id: ID!) { publish_webform_version(version: $id) { ok message } }"
CLOSE = "mutation($id: ID!) { close_webform(form: $id) { ok message } }"
ERASE = "mutation($id: ID!) { erase_webform_response(response: $id) { ok message } }"
DISMISS = "mutation($id: ID!) { dismiss_webform_response(response: $id) { ok message } }"


def schema() -> Any:
    return addon_schema(webforms_schema.schemas, "console")


def manager(username: str = "webforms-manager") -> Any:
    """A user who holds the web forms role and no other."""

    user = create_user(username)
    with system_context(reason="webforms tests setup"):
        grant_role(actor=user, role="webforms/role:webforms_admin")
    return user


def kept_form() -> tuple[Any, Any, Any]:
    """A published form with one response."""

    with system_context(reason="webforms tests setup"):
        form = Form.objects.create(name="Feedback")
        version = FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en"], default_language="en",
            personal_questions=["name", "email"], contact_question="email",
        )
        version.publish()
    response, _made = Response.objects.receive(
        form, version=1, submission_id="r-1", language="en", answers=ANSWERS,
    )
    return form, version, response


def stored(row: Any) -> Any:
    with system_context(reason="webforms tests read"):
        return type(row)._base_manager.get(pk=row.pk)


@pytest.mark.parametrize("who", ["admin", "manager"])
def test_whoever_manages_forms_makes_one_and_publishes_its_version(who: str) -> None:
    actor = create_platform_admin("webforms-admin") if who == "admin" else manager()

    made = result_data(execute_schema(schema(), """
      mutation { insert_webforms_one(object: {name: "Feedback"}) { id status target_class } }
    """, user=actor))["insert_webforms_one"]
    version = result_data(execute_schema(schema(), """
      mutation($form: ID!, $definition: JSON!) {
        insert_webform_versions_one(object: {
          form: $form, definition: $definition, languages: ["en"], default_language: "en", personal_questions: []
        }) { id number published_at }
      }
    """, {"form": made["id"], "definition": DEFINITION}, user=actor))["insert_webform_versions_one"]
    published = result_data(execute_schema(schema(), PUBLISH, {"id": version["id"]}, user=actor))

    assert (made["status"], made["target_class"], version["number"], version["published_at"]) == (
        "DRAFT", "STORE", 1, None,
    )
    assert published["publish_webform_version"]["ok"] is True
    assert result_data(execute_schema(schema(), FORMS, user=actor))["webforms"] == [
        {"name": "Feedback", "status": "PUBLISHED"},
    ]


def test_a_definition_outside_the_subset_is_refused_under_its_field() -> None:
    actor = manager()
    with system_context(reason="webforms tests setup"):
        form = Form.objects.create(name="Feedback")

    refused = execute_schema(schema(), """
      mutation($form: ID!) {
        insert_webform_versions_one(object: {
          form: $form, languages: ["en"], default_language: "en",
          definition: {elements: [{type: "matrix", name: "grid"}]}
        }) { id }
      }
    """, {"form": form.sqid}, user=actor)

    assert refused.errors
    assert "definition" in str(refused.errors[0].extensions)
    with system_context(reason="webforms tests read"):
        assert not FormVersion._base_manager.filter(form=form).exists()


def test_a_manager_reads_responses_and_an_outsider_and_a_visitor_read_and_change_nothing() -> None:
    form, version, response = kept_form()
    outsider = create_user("webforms-outsider")

    rows = result_data(execute_schema(schema(), RESPONSES, user=manager()))["webform_responses"]

    assert rows == [{
        "submission_id": "r-1", "delivery": "PENDING", "answers": ANSWERS, "form": {"name": "Feedback"},
        "version": {"number": 1},
    }]
    for actor in (outsider, None):
        assert (execute_schema(schema(), RESPONSES, user=actor).data or {}).get("webform_responses", []) == []
        assert (execute_schema(schema(), FORMS, user=actor).data or {}).get("webforms", []) == []
        for document, target in ((CLOSE, form), (ERASE, response), (DISMISS, response)):
            result = execute_schema(schema(), document, {"id": target.sqid}, user=actor)
            verb = next(iter(result.data.values())) if result.data else None
            assert result.errors or verb is None or verb["ok"] is False, document
    kept = stored(response)
    assert (stored(form).status, kept.answers, kept.delivery) == (FormStatus.PUBLISHED, ANSWERS, Delivery.PENDING)


def test_a_manager_closes_a_form_and_erases_and_dismisses_a_response() -> None:
    form, _version, response = kept_form()
    actor = manager()

    closed = result_data(execute_schema(schema(), CLOSE, {"id": form.sqid}, user=actor))["close_webform"]
    erased = result_data(execute_schema(schema(), ERASE, {"id": response.sqid}, user=actor))
    dismissed = result_data(execute_schema(schema(), DISMISS, {"id": response.sqid}, user=actor))

    assert (closed["ok"], erased["erase_webform_response"]["ok"], dismissed["dismiss_webform_response"]["ok"]) == (
        True, True, True,
    )
    kept = stored(response)
    assert (stored(form).status, kept.answers, kept.erased_at is not None, kept.delivery) == (
        FormStatus.CLOSED, {}, True, Delivery.DISMISSED,
    )


def test_the_status_and_what_became_of_a_response_are_not_writable_fields() -> None:
    form, _version, response = kept_form()
    actor = manager()

    forced = execute_schema(schema(), """
      mutation($id: ID!) { update_webforms_by_pk(pk_columns: {id: $id}, _set: {status: "closed"}) { id } }
    """, {"id": form.sqid}, user=actor)
    rewritten = execute_schema(schema(), """
      mutation($id: ID!) { update_webform_responses_by_pk(pk_columns: {id: $id}, _set: {answers: {}}) { id } }
    """, {"id": response.sqid}, user=actor)

    assert forced.errors and rewritten.errors
    assert (stored(form).status, stored(response).answers) == (FormStatus.PUBLISHED, ANSWERS)
