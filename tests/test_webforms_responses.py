"""A form's versions, the responses it keeps, and what a response becomes.

Forms are managed surfaces, so their rows are made under ``system_context``; the
verbs run as the form's manager where they authorize. ``tags.Tag`` stands in for
the record a target makes. Every name and address is invented.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rebac import actor_context, system_context

from angee.tags.testing.models import Tag
from angee.webforms.models import Delivery, FormChanged, FormStatus, FormUnavailable
from angee.webforms.targets import FormTarget
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response, ResponseRecord
from tests.conftest import create_platform_admin, create_user

pytestmark = pytest.mark.django_db

DEFINITION: dict[str, Any] = {
    "title": {"default": "Tell us", "es": "Cuéntanos"},
    "elements": [
        {"type": "text", "name": "name", "isRequired": True, "title": {"default": "Your name", "es": "Tu nombre"}},
        {"type": "text", "name": "email", "inputType": "email", "isRequired": True},
        {
            "type": "radiogroup", "name": "size", "showOtherItem": True,
            "choices": [{"value": "small"}, {"value": "large"}],
        },
    ],
}
ANSWERS = {"name": "Ada Example", "email": "ada@example.test", "size": "other", "size-Comment": "Huge"}


class TagTarget(FormTarget):
    """A target that makes one tag named after the respondent (registered in ``tests/autoconfig.py``)."""

    key = "tag"

    def deliver(self, response: Any) -> Any:
        return Tag.objects.create(name=response.answers["name"])


class RefusingTarget(FormTarget):
    """A target whose owner refuses."""

    key = "refusing"

    def deliver(self, response: Any) -> Any:
        Tag.objects.create(name="left behind?")
        raise ValidationError("The owner refused.")


def form_with(target: str = "store", **fields: Any) -> tuple[Any, Any]:
    """A form and its first version, unpublished."""

    with system_context(reason="webforms tests setup"):
        form = Form.objects.create(name="Feedback", target_class=target, **fields)
        version = FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en", "es"], default_language="en",
            personal_questions=["name", "email"], contact_question="email",
        )
    return form, version


def published(target: str = "store", **fields: Any) -> tuple[Any, Any]:
    form, version = form_with(target, **fields)
    with system_context(reason="webforms tests setup"):
        version.publish()
    form.refresh_from_db()
    return form, version


def receive(form: Any, receipt: str = "r-1", **facts: Any) -> tuple[Any, bool]:
    values = {"version": 1, "submission_id": receipt, "language": "es", "answers": ANSWERS, **facts}
    return Response.objects.receive(form, **values)


def stored(row: Any) -> Any:
    with system_context(reason="webforms tests read"):
        return type(row)._base_manager.get(pk=row.pk)


def test_versions_are_numbered_after_their_forms_last_and_an_unpublished_one_is_revised_in_place() -> None:
    form, first = form_with()
    with system_context(reason="webforms tests setup"):
        second = FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en", "es"], default_language="en",
        )
        second.languages = ["en"]
        second.definition = {"elements": [{"type": "text", "name": "name"}]}
        second.save()

    assert (first.number, second.number) == (1, 2)
    assert stored(second).languages == ["en"]


def test_publishing_freezes_a_version_and_opens_a_draft_form() -> None:
    form, version = published()

    assert (form.status, form.published_version_id) == (FormStatus.PUBLISHED, version.pk)
    assert stored(version).published_at is not None
    with system_context(reason="webforms tests setup"), pytest.raises(ValidationError):
        version.definition = {"elements": [{"type": "text", "name": "other"}]}
        version.save()
    with system_context(reason="webforms tests setup"), pytest.raises(ValidationError):
        FormVersion.objects.filter(pk=version.pk).update(default_language="es")
    assert stored(version).definition == DEFINITION


@pytest.mark.parametrize(("change", "field"), [
    pytest.param({"definition": {"elements": [{"type": "matrix", "name": "g"}]}}, "definition", id="not-in-the-subset"),
    pytest.param(
        {"definition": {"title": {"default": "Tell us"}, "elements": [{"type": "text", "name": "name"}]},
         "personal_questions": [], "contact_question": ""},
        "definition", id="a-text-not-in-every-language",
    ),
    pytest.param({"personal_questions": None}, "personal_questions", id="personal-data-not-stated"),
    pytest.param({"personal_questions": ["phone"]}, "personal_questions", id="a-personal-question-never-asked"),
    pytest.param({"contact_question": "phone"}, "contact_question", id="a-contact-question-never-asked"),
])
def test_a_version_that_is_not_ready_is_not_published(change: dict[str, Any], field: str) -> None:
    form, version = form_with()
    with system_context(reason="webforms tests setup"):
        for name, value in change.items():
            setattr(version, name, value)
        version.save()
        with pytest.raises(ValidationError) as refused:
            version.publish()

    assert field in refused.value.message_dict
    form.refresh_from_db()
    assert (form.status, form.published_version_id, stored(version).published_at) == (FormStatus.DRAFT, None, None)


def test_a_form_closes_and_reopens_and_a_draft_without_a_version_does_not_open() -> None:
    form, _version = published()
    empty, _draft = form_with()
    with system_context(reason="webforms tests setup"):
        form.close()
        assert stored(form).status == FormStatus.CLOSED
        with pytest.raises(FormUnavailable):
            receive(form)
        form.open()
        with pytest.raises(ValidationError):
            empty.open()

    assert stored(form).status == FormStatus.PUBLISHED
    assert stored(empty).status == FormStatus.DRAFT


def test_a_submission_is_kept_once_with_its_version_and_a_resend_is_the_same_response() -> None:
    form, version = published()

    row, made = receive(form)
    again, made_again = receive(form, answers={**ANSWERS, "name": "Someone Else"})

    assert (made, made_again, again.pk) == (True, False, row.pk)
    kept = stored(row)
    assert (kept.version_id, kept.language, kept.answers, kept.delivery) == (
        version.pk, "es", ANSWERS, Delivery.PENDING,
    )
    assert Response._base_manager.filter(form=form).count() == 1


@pytest.mark.parametrize(("facts", "refusal"), [
    pytest.param({"answers": {"name": "Ada Example"}}, ValidationError, id="answers-the-version-does-not-admit"),
    pytest.param({"submission_id": "has spaces"}, ValidationError, id="a-malformed-receipt"),
    pytest.param({"version": 7}, FormChanged, id="answers-to-another-version"),
])
def test_a_submission_that_is_not_admitted_keeps_nothing(facts: dict[str, Any], refusal: type[Exception]) -> None:
    form, _version = published()

    with pytest.raises(refusal):
        receive(form, **facts)

    assert Response._base_manager.filter(form=form).count() == 0


def test_a_language_the_version_is_not_written_in_falls_back_to_its_default() -> None:
    form, _version = published()

    row, _made = receive(form, language="fr")

    assert stored(row).language == "en"


def test_a_resend_after_the_form_closed_is_still_the_response_it_made() -> None:
    form, _version = published()
    row, _made = receive(form)
    with system_context(reason="webforms tests setup"):
        form.close()

    again, made = receive(form)

    assert (again.pk, made) == (row.pk, False)
    with pytest.raises(FormUnavailable):
        receive(form, "r-2")


def test_a_form_that_points_at_an_unpublished_version_takes_no_submission() -> None:
    """Whatever put it there, a version without a publication is never served."""

    form, version = published()
    with system_context(reason="webforms tests setup"):
        FormVersion.objects.filter(pk=version.pk).owner_update(published_at=None)

    with pytest.raises(FormUnavailable):
        receive(form)


def test_a_draft_form_takes_no_submission() -> None:
    form, _version = form_with()

    with pytest.raises(FormUnavailable):
        receive(form)


def test_delivery_makes_the_targets_record_once_and_links_it() -> None:
    form, _version = published("tag")
    row, _made = receive(form)

    made = stored(row).deliver()
    again = stored(row).deliver()

    kept = stored(row)
    assert (made.name, again.pk, kept.delivery, kept.delivery_error) == ("Ada Example", made.pk, Delivery.DELIVERED, "")
    assert kept.delivered_at is not None
    with system_context(reason="webforms tests read"):
        assert Tag._base_manager.filter(name="Ada Example").count() == 1
        assert ResponseRecord._base_manager.get(response=row).record == made


def test_a_failed_delivery_leaves_no_record_and_shows_on_the_response_until_it_is_dismissed() -> None:
    form, _version = published("refusing")
    row, _made = receive(form)

    with pytest.raises(ValidationError):
        stored(row).deliver()

    kept = stored(row)
    assert (kept.delivery, "The owner refused." in kept.delivery_error) == (Delivery.FAILED, True)
    with system_context(reason="webforms tests read"):
        assert not Tag._base_manager.filter(name="left behind?").exists()
        assert not ResponseRecord._base_manager.filter(response=row).exists()
        assert stored(row).dismiss() is True
        assert stored(row).dismiss() is False
    assert stored(row).delivery == Delivery.DISMISSED
    assert stored(row).deliver() is None, "a dismissed response is not delivered after all"


def test_a_response_the_form_keeps_as_its_record_is_delivered_with_no_link() -> None:
    form, _version = published()
    row, _made = receive(form)

    assert stored(row).deliver() is None

    assert stored(row).delivery == Delivery.DELIVERED
    assert not ResponseRecord._base_manager.filter(response=row).exists()


def test_erasing_blanks_the_personal_answers_once_and_keeps_the_rest() -> None:
    form, _version = published()
    row, _made = receive(form)
    with system_context(reason="webforms tests setup"):
        stored(row).deliver()

        assert stored(row).erase() is True
        assert stored(row).erase() is False

    kept = stored(row)
    assert kept.answers == {"size": "other", "size-Comment": "Huge"}
    assert (kept.erased_at is not None, kept.delivery) == (True, Delivery.DELIVERED)


def test_a_response_is_closed_to_every_generic_edit() -> None:
    form, _version = published()
    row, _made = receive(form)

    with system_context(reason="webforms tests setup"):
        kept = stored(row)
        kept.answers = {}
        with pytest.raises(ValidationError):
            kept.save()
        with pytest.raises(ValidationError):
            Response.objects.filter(pk=row.pk).update(answers={})
        with pytest.raises(ValidationError):
            Response.objects.filter(pk=row.pk).delete()

    assert stored(row).answers == ANSWERS


def test_retention_removes_delivered_responses_past_their_forms_days_and_no_others() -> None:
    form, _version = published(retention_days=30)
    kept_forever, _other = published()
    with system_context(reason="webforms tests setup"):
        old, _ = receive(form, "old")
        fresh, _ = receive(form, "fresh")
        waiting, _ = receive(form, "waiting")
        unlimited, _ = receive(kept_forever, "unlimited")
        for row in (old, fresh, unlimited):
            stored(row).deliver()
        now = timezone.now()
        Response.objects.filter(pk__in=(old.pk, unlimited.pk)).owner_update(delivered_at=now - timedelta(days=31))

    assert Response.objects.purge_expired(now=now) == 1

    with system_context(reason="webforms tests read"):
        left = set(Response._base_manager.values_list("submission_id", flat=True))
    assert left == {"fresh", "waiting", "unlimited"}


def test_an_alias_leads_to_a_form_only_while_it_takes_submissions() -> None:
    form, _version = published()
    draft, _unpublished = form_with()
    with system_context(reason="webforms tests setup"):
        FormAlias.objects.create(form=form, slug="feedback", language="es")
        FormAlias.objects.create(form=draft, slug="soon")
        serving = FormAlias.objects.serving()
        assert [alias.slug for alias in serving] == ["feedback"]
        form.close()
        assert list(FormAlias.objects.serving()) == []
        with pytest.raises(ValidationError):
            FormAlias(form=form, slug="Not A Slug").full_clean()


def test_a_forms_manager_publishes_and_an_outsider_does_not_even_read() -> None:
    form, version = form_with()
    admin = create_platform_admin("webforms-admin")
    outsider = create_user("webforms-outsider")

    with actor_context(outsider):
        assert list(Form.objects.filter(pk=form.pk)) == []
        assert list(Response.objects.all()) == []
    with actor_context(admin):
        FormVersion.objects.get(pk=version.pk).publish()

    assert stored(form).status == FormStatus.PUBLISHED
