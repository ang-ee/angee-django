"""PostgreSQL interleavings of a form's submissions, deliveries and publication.

A submission judges its answers before it locks its form and checks under the
lock that the form still serves the version it judged; a delivery locks its
response; a publication locks the form and then the version, and so does a
version's own save. Whichever runs second waits on the other's lock and judges
its committed state. Two forms taking one address wait on its unique slug.
Every name and address is invented.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import system_context

from angee.tags.testing.models import Tag
from angee.webforms.models import Delivery, FormChanged
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response, ResponseRecord
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]

DEFINITION: dict[str, Any] = {
    "elements": [
        {"type": "text", "name": "name", "isRequired": True},
        {"type": "text", "name": "email", "inputType": "email", "isRequired": True},
    ],
}
SHORTER: dict[str, Any] = {"elements": [{"type": "text", "name": "name", "isRequired": True}]}
ANSWERS = {"name": "Ada Example", "email": "ada@example.test"}


def form_with(target: str = "store") -> tuple[Any, Any]:
    """A form and its first version, unpublished."""

    with system_context(reason="webforms concurrency test setup"):
        form = Form.objects.create(name="Feedback", target_class=target)
        version = FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en"], default_language="en",
            personal_questions=["name", "email"],
        )
    return form, version


def publish(version: Any) -> None:
    with system_context(reason="webforms concurrency test setup"):
        FormVersion.objects.get(pk=version.pk).publish()


def receive(form: Any, receipt: str = "r-1", **facts: Any) -> tuple[Any, bool]:
    values = {"version": 1, "submission_id": receipt, "language": "en", "answers": ANSWERS, **facts}
    return Response.objects.receive(form, **values)


def stored(row: Any) -> Any:
    with system_context(reason="webforms concurrency test read"):
        return type(row)._base_manager.get(pk=row.pk)


def test_two_sends_of_one_submission_keep_one_response() -> None:
    """The second waits on the form's lock and is answered with what the first kept."""

    form, version = form_with()
    publish(version)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Form.system_queryset(lock=("self",)).get(pk=form.pk)
            contender, pid = submit(pool, lambda: receive(form))
            wait_for_lock(pid, contender)
            mine, made = receive(form)
        theirs, made_again = contender.result(timeout=10)

    assert (mine.pk, made, made_again) == (theirs.pk, True, False)
    assert Response._base_manager.filter(form=form).count() == 1


def test_a_submission_in_flight_when_a_new_version_is_published_is_told_the_form_changed() -> None:
    """Its answers were judged by the version it saw; under the lock the form serves another, so nothing is kept."""

    form, version = form_with()
    publish(version)
    with system_context(reason="webforms concurrency test setup"):
        newer = FormVersion.objects.create(
            form=form, definition=SHORTER, languages=["en"], default_language="en", personal_questions=["name"],
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            publish(newer)
            contender, pid = submit(pool, lambda: receive(form))
            wait_for_lock(pid, contender)
        with pytest.raises(FormChanged):
            contender.result(timeout=10)

    assert not Response._base_manager.filter(form=form).exists()
    kept, made = receive(form, "r-2", version=2, answers={"name": "Ada Example"})
    assert (kept.version_id, made) == (newer.pk, True)


def test_two_deliveries_of_one_response_make_one_record() -> None:
    """The second waits on the response's lock and finds it delivered, with its link."""

    form, version = form_with("tag")
    publish(version)
    row, _made = receive(form)
    first, second = stored(row), stored(row)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Response._base_manager.select_for_update().get(pk=row.pk)
            contender, pid = submit(pool, second.deliver)
            wait_for_lock(pid, contender)
            made = first.deliver()
        again = contender.result(timeout=10)

    with system_context(reason="webforms concurrency test read"):
        assert Tag._base_manager.filter(name="Ada Example").count() == 1
        assert ResponseRecord._base_manager.filter(response_id=row.pk).count() == 1
    assert (made.pk, stored(row).delivery, stored(row).delivery_attempts) == (again.pk, Delivery.DELIVERED, 1)


def test_an_edit_that_began_before_a_publication_waits_for_it_and_is_refused() -> None:
    """The save locks the version's row, finds it published, and changes nothing the form now serves."""

    form, version = form_with()
    with system_context(reason="webforms concurrency test setup"):
        editor = FormVersion.objects.get(pk=version.pk)
    editor.definition = SHORTER

    def edit() -> None:
        with system_context(reason="webforms concurrency test edit"):
            editor.save()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            publish(version)
            contender, pid = submit(pool, edit)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError):
            contender.result(timeout=10)

    kept, served = stored(version), stored(form)
    assert (kept.definition, kept.published_at is not None, served.published_version_id) == (DEFINITION, True, kept.pk)


def test_an_edit_that_commits_before_a_publication_is_what_gets_judged_and_published() -> None:
    """The publication waits on the version's row and reads it again: what it publishes is what it checked."""

    form, version = form_with()
    with system_context(reason="webforms concurrency test setup"):
        publisher = FormVersion.objects.get(pk=version.pk)
        editor = FormVersion.objects.get(pk=version.pk)

    def publish_stale() -> None:
        with system_context(reason="webforms concurrency test publish"):
            publisher.publish()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="webforms concurrency test edit"):
            editor.personal_questions = None
            editor.save()
            contender, pid = submit(pool, publish_stale)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError) as refused:
            contender.result(timeout=10)

    assert "personal_questions" in refused.value.message_dict
    assert (stored(version).published_at, stored(form).published_version_id) == (None, None)


def test_an_answer_the_database_could_not_store_is_refused_before_it_reaches_it() -> None:
    form, version = form_with()
    publish(version)

    with pytest.raises(ValidationError) as refused:
        receive(form, answers={**ANSWERS, "name": "Ada\x00"})

    assert "name" in refused.value.message_dict
    assert not Response._base_manager.filter(form=form).exists()


@pytest.mark.parametrize("contender", ["another form", "the same form"])
def test_an_address_two_forms_take_at_once_goes_to_the_first(contender: str) -> None:
    """The second insert waits on the first's uncommitted slug; another form is refused, the same form keeps it."""

    first, _version = form_with()
    second = first if contender == "the same form" else form_with()[0]

    def take(form: Any) -> None:
        with system_context(reason="webforms concurrency test"):
            Form._base_manager.get(pk=form.pk).ensure_aliases(["shared"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            take(first)
            contending, pid = submit(pool, lambda: take(second))
            wait_for_lock(pid, contending)
        if second is first:
            contending.result(timeout=10)
        else:
            with pytest.raises(ValidationError) as refused:
                contending.result(timeout=10)
            assert refused.value.message_dict == {"aliases": ["The address forms/shared belongs to another form."]}

    assert list(FormAlias._base_manager.values_list("slug", "form_id")) == [("shared", first.pk)]
