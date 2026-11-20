"""A form's versions, the responses it keeps, and what a response becomes.

Forms are managed surfaces, so their rows are made under ``system_context``; the
verbs run as the form's manager where they authorize. ``tags.Tag`` stands in for
the record a target makes. Every name and address is invented.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError, RestrictedError
from django.utils import timezone
from pydantic import BaseModel
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_of
from angee.provenance.checks import check_merge_history
from angee.tags.testing.models import Tag
from angee.webforms import models as webforms_models
from angee.webforms import tasks
from angee.webforms.models import (
    FIRST_RETRY,
    MAX_DELIVERY_ATTEMPTS,
    MIN_RETENTION_DAYS,
    REDELIVERY_LEASE,
    Delivery,
    DeliveryFailed,
    FormChanged,
    FormQuerySet,
    FormStatus,
    FormUnavailable,
    retry_delay,
)
from angee.webforms.targets import DeliveryRefused, FormTarget
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


class TagNaming(BaseModel):
    """How the tag target names what it makes."""

    prefix: str = ""


class TagTarget(FormTarget):
    """A target that makes one tag named after the respondent (registered in ``tests/autoconfig.py``)."""

    key = "tag"
    config_model = TagNaming

    def permits(self, actor: Any) -> bool:
        return bool(Tag(name="A tag this target would make").with_actor(actor).has_access("create"))

    def deliver(self, response: Any) -> Any:
        return Tag.objects.create(name=self.config.prefix + response.answers["name"])


class FailingTarget(FormTarget):
    """A target that fails after writing, with a message that quotes an answer."""

    key = "failing"

    def permits(self, actor: Any) -> bool:
        return False

    def deliver(self, response: Any) -> Any:
        Tag.objects.create(name="left behind?")
        raise RuntimeError(f"Could not file {response.answers['email']}.")


class RefusingTarget(FormTarget):
    """A target whose owner refuses the response as it is."""

    key = "refusing"

    def permits(self, actor: Any) -> bool:
        return False

    def deliver(self, response: Any) -> Any:
        raise DeliveryRefused("unknown_size")


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


def test_publishing_judges_the_stored_version_not_the_instance_it_was_asked_through() -> None:
    """An edit that landed after the publisher loaded the version is what gets judged."""

    form, version = form_with()
    with system_context(reason="webforms tests setup"):
        publisher = FormVersion.objects.get(pk=version.pk)
        editor = FormVersion.objects.get(pk=version.pk)
        editor.personal_questions = None
        editor.save()
        with pytest.raises(ValidationError) as refused:
            publisher.publish()

    assert "personal_questions" in refused.value.message_dict
    assert stored(version).published_at is None


def test_a_save_that_began_before_the_publication_finds_the_version_frozen() -> None:
    form, version = form_with()
    with system_context(reason="webforms tests setup"):
        editor = FormVersion.objects.get(pk=version.pk)
        FormVersion.objects.get(pk=version.pk).publish()
        editor.definition = {"elements": [{"type": "text", "name": "name"}]}
        with pytest.raises(ValidationError):
            editor.save()

    kept = stored(version)
    assert (kept.definition, kept.published_at is not None) == (DEFINITION, True)
    form.refresh_from_db()
    assert form.published_version_id == version.pk


def test_a_published_version_is_closed_to_collection_edits_through_every_manager() -> None:
    form, version = published()
    with system_context(reason="webforms tests setup"):
        for manager in (FormVersion.objects, FormVersion._base_manager):
            with pytest.raises(ValidationError):
                manager.filter(pk=version.pk).update(definition={"elements": [{"type": "text", "name": "x"}]})

    assert stored(version).definition == DEFINITION


def test_a_version_is_never_born_published_and_names_only_questions_as_personal() -> None:
    form, _version = form_with()
    with system_context(reason="webforms tests setup"):
        born = FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en", "es"], default_language="en",
            published_at=timezone.now(),
        )
        born.personal_questions = [["name"]]
        with pytest.raises(ValidationError) as refused:
            born.full_clean()

    assert stored(born).published_at is None
    assert "personal_questions" in refused.value.message_dict


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
    form, _version = published("failing")
    row, _made = receive(form)

    with pytest.raises(DeliveryFailed):
        stored(row).deliver()

    kept = stored(row)
    assert (kept.delivery, kept.delivery_error, kept.delivery_attempts) == (Delivery.FAILED, "RuntimeError", 1)
    with system_context(reason="webforms tests read"):
        assert not Tag._base_manager.filter(name="left behind?").exists()
        assert not ResponseRecord._base_manager.filter(response=row).exists()
        assert stored(row).dismiss() is True
        assert stored(row).dismiss() is False
    kept = stored(row)
    assert (kept.delivery, kept.retry_at) == (Delivery.DISMISSED, None)
    assert stored(row).deliver() is None, "a dismissed response is not delivered after all"


def test_a_failure_is_kept_logged_and_raised_without_what_it_says(caplog: pytest.LogCaptureFixture) -> None:
    """A failure's message may quote an answer: only its class and where it was raised leave the delivery."""

    form, _version = published("failing")
    row, _made = receive(form)

    with caplog.at_level("WARNING", logger="angee.webforms.models"), pytest.raises(DeliveryFailed) as failed:
        stored(row).deliver()

    assert (failed.value.reason, failed.value.__context__, failed.value.__cause__) == ("RuntimeError", None, None)
    assert "RuntimeError" in caplog.text and "in deliver" in caplog.text
    assert ANSWERS["email"] not in caplog.text + stored(row).delivery_error + str(failed.value)
    assert tasks.deliver_response(row.pk) is False, "the task reports the failure and raises nothing"


