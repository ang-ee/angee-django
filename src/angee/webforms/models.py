"""Web forms: public forms and surveys, the responses they keep, and what a response becomes.

A :class:`Form` is what staff manage: whether it takes submissions, which
:class:`FormVersion` the public sees, and the target a response is handed to
(:mod:`angee.webforms.targets`). A version holds the questions, as a checked
subset of a SurveyJS survey definition (:mod:`angee.webforms.validators`), in
every language the form is written in. A published version never changes: a
:class:`Response` pins the version it answered, so its answers stay readable
whatever the form asks later. A :class:`FormAlias` is a public address of a form,
optionally in one language.

**A response is evidence.** It is kept once, as received, before anything is made
from it (:meth:`ResponseManager.receive`); a client that sends the same
``submission_id`` again gets the response it already made. Its rows are closed
to generic edits: only the owner verbs here change one, to record what became of
it (:meth:`Response.deliver`), to blank a person's answers
(:meth:`Response.erase`) or to remove it when its form's retention has passed
(:meth:`ResponseManager.purge_expired`). A response carries no history, so an
erased answer is kept nowhere.

**Personal data.** A response holds the answers, the language and the client's
submission id; no address of the sender and no browser detail. Each version
states which of its questions hold personal data (``personal_questions``; it
cannot be published until it has, even if none do) and may name the one whose
answer finds a person's responses (``contact_question``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.fields import DiagnosticTextField, ImplClassField, StateField
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.models.roles import role_anchor
from angee.base.models.transitions import StateTransitions, save_state, transition
from angee.base.models.trash import ArchiveMixin, ArchiveQuerySet
from angee.base.querysets import AngeeQuerySet, ClosedUpdateQuerySet
from angee.base.refs import generic_pointer_target
from angee.jobs.enqueue import enqueue_task
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.webforms.constants import DELIVER_RESPONSE_TASK
from angee.webforms.targets import FormTarget
from angee.webforms.validators import (
    LANGUAGE_TAG,
    OTHER_SUFFIX,
    missing_translations,
    questions,
    validate_answers,
    validate_definition,
    validate_submission_id,
)

SLUG = RegexValidator(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", "Use lower-case letters and numbers, with single hyphens between.")
THEME_ID = RegexValidator(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$", "Enter a theme id, such as acme.paper.")
LANGUAGE = RegexValidator(LANGUAGE_TAG, "Enter a language tag, such as en or pt-BR.")


def _model(name: str) -> Any:
    """Return a composed web forms model through the app registry."""

    return apps.get_model("webforms", name)


class FormUnavailable(LookupError):
    """The form takes no submission: it is not published, or it is closed or archived."""


class FormChanged(ValueError):
    """The form's published version changed after the page that sent this submission loaded."""


class FormStatus(models.TextChoices):
    """Whether a form takes submissions."""

    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    CLOSED = "closed", "Closed"


class Delivery(models.TextChoices):
    """What became of a kept response."""

    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    DISMISSED = "dismissed", "Dismissed"


class FormQuerySet[FormT: Form](ArchiveQuerySet[FormT], AngeeHistoryQuerySet[FormT]):
    """Archive read scopes over the REBAC-scoped form queryset."""

    def accepting(self) -> FormQuerySet[FormT]:
        """Keep the forms that take submissions now: published, with a version, and not archived."""

        return self.filter(status=FormStatus.PUBLISHED, published_version__isnull=False, is_archived=False)


FormManager = AngeeHistoryManager.from_queryset(FormQuerySet)


class Form(HistoryMixin, ArchiveMixin, AngeeDataModel, concrete=True):
    """One public form: its name for staff, whether it takes submissions, and what a response becomes."""

    sqid_prefix = "wbf_"

    name = models.CharField(max_length=160)
    status = StateField(choices_enum=FormStatus, default=FormStatus.DRAFT)
    published_version = models.ForeignKey(
        "webforms.FormVersion", null=True, blank=True, editable=False, on_delete=models.PROTECT, related_name="+",
    )
    """The version the public answers; set by :meth:`FormVersion.publish`."""
    target_class = ImplClassField(FormTarget, default="store")
    target_config = models.JSONField(default=dict, blank=True)
    """The target's own configuration, checked by its ``config_model``: how answers become its record's fields."""
    theme_id = models.CharField(max_length=64, blank=True, default="", validators=[THEME_ID])
    """The theme the public page wears; empty follows the deployment's default."""
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    """How many days a delivered response is kept; empty keeps it."""

    status_transitions = StateTransitions(
        status,
        {
            FormStatus.DRAFT: [FormStatus.PUBLISHED],
            FormStatus.PUBLISHED: [FormStatus.CLOSED],
            FormStatus.CLOSED: [FormStatus.PUBLISHED],
        },
    )

    objects = FormManager()

    class Meta:
        """Django model options for a form."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "webforms/form"

    def __str__(self) -> str:
        """Return the form's name."""

        return self.name

    def clean(self) -> None:
        """Check the target's configuration against the target the form selects."""

        super().clean()
        try:
            self.target()
        except ValidationError as error:
            raise ValidationError({"target_config": error.messages}) from error

    def target(self) -> FormTarget:
        """Return this form's target, bound to it and to its checked configuration."""

        return self.get_target_class_impl()(self)

    @transition(
        status, source=[FormStatus.DRAFT, FormStatus.CLOSED], target=FormStatus.PUBLISHED, on_success=save_state,
    )
    def open(self) -> None:
        """Take submissions on the published version."""

        if self.published_version_id is None:
            raise ValidationError({"status": "Publish a version before the form takes submissions."})

    @transition(status, source=FormStatus.PUBLISHED, target=FormStatus.CLOSED, on_success=save_state)
    def close(self) -> None:
        """Stop taking submissions; what was received stays."""