def test_a_failure_is_tried_again_later_and_ever_later_until_it_waits_for_staff() -> None:
    form, _version = published("failing")
    row, _made = receive(form)
    assert stored(row).retry_at - stored(row).created_at < FIRST_RETRY + timedelta(seconds=5)

    waits = []
    for _attempt in range(MAX_DELIVERY_ATTEMPTS):
        before = timezone.now()
        with pytest.raises(DeliveryFailed):
            stored(row).deliver()
        kept = stored(row)
        waits.append(kept.retry_at and kept.retry_at - before)

    assert [retry_delay(attempt) for attempt in (1, 2, 3, 20)] == [
        timedelta(minutes=5), timedelta(minutes=10), timedelta(minutes=20), timedelta(hours=6),
    ]
    slack = timedelta(seconds=5)
    assert all(retry_delay(n + 1) <= wait < retry_delay(n + 1) + slack for n, wait in enumerate(waits[:-1]))
    assert (waits[-1], kept.delivery_attempts, kept.delivery) == (None, MAX_DELIVERY_ATTEMPTS, Delivery.FAILED)


def test_a_response_its_targets_owner_refuses_is_named_by_the_code_and_not_tried_again() -> None:
    form, _version = published("refusing")
    row, _made = receive(form)

    with pytest.raises(DeliveryFailed) as failed:
        stored(row).deliver()

    kept = stored(row)
    assert failed.value.reason == "refused: unknown_size"
    assert (kept.delivery, kept.delivery_error, kept.retry_at) == (Delivery.FAILED, "refused: unknown_size", None)
    with pytest.raises(ValueError, match="short lower-case name"):
        DeliveryRefused("The size Huge is unknown.")


def test_the_reconciler_queues_what_is_due_once_and_leaves_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost task or a failure is delivered by the next tick that finds it due."""

    queued: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        webforms_models, "enqueue_task", lambda name, *, kwargs, **options: queued.append((name, kwargs["pk"])),
    )
    form, _version = published("tag")
    lost, _ = receive(form, "lost")
    recent, _ = receive(form, "recent")
    done, _ = receive(form, "done")
    gone, _ = receive(form, "gone")
    with system_context(reason="webforms tests setup"):
        stored(done).deliver()
        stored(gone).dismiss()
    queued.clear()
    now = timezone.now() + FIRST_RETRY + timedelta(minutes=1)
    with system_context(reason="webforms tests setup"):
        Response.objects.filter(pk=recent.pk).owner_update(retry_at=now + timedelta(hours=1))

    first = Response.objects.redeliver(now=now)
    again = Response.objects.redeliver(now=now + timedelta(minutes=1))

    assert (first, again, queued) == (1, 0, [("webforms.deliver_response", lost.pk)])
    assert stored(lost).retry_at == now + REDELIVERY_LEASE
    assert tasks.deliver_response(lost.pk) is True
    assert (stored(lost).delivery, stored(lost).retry_at) == (Delivery.DELIVERED, None)
    assert Response.objects.redeliver(now=now + timedelta(days=1)) == 1, "the one left waiting, when its time comes"


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


def test_deleting_the_record_a_response_became_erases_its_personal_answers_and_drops_the_link() -> None:
    form, _version = published("tag")
    row, _made = receive(form)
    other, _made = receive(form, "r-2", answers={**ANSWERS, "name": "Bo Example"})
    with system_context(reason="webforms tests setup"):
        made = stored(row).deliver()
        stored(other).deliver()

        made.delete()

    kept, untouched = stored(row), stored(other)
    assert (kept.answers, kept.erased_at is not None) == ({"size": "other", "size-Comment": "Huge"}, True)
    assert (untouched.answers["name"], untouched.erased_at) == ("Bo Example", None)
    with system_context(reason="webforms tests read"):
        assert not ResponseRecord._base_manager.filter(response=row).exists()
        assert ResponseRecord._base_manager.filter(response=other).exists()


def test_the_owner_of_a_record_erases_the_responses_it_was_made_from() -> None:
    form, _version = published("tag")
    row, _made = receive(form)
    with system_context(reason="webforms tests setup"):
        made = stored(row).deliver()

    assert Response.objects.erase_for_record(made) == 1
    assert Response.objects.erase_for_record(made) == 0

    assert stored(row).answers == {"size": "other", "size-Comment": "Huge"}
    with system_context(reason="webforms tests read"):
        assert ResponseRecord._base_manager.get(response=row).record == made, "the record and its link stay"


def test_a_persons_responses_are_found_by_their_contact_answer_across_forms() -> None:
    first, _version = published()
    second, _version = published()
    nameless, unnamed = form_with()
    with system_context(reason="webforms tests setup"):
        unnamed.contact_question = ""
        unnamed.save()
        unnamed.publish()
        mine, _ = receive(first, "mine")
        also_mine, _ = receive(second, "also", answers={**ANSWERS, "email": "ADA@Example.test"})
        receive(first, "theirs", answers={**ANSWERS, "email": "bo@example.test"})
        receive(nameless, "unnamed")

        found = set(Response.objects.answered_by(" Ada@Example.test ").values_list("pk", flat=True))
        nobody = Response.objects.answered_by("  ").count()
        assert stored(also_mine).erase() is True

    assert (found, nobody) == ({mine.pk, also_mine.pk}, 0)
    assert (stored(mine).contact, stored(also_mine).contact) == ("ada@example.test", "")
    with system_context(reason="webforms tests read"):
        assert list(Response.objects.answered_by("ada@example.test").values_list("pk", flat=True)) == [mine.pk]


def test_the_contact_question_is_one_the_version_states_personal() -> None:
    """A way to reach a person is personal data: erasing a response must take it too."""

    form, version = form_with()
    version.personal_questions = ["name"]

    with system_context(reason="webforms tests setup"), pytest.raises(ValidationError) as refused:
        version.full_clean()

    assert "list it among the personal questions" in refused.value.message_dict["contact_question"][0]


def test_a_forms_target_is_not_changed_through_a_collection_edit() -> None:
    """The target's owner is asked one form at a time; an edit of many forms at once would skip it."""

    form, _version = form_with()
    with system_context(reason="webforms tests setup"):
        with pytest.raises(ValidationError):
            Form.objects.filter(pk=form.pk).update(target_class="tag")
        with pytest.raises(ValidationError):
            Form.objects.filter(pk=form.pk).update(name="Renamed", target_config={"prefix": "x"})
        Form.objects.filter(pk=form.pk).update(name="Renamed")

    assert (stored(form).name, stored(form).target_class) == ("Renamed", "store")