class FormVersionQuerySet(ClosedUpdateQuerySet[Any], AngeeQuerySet):
    """Versions change through their own saves and verbs, never through a collection edit."""


FormVersionManager = AngeeManager.from_queryset(FormVersionQuerySet)


class FormVersion(ImmutableFieldsMixin, AngeeDataModel, concrete=True):
    """The questions of a form at one moment, in every language it is written in.

    A version is numbered after its form's last when it is made. While it is
    unpublished its content may be revised; once published it is frozen, and a
    change is a new version.
    """

    sqid_prefix = "wbv_"
    CONTENT_FIELDS: ClassVar[tuple[str, ...]] = (
        "definition", "languages", "default_language", "personal_questions", "contact_question",
    )
    immutable_fields = ("form_id", "number", "published_at", *CONTENT_FIELDS)

    form = models.ForeignKey("webforms.Form", on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField(default=0, editable=False)
    definition = models.JSONField(default=dict)
    """The checked subset of a SurveyJS survey definition (:func:`~angee.webforms.validators.validate_definition`)."""
    languages = models.JSONField(default=list)
    """The language tags the form is written in."""
    default_language = models.CharField(max_length=35, validators=[LANGUAGE])
    """The language a text map holds under ``default``."""
    personal_questions = models.JSONField(null=True, blank=True)
    """The names of the questions that hold personal data. Null is not yet stated; an empty list states none."""
    contact_question = models.CharField(max_length=64, blank=True, default="")
    """The question whose answer finds a person's responses, typically their email address."""
    published_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = FormVersionManager()

    class Meta:
        """Django model options for a form version."""

        abstract = True
        ordering = ("form", "-number")
        rebac_resource_type = "webforms/form_version"
        constraints = (
            models.UniqueConstraint(fields=("form", "number"), name="%(app_label)s_version_number"),
        )

    def __str__(self) -> str:
        """Return the form's name and this version's number."""

        return f"{self.form} v{self.number}"

    def clean(self) -> None:
        """Refuse a definition outside the stored subset, and personal questions the definition does not ask."""

        super().clean()
        validate_definition(self.definition, languages=self.languages, default_language=self.default_language)
        asked = {question["name"] for question in questions(self.definition)}
        errors: dict[str, str] = {}
        personal = self.personal_questions
        if personal is not None and (
            not isinstance(personal, list) or any(name not in asked for name in personal)
        ):
            errors["personal_questions"] = "Name questions this version asks."
        if self.contact_question and self.contact_question not in asked:
            errors["contact_question"] = "Name a question this version asks."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Number a new version after its form's last, and keep an unpublished one open to revision."""

        if self._state.adding:
            with transaction.atomic(using=kwargs.get("using")):
                if not self.number:
                    _model("Form").system_queryset(lock=("self",)).filter(pk=self.form_id).first()
                    last = type(self)._base_manager.filter(form_id=self.form_id).aggregate(last=models.Max("number"))
                    self.number = (last["last"] or 0) + 1
                super().save(*args, **kwargs)
            return
        if not self._published():
            self.allow_immutable_save(*self.CONTENT_FIELDS)
        super().save(*args, **kwargs)

    def _published(self) -> bool:
        """Whether the stored row is published, whatever this instance says."""

        return bool(type(self)._base_manager.filter(pk=self.pk, published_at__isnull=False).exists())

    def publish(self) -> None:
        """Publish this version: its form serves it from now on, and it never changes again.

        Refused while a text lacks one of the form's languages, or while the
        version has not stated which questions hold personal data. A form still in
        draft starts taking submissions; a published or closed one keeps its
        state. Publishing a version published before serves it again.
        """

        self.require_access("write")
        with transaction.atomic():
            form = _model("Form").system_queryset(lock=("self",)).get(pk=self.form_id)
            if not self._published():
                self.clean()
                missing = missing_translations(
                    self.definition, languages=self.languages, default_language=self.default_language,
                )
                if missing:
                    raise ValidationError({
                        "definition": [f"{pointer}: not written in every language of the form." for pointer in missing],
                    })
                if self.personal_questions is None:
                    raise ValidationError({
                        "personal_questions": "State which questions hold personal data, even if none do.",
                    })
                self.allow_immutable_save("published_at")
                self.published_at = timezone.now()
                self.save(update_fields=["published_at", "updated_at"])
            if form.published_version_id != self.pk:
                form.published_version_id = self.pk
                form.save(update_fields=["published_version", "updated_at"])
            if form.status == FormStatus.DRAFT:
                form.open()


class FormAliasQuerySet(AngeeQuerySet):
    """Public addresses, and the ones that lead somewhere now."""

    def serving(self) -> FormAliasQuerySet:
        """Keep the aliases of forms that take submissions now."""

        return self.filter(
            form__status=FormStatus.PUBLISHED, form__published_version__isnull=False, form__is_archived=False,
        )


FormAliasManager = AngeeManager.from_queryset(FormAliasQuerySet)


class FormAlias(AngeeDataModel, concrete=True):
    """A public address of a form, optionally in one language."""

    sqid_prefix = "wba_"

    form = models.ForeignKey("webforms.Form", on_delete=models.CASCADE, related_name="aliases")
    slug = models.CharField(max_length=64, unique=True, validators=[SLUG])
    language = models.CharField(max_length=35, blank=True, default="", validators=[LANGUAGE])
    """The language this address opens the form in; empty leaves it to the visitor's."""

    objects = FormAliasManager()

    class Meta:
        """Django model options for a form alias."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "webforms/form_alias"

    def __str__(self) -> str:
        """Return the public slug."""

        return self.slug


class ResponseQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Responses are kept as received; only the owner verbs change or remove one."""

    def undelivered(self) -> ResponseQuerySet:
        """Keep the responses still to be handed to their target."""

        return self.filter(delivery__in=(Delivery.PENDING, Delivery.FAILED))

    def expired(self, *, now: Any) -> ResponseQuerySet:
        """Keep the delivered responses past their form's retention."""

        # One bound per retention length in use: a duration multiplied in SQL is not portable.
        lengths = _model("Form")._base_manager.exclude(retention_days__isnull=True).values_list(
            "retention_days", flat=True,
        ).order_by().distinct()
        past = models.Q(pk__in=())
        for days in lengths:
            past |= models.Q(form__retention_days=days, delivered_at__lt=now - timedelta(days=days))
        return self.filter(past, delivery=Delivery.DELIVERED)


class ResponseManager(AngeeManager.from_queryset(ResponseQuerySet)):  # type: ignore[misc]
    """Receive and retire responses."""

    def receive(self, form: Any, *, version: int, submission_id: Any, language: Any, answers: Any) -> tuple[Any, bool]:
        """Keep one submission to ``form``; return the response and whether this call made it.

        Named system work for the public endpoint, after its guard. ``version``
        is the number the sender rendered: a form that has since published
        another raises :class:`FormChanged`, so its answers are never judged by
        questions the sender did not see, and a form that takes no submission
        raises :class:`FormUnavailable`. A ``submission_id`` the form already
        holds returns that response unchanged. A language the version is not
        written in falls back to its default. The response is handed to the
        form's target after the transaction commits.
        """

        receipt = validate_submission_id(submission_id)
        with transaction.atomic(), system_context(reason="webforms.response.receive"):
            current = _model("Form").system_queryset(lock=("self",)).accepting().filter(pk=form.pk).first()
            if current is None:
                raise FormUnavailable
            kept = self.model._base_manager.filter(form_id=current.pk, submission_id=receipt).first()
            if kept is not None:
                return kept, False
            published = _model("FormVersion")._base_manager.get(pk=current.published_version_id)
            if published.number != version:
                raise FormChanged
            row = self.model(
                form_id=current.pk, version_id=published.pk, submission_id=receipt,
                language=language if language in published.languages else published.default_language,
                answers=validate_answers(published.definition, answers),
            )
            row._owner_insert()
            enqueue_task(DELIVER_RESPONSE_TASK, kwargs={"pk": row.pk}, robust=True)
        return row, True

    def purge_expired(self, *, now: Any) -> int:
        """Remove the delivered responses past their form's retention; return how many."""

        with system_context(reason="webforms.response.purge_expired"):
            _total, removed = self.get_queryset().expired(now=now).owner_delete()
        return int(removed.get(self.model._meta.label, 0))


class Response(AppendOnlyModel, AngeeDataModel, concrete=True):
    """One submission to a form, kept as received, and what became of it."""

    sqid_prefix = "wbr_"

    form = models.ForeignKey("webforms.Form", on_delete=models.PROTECT, related_name="responses")
    version = models.ForeignKey("webforms.FormVersion", on_delete=models.PROTECT, related_name="responses")
    submission_id = models.CharField(max_length=128)
    language = models.CharField(max_length=35)
    """The language the respondent answered in."""
    answers = models.JSONField(default=dict)
    """The answers by question name, as the version's derived schema admitted them."""
    erased_at = models.DateTimeField(null=True, blank=True)
    delivery = StateField(choices_enum=Delivery, default=Delivery.PENDING)
    delivery_error = DiagnosticTextField(blank=True, default="")
    delivered_at = models.DateTimeField(null=True, blank=True)

    objects = ResponseManager()

    class Meta:
        """Django model options for a response."""

        abstract = True
        base_manager_name = "_angee_base"
        ordering = ("-created_at", "-pk")
        rebac_resource_type = "webforms/response"
        constraints = (
            models.UniqueConstraint(fields=("form", "submission_id"), name="%(app_label)s_response_submission"),
        )
        indexes = (models.Index(fields=("delivery", "delivered_at"), name="%(app_label)s_response_delivery"),)

    def __str__(self) -> str:
        """Return the form and the receipt id."""

        return f"{self.form}: {self.submission_id}"

    def _stored(self) -> Any:
        """This row's collection, for a compare-and-set owner write."""

        return type(self).objects.filter(pk=self.pk)

    def record(self) -> Any:
        """Return the record this response became, or ``None``."""

        link = _model("ResponseRecord")._base_manager.filter(response_id=self.pk).first()
        return link.record if link is not None else None

    def deliver(self) -> Any:
        """Hand this response to its form's target once; return the record it became, if any.

        Named system work: the delivery task calls it, and staff do to try a
        failed one again. The target's verb, the link to the record it made and
        the delivered state are one transaction, so a failure leaves no record
        behind, and a response that already has its link is only marked. A
        failure is recorded on the response, where staff see it, and raised.
        """

        try:
            with transaction.atomic(), system_context(reason="webforms.response.deliver"):
                row = self._stored().undelivered().lock_if_supported().first()
                if row is None:
                    return self.record()
                made = row.record()
                if made is None:
                    made = row.form.target().deliver(row)
                    if made is not None:
                        content_type, object_id = generic_pointer_target(made)
                        _model("ResponseRecord").objects.create(
                            response_id=row.pk, content_type=content_type, object_id=str(object_id),
                        )
                row._stored().undelivered().owner_update(
                    delivery=Delivery.DELIVERED, delivered_at=timezone.now(), delivery_error="",
                )
                return made
        except Exception as error:
            with system_context(reason="webforms.response.deliver"):
                self._stored().undelivered().owner_update(
                    delivery=Delivery.FAILED, delivery_error=f"{type(error).__name__}: {error}",
                )
            raise

    def dismiss(self) -> bool:
        """Give up delivering a response that could not be; return whether this call did."""

        self.require_access("write")
        return bool(self._stored().undelivered().owner_update(delivery=Delivery.DISMISSED))

    def erase(self) -> bool:
        """Blank the answers its version marks as personal; return whether this call did.

        The row stays, with its other answers, its delivery and its link, so
        counts and what was made from it still stand.
        """

        self.require_access("write")
        personal = set(self.version.personal_questions or ())
        kept = {
            name: value for name, value in self.answers.items() if name.removesuffix(OTHER_SUFFIX) not in personal
        }
        return bool(self._stored().filter(erased_at__isnull=True).owner_update(answers=kept, erased_at=timezone.now()))


class ResponseRecord(RecordRefMixin, AngeeDataModel, concrete=True):
    """The record one response became.

    It is what a retried delivery finds instead of making a second record, and
    the path from that record back to the answers it came from. It follows the
    record through a merge and goes when the record or the response does; it
    never blocks either.
    """

    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    accepts_any_record_target = True
    sqid_prefix = "wbl_"

    response = models.OneToOneField("webforms.Response", on_delete=models.CASCADE, related_name="record_link")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=255)
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for a response's record link."""

        abstract = True
        rebac_resource_type = "webforms/response_record"
        indexes = (models.Index(fields=("content_type", "object_id"), name="%(app_label)s_record_target"),)


WebformsRole = role_anchor("webforms/role", name="WebformsRole")
"""Role anchor for ``webforms/role``: the ``webforms_admin`` role manages forms and reads their responses."""