def test_a_target_that_returns_what_no_record_reference_reaches_fails_and_links_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing would erase the answers when such a row goes, so it is never taken as the record."""

    monkeypatch.setattr(TagTarget, "deliver", lambda self, response: ContentType.objects.get_for_model(Tag))
    form, _version = published("tag")
    row, _made = receive(form)

    with pytest.raises(DeliveryFailed) as failed:
        stored(row).deliver()

    assert failed.value.reason == "ImproperlyConfigured"
    with system_context(reason="webforms tests read"):
        assert not ResponseRecord._base_manager.filter(response=row).exists()


def test_retention_removes_every_response_received_longer_ago_than_its_form_keeps_one() -> None:
    form, _version = published("failing", retention_days=30)
    kept_forever, _other = published()
    with system_context(reason="webforms tests setup"):
        old, _ = receive(form, "old")
        failed, _ = receive(form, "failed")
        given_up, _ = receive(form, "given-up")
        fresh, _ = receive(form, "fresh")
        unlimited, _ = receive(kept_forever, "unlimited")
        with pytest.raises(DeliveryFailed):
            stored(failed).deliver()
        stored(given_up).dismiss()
        stored(unlimited).deliver()
        now = timezone.now()
        long_ago = Response.objects.filter(pk__in=(old.pk, failed.pk, given_up.pk, unlimited.pk))
        long_ago.owner_update(created_at=now - timedelta(days=31))

    assert Response.objects.purge_expired(now=now) == 3

    with system_context(reason="webforms tests read"):
        left = set(Response._base_manager.values_list("submission_id", flat=True))
    assert left == {"fresh", "unlimited"}


def test_an_attempt_that_never_reports_still_counts_and_the_last_one_leaves_the_response_to_staff() -> None:
    """A worker that dies on one response every time is not handed it for ever."""

    form, _version = published("tag")
    with system_context(reason="webforms tests setup"):
        dying, _ = receive(form, "dying")
        last, _ = receive(form, "last")
        now = timezone.now()
        # What an attempt leaves when its worker dies after it started: one more attempt and a lease.
        Response.objects.filter(pk=dying.pk).owner_update(delivery_attempts=3, retry_at=now - timedelta(minutes=1))
        Response.objects.filter(pk=last.pk).owner_update(
            delivery_attempts=MAX_DELIVERY_ATTEMPTS, retry_at=now - timedelta(minutes=1),
        )

    assert Response.objects.redeliver(now=now) == 1

    kept = stored(last)
    assert (kept.delivery, kept.delivery_error, kept.retry_at) == (Delivery.FAILED, "Unfinished", None)
    assert (stored(dying).delivery, stored(dying).retry_at) == (Delivery.PENDING, now + REDELIVERY_LEASE)
    with system_context(reason="webforms tests setup"):
        assert stored(dying)._start_attempt() is True
    started = stored(dying)
    assert started.delivery_attempts == 4, "counted before the target runs"
    assert started.retry_at >= now + retry_delay(4), "and tried again no sooner than a failed attempt would be"
    assert stored(dying).deliver() is not None
    assert (stored(dying).delivery, stored(dying).delivery_attempts, stored(dying).retry_at) == (
        Delivery.DELIVERED, 5, None,
    )


def test_a_response_keeps_naming_the_record_it_became_through_a_merge_and_is_read_from_the_survivor() -> None:
    """The link is evidence: a merge rewrites none of it, and asks for no history to undo."""

    form, _version = published("tag")
    row, _made = receive(form)
    curator = create_platform_admin("webforms-merge-curator")
    with system_context(reason="webforms tests setup"):
        made = stored(row).deliver()
        survivor = Tag.objects.create(name="Ada Lovelace")
    with actor_context(curator):
        Tag.objects.get(pk=survivor.pk).merge(records=[public_id_of(made)])

    with system_context(reason="webforms tests read"):
        link = ResponseRecord._base_manager.get(response=row)
        assert (link.object_id, stored(row)._record().pk) == (made.pk, survivor.pk)
        assert list(Response.objects.for_record(survivor).values_list("pk", flat=True)) == [row.pk]
        assert Response.objects.erase_for_record(survivor) == 1
    assert [error for error in check_merge_history() if error.obj is ResponseRecord] == []


def test_what_is_undelivered_is_counted_with_the_day_the_first_of_it_expires() -> None:
    """Retention removes an undelivered response too, so staff are shown it, and by when, first."""

    form, _version = published("failing", retention_days=30)
    kept_forever, _other = published("failing")
    with system_context(reason="webforms tests setup"):
        oldest, _ = receive(form, "oldest")
        failed, _ = receive(form, "failed")
        done, _ = receive(form, "done")
        receive(kept_forever, "kept")
        with pytest.raises(DeliveryFailed):
            stored(failed).deliver()
        Response.objects.filter(pk=done.pk).owner_update(delivery=Delivery.DELIVERED)
        received = timezone.now() - timedelta(days=20)
        Response.objects.filter(pk=oldest.pk).owner_update(created_at=received)

        annotated = Form.objects.annotate(**FormQuerySet.undelivered_expressions()).get(pk=form.pk)
        counted = (stored(form).undelivered(), annotated.undelivered(), stored(kept_forever).undelivered())

    expiry = received + timedelta(days=30)
    assert counted == ((2, expiry), (2, expiry), (1, None))
    assert (stored(oldest).expires_at, stored(done).expires_at is not None) == (expiry, True)
    with system_context(reason="webforms tests read"):
        assert Response._base_manager.get(form=kept_forever).expires_at is None


def test_every_retry_is_made_long_before_the_shortest_retention_and_none_after_it() -> None:
    """A delivery gives up well inside a day, and a response past its retention is not queued again."""

    tick = timedelta(minutes=5)
    longest = FIRST_RETRY + sum(
        (retry_delay(attempt) + REDELIVERY_LEASE + tick for attempt in range(1, MAX_DELIVERY_ATTEMPTS)), timedelta(),
    )
    assert longest < timedelta(days=MIN_RETENTION_DAYS) * 2 / 3, longest

    form, _version = published("tag", retention_days=MIN_RETENTION_DAYS)
    with system_context(reason="webforms tests setup"):
        late, _ = receive(form, "late")
        now = timezone.now()
        Response.objects.filter(pk=late.pk).owner_update(created_at=now - timedelta(days=2), retry_at=now)

    assert Response.objects.redeliver(now=now) == 0
    assert Response.objects.purge_expired(now=now) == 1


@pytest.mark.parametrize("days", [0, 36_501])
def test_a_form_keeps_its_responses_for_at_least_a_day_and_at_most_a_century(days: int) -> None:
    form, _version = form_with()
    form.retention_days = days

    with pytest.raises(ValidationError) as refused:
        form.full_clean()

    assert "retention_days" in refused.value.message_dict


def test_a_form_is_deleted_with_its_versions_while_it_holds_no_response() -> None:
    empty, version = published()
    answered, kept_version = published()
    receive(answered)
    with system_context(reason="webforms tests setup"):
        with pytest.raises(RestrictedError):
            FormVersion._base_manager.get(pk=version.pk).delete()
        with pytest.raises(ProtectedError):
            Form._base_manager.get(pk=answered.pk).delete()

        Form._base_manager.get(pk=empty.pk).delete()

        assert not FormVersion._base_manager.filter(pk=version.pk).exists()
        assert FormVersion._base_manager.filter(pk=kept_version.pk).exists()


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


def test_a_target_is_chosen_only_by_someone_its_owner_lets_make_its_records() -> None:
    """Managing forms is not making tags: the target's owner decides who points a form at it."""

    manager = create_user("webforms-only-manager")
    with system_context(reason="webforms tests setup"):
        grant_role(actor=manager, role="webforms/role:webforms_admin")
        seeded = Form.objects.create(name="Seeded", target_class="tag")
    admin = create_platform_admin("webforms-and-tags-admin")

    with actor_context(manager):
        kept = Form.objects.create(name="Survey")
        with pytest.raises(PermissionDenied):
            Form.objects.create(name="Tags from strangers", target_class="tag")
        kept.target_class = "tag"
        with pytest.raises(PermissionDenied):
            kept.save()
        theirs = Form.objects.get(pk=seeded.pk)
        theirs.name = "Renamed"
        theirs.save()
        theirs.target_config = {"prefix": "Guest: "}
        with pytest.raises(PermissionDenied):
            theirs.save()
    with actor_context(admin):
        chosen = Form.objects.create(name="Tags from the public", target_class="tag")

    assert (stored(kept).target_class, stored(chosen).target_class) == ("store", "tag")
    assert (stored(seeded).name, stored(seeded).target_config) == ("Renamed", {})


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
