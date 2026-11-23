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
(:meth:`ResponseManager.purge_expired`). A response carries no history, a
failed delivery keeps only the failure's class, and deleting the record a
response became erases the response's personal answers too, so an erased answer
stays nowhere inside web forms. What a target made from a response is a copy
this addon does not reach: the record, or the message a form posted, is its
owner's to erase.

**Personal data.** A response holds the answers, the language and the client's
submission id; no address of the sender and no browser detail. Each version
states which of its questions hold personal data (``personal_questions``; it
cannot be published until it has, even if none do) and may name the one whose
answer finds a person's responses (``contact_question``).
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, ClassVar, Self

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.utils import timezone
from rebac import PermissionDenied, system_context
from rebac.actors import is_sudo

from angee.base.fields import DiagnosticTextField, ImplClassField, StateField
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.models.roles import role_anchor
from angee.base.models.transitions import StateTransitions, save_state, transition
from angee.base.models.trash import ArchiveMixin, ArchiveQuerySet
from angee.base.permissions import instance_actor
from angee.base.querysets import AngeeQuerySet, AngeeUnscopedQuerySet, ClosedUpdateQuerySet
from angee.base.refs import generic_pointer_target
from angee.jobs.enqueue import enqueue_task
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.resources.mixins import ResourceLoadMixin
from angee.webforms.constants import DELIVER_RESPONSE_TASK
from angee.webforms.resources import FormResource
from angee.webforms.targets import DeliveryRefused, FormTarget
from angee.webforms.validators import (
    CONTACT_LIMIT,
    LANGUAGE_TAG,
    OTHER_SUFFIX,
    contact_of,
    validate_answers,
    validate_publishable,
    validate_submission_id,
    validate_version,
)

SLUG = RegexValidator(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", "Use lower-case letters and numbers, with single hyphens between.")
THEME_ID = RegexValidator(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$", "Enter a theme id, such as acme.paper.")
LANGUAGE = RegexValidator(LANGUAGE_TAG, "Enter a language tag, such as en or pt-BR.")

MIN_RETENTION_DAYS = 1
"""A day: every retry of a delivery has been made well before the shortest retention removes a response."""
MAX_RETENTION_DAYS = 36_500
"""A century: past it a form keeps its responses by leaving ``retention_days`` empty."""
MAX_DELIVERY_ATTEMPTS = 8
"""How often a failing delivery is tried before it waits for staff."""
FIRST_RETRY = timedelta(minutes=5)
LONGEST_RETRY = timedelta(hours=6)
REDELIVERY_LEASE = timedelta(minutes=15)
"""How long a response queued again is left alone, so a slow queue is not filled with it on every tick."""
REDELIVERY_BATCH = 500

logger = logging.getLogger(__name__)


def past_retention(now: datetime) -> models.Q:
    """Select the responses received longer ago than their form keeps one."""

    # One bound per retention length in use: a duration multiplied in SQL is not portable.
    lengths = _model("Form")._base_manager.filter(
        retention_days__range=(MIN_RETENTION_DAYS, MAX_RETENTION_DAYS),
    ).values_list("retention_days", flat=True).order_by().distinct()
    past = models.Q(pk__in=())
    for days in lengths:
        past |= models.Q(form__retention_days=days, created_at__lt=now - timedelta(days=days))
    return past


def retry_delay(attempts: int) -> timedelta:
    """How long after its ``attempts``-th failure a delivery is tried again: doubling from five minutes to six hours."""

    return min(FIRST_RETRY * 2 ** max(attempts - 1, 0), LONGEST_RETRY)


def _model(name: str) -> Any:
    """Return a composed web forms model through the app registry."""

    return apps.get_model("webforms", name)


class FormUnavailable(LookupError):
    """The form takes no submission: it is not published, or it is closed or archived."""


class FormChanged(ValueError):
    """The form's published version changed after the page that sent this submission loaded."""


class DeliveryFailed(Exception):
    """A response could not be handed to its target; ``reason`` is what the response keeps of it.

    The reason is the failure's class, or ``refused: <code>`` when the target's
    owner refused the response. It never carries the failure's own message,
    which may quote an answer.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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

    @staticmethod
    def undelivered_expressions() -> dict[str, Any]:
        """How many of a form's responses still wait for delivery, and when the oldest was received."""

        waiting = models.Q(responses__delivery__in=(Delivery.PENDING, Delivery.FAILED))
        return {
            "_undelivered_count": models.Count("responses", filter=waiting),
            "_undelivered_since": models.Min("responses__created_at", filter=waiting),
        }

    def update(self, **kwargs: Any) -> int:
        """Refuse a collection edit of a form's target: its owner decides who may choose it, one form at a time."""

        if {"target_class", "target_config"} & set(kwargs):
            raise ValidationError("A form's target changes through the form's own save.")
        return super().update(**kwargs)

    def accepting(self) -> FormQuerySet[FormT]:
        """Keep the forms that take submissions now: published, with a version, and not archived."""

        return self.filter(status=FormStatus.PUBLISHED, published_version__isnull=False, is_archived=False)


FormManager = AngeeHistoryManager.from_queryset(FormQuerySet)


class Form(ResourceLoadMixin, HistoryMixin, ArchiveMixin, AngeeDataModel, concrete=True):
    """One public form: its name for staff, whether it takes submissions, and what a response becomes.

    An addon declares a form as one resource row (:class:`~angee.webforms.resources.FormResource`):
    the form, the version it serves (:meth:`serve`) and its addresses (:meth:`ensure_aliases`).
    """

    resource_class = FormResource
    sqid_prefix = "wbf_"

    name = models.CharField(max_length=160)
    status = StateField(choices_enum=FormStatus, default=FormStatus.DRAFT)
    published_version = models.ForeignKey(
        "webforms.FormVersion", null=True, blank=True, editable=False, on_delete=models.RESTRICT, related_name="+",
    )
    """The version the public answers; set by :meth:`FormVersion.publish`.

    The served version cannot be deleted by itself; it goes with its form, which
    is deleted only while it holds no response.
    """
    target_class = ImplClassField(FormTarget, default="store")
    target_config = models.JSONField(default=dict, blank=True)
    """The target's own configuration, checked by its ``config_model``: how answers become its record's fields."""
    theme_id = models.CharField(max_length=64, blank=True, default="", validators=[THEME_ID])
    """The theme the public page wears; empty follows the deployment's default."""
    retention_days = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(MIN_RETENTION_DAYS), MaxValueValidator(MAX_RETENTION_DAYS)],
    )
    """How many days a response is kept after it was received, whatever became of it; empty keeps it."""

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

    def undelivered(self) -> tuple[int, datetime | None]:
        """Return how many responses still wait for delivery, and when the first of them is removed.

        Retention removes a response whatever became of it, so this is what
        staff are shown before an undelivered one goes: the count of pending
        and failed responses, and the oldest one's expiry (``None`` while there
        is none, or while the form keeps its responses). It reads the
        annotations of :meth:`FormQuerySet.undelivered_expressions` when the row
        carries them, and the acting user's own view of the responses otherwise.
        """

        if "_undelivered_count" in self.__dict__:
            count, since = self.__dict__["_undelivered_count"], self.__dict__["_undelivered_since"]
        else:
            waiting = _model("Response").objects.filter(form_id=self.pk).undelivered()
            found = waiting.aggregate(count=models.Count("pk"), since=models.Min("created_at"))
            count, since = found["count"], found["since"]
        expires = since + timedelta(days=self.retention_days) if since and self.retention_days else None
        return int(count or 0), expires

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse a target, or a configuration of it, the acting user may not choose."""

        fields = kwargs.get("update_fields")
        if fields is None or {"target_class", "target_config"} & set(fields):
            self._require_target_choice()
        super().save(*args, **kwargs)

    def _require_target_choice(self) -> None:
        """Ask the target's owner whether the acting user may point this form at it.

        A target makes its owner's records as system work, so managing forms is
        not enough to choose one. Asked only when the target or its
        configuration differs from what is stored; system work without an
        acting user (demo data, a migration) is not asked.
        """

        chosen = (str(self.target_class), self.target_config)
        if not self._state.adding:
            stored = type(self)._base_manager.filter(pk=self.pk).values_list("target_class", "target_config").first()
            if stored is not None and (str(stored[0]), stored[1]) == chosen:
                return
        actor = instance_actor(self)
        if actor is None:
            if self.is_sudo() or is_sudo():
                return
            raise PermissionDenied("Denied: choosing a form's target requires an actor.")
        if not self.target().permits(actor):
            raise PermissionDenied(f"Denied: the current actor may not point a form at {chosen[0]!r}.")

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

    def serve(self, content: Mapping[str, Any]) -> Any:
        """Serve ``content``, a version's content by field name (``FormVersion.CONTENT_FIELDS``); return its version.

        The version served now is kept when it holds exactly that content.
        Otherwise a new version holding it is made and published
        (:meth:`FormVersion.publish`), in one transaction, so content the
        publication refuses leaves no version behind. Content the form served
        before, and serves no longer, is a new version too: what is served is
        decided by the version served now, never by an older one. A field
        ``content`` leaves out takes the version's default. Like ``publish``, it
        opens a draft form and leaves a published or closed one in its state.
        """

        self.require_access("write")
        versions = _model("FormVersion")
        unknown = sorted(str(name) for name in set(content) - set(versions.CONTENT_FIELDS))
        if unknown:
            raise ValidationError({"version": (
                f"A version holds {', '.join(versions.CONTENT_FIELDS)}; it does not hold {', '.join(unknown)}."
            )})
        candidate = versions(form_id=self.pk, **content)
        with transaction.atomic():
            # The form's lock first, as publish takes it: the version served is read as it stands.
            form = _model("Form").system_queryset(lock=("self",)).get(pk=self.pk)
            served = versions._base_manager.filter(pk=form.published_version_id).first()
            if served is not None and served._content() == candidate._content():
                return served
            candidate.save()
            candidate.publish()
        self.refresh_from_db(fields=("published_version", "status", "updated_at"))
        return candidate

    def ensure_aliases(self, slugs: Iterable[str]) -> None:
        """Give the form an alias for each of ``slugs`` it lacks; never remove one.

        An alias the form already has, under a declared slug or another, stays
        as it is. A slug another form holds is refused under ``aliases``, before
        any alias is made.
        """

        self.require_access("write")
        wanted = list(dict.fromkeys(slugs))
        aliases = _model("FormAlias")
        held = dict(aliases._base_manager.filter(slug__in=wanted).values_list("slug", "form_id"))
        taken = [slug for slug in wanted if held.get(slug, self.pk) != self.pk]
        if taken:
            raise ValidationError({"aliases": [f"The address forms/{slug} belongs to another form." for slug in taken]})
        for slug in wanted:
            if slug not in held:
                alias = aliases(form=self, slug=slug)
                alias.clean_fields(exclude=["form"])
                alias.save()


class FormVersionQuerySet(ClosedUpdateQuerySet[Any], AngeeQuerySet):
    """Versions change through their own saves and verbs, never through a collection edit."""


FormVersionManager = AngeeManager.from_queryset(FormVersionQuerySet)


class FormVersionBaseQuerySet(ClosedUpdateQuerySet[Any], AngeeUnscopedQuerySet):
    """Unscoped rows for Django's own relation reads, closed to collection edits like the default manager."""


FormVersionBaseManager = models.Manager.from_queryset(FormVersionBaseQuerySet)


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

    _angee_base = FormVersionBaseManager()
    objects = FormVersionManager()

    class Meta:
        """Django model options for a form version."""

        abstract = True
        base_manager_name = "_angee_base"
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
        validate_version(**self._content())

    def _content(self) -> dict[str, Any]:
        """This version's content, as its validators take it."""

        return {
            "definition": self.definition, "languages": self.languages, "default_language": self.default_language,
            "personal_questions": self.personal_questions, "contact_question": self.contact_question,
        }

    def validate_answers(self, answers: Any) -> dict[str, Any]:
        """Return ``answers`` when this version's definition admits them; raise each refusal under its question."""

        return validate_answers(self.definition, answers)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Number a new version after its form's last, and keep only an unpublished one open to revision.

        A new version is never born published. An existing row is locked first
        and its STORED publication decides, whatever this instance says: a save
        that began before a publish committed finds the version frozen.
        """

        with transaction.atomic(using=kwargs.get("using")):
            if self._state.adding:
                self.published_at = None
                if not self.number:
                    _model("Form").system_queryset(lock=("self",)).filter(pk=self.form_id).first()
                    last = type(self)._base_manager.filter(form_id=self.form_id).aggregate(last=models.Max("number"))
                    self.number = (last["last"] or 0) + 1
            else:
                stored = list(
                    type(self).system_queryset(lock=("self",)).filter(pk=self.pk).values_list("published_at", flat=True)
                )
                if stored == [None]:
                    self.allow_immutable_save(*self.CONTENT_FIELDS)
            super().save(*args, **kwargs)

    def _refuse_unready(self) -> None:
        """Refuse publication while the definition, its translations or its personal-data statement fall short."""

        self.clean()
        validate_publishable(**self._content())

    def publish(self) -> None:
        """Publish this version: its form serves it from now on, and it never changes again.

        Refused while a text lacks one of the form's languages, or while the
        version has not stated which questions hold personal data. A form still in
        draft starts taking submissions; a published or closed one keeps its
        state. Publishing a version published before serves it again.
        """

        self.require_access("write")
        with transaction.atomic():
            # The form, then the version: the stored row is judged and stamped under its own lock,
            # so what is published is what was checked, whatever this instance was loaded as.
            form = _model("Form").system_queryset(lock=("self",)).get(pk=self.form_id)
            row = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if row.published_at is None:
                row._refuse_unready()
                row.allow_immutable_save("published_at")
                row.published_at = timezone.now()
                row.save(update_fields=["published_at", "updated_at"])
            self.published_at = row.published_at
            if form.published_version_id != self.pk:
                form.published_version_id = self.pk
                form.save(update_fields=["published_version", "updated_at"])
            if form.status == FormStatus.DRAFT:
                form.open()


class FormAliasQuerySet(AngeeQuerySet):
    """Public addresses, and the ones that lead somewhere now."""

    def serving(self) -> Self:
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
        verbose_name = "form alias"
        verbose_name_plural = "form aliases"

    def __str__(self) -> str:
        """Return the public slug."""

        return self.slug


class ResponseQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Responses are kept as received; only the owner verbs change or remove one."""

    def undelivered(self) -> Self:
        """Keep the responses still to be handed to their target."""

        return self.filter(delivery__in=(Delivery.PENDING, Delivery.FAILED))

    def due(self, *, now: datetime) -> Self:
        """Keep the undelivered responses whose next try has come, with attempts and retention left."""

        return self.undelivered().filter(
            retry_at__lte=now, delivery_attempts__lt=MAX_DELIVERY_ATTEMPTS,
        ).exclude(past_retention(now))

    def abandoned(self, *, now: datetime) -> Self:
        """Keep the undelivered responses whose last attempt started, reported nothing, and was the last allowed."""

        return self.undelivered().filter(retry_at__lte=now, delivery_attempts__gte=MAX_DELIVERY_ATTEMPTS)

    def expired(self, *, now: datetime) -> Self:
        """Keep the responses received longer ago than their form keeps one.

        Every response counts, delivered or not: what a form says it keeps for
        a time is not kept longer because nothing came of it. Staff see what is
        still undelivered and when it goes (:meth:`Form.undelivered`,
        :attr:`Response.expires_at`), and every retry has been made long before.
        """

        return self.filter(past_retention(now))

    def answered_by(self, contact: str) -> Self:
        """Keep the responses whose contact answer is ``contact``, whatever the letter case.

        The contact answer is the one a response's version names
        (``FormVersion.contact_question``), kept beside the answers in
        ``Response.contact``. This is how a person's responses are found across
        forms when they ask what is held about them.
        """

        wanted = contact_of({"contact": contact}, "contact")
        return self.filter(contact=wanted) if wanted else self.none()

    def for_record(self, record: models.Model) -> Self:
        """Keep the responses ``record`` was made from."""

        links = _model("ResponseRecord")
        return self.filter(pk__in=links._base_manager.filter(links.record_filter(record)).values("response_id"))


_ResponseManagerBase = AngeeManager.from_queryset(ResponseQuerySet)


class ResponseManager(_ResponseManagerBase):
    """Receive and retire responses."""

    def receive(
        self, form: Form, *, version: int, submission_id: object, language: object, answers: object,
    ) -> tuple[Any, bool]:
        """Keep one submission to ``form``; return the response and whether this call made it.

        Named system work for the public endpoint, after its guard. ``version``
        is the number the sender rendered: a form that has since published
        another raises :class:`FormChanged`, so its answers are never judged by
        questions the sender did not see, and a form that takes no submission
        raises :class:`FormUnavailable`. A ``submission_id`` the form already
        holds returns that response unchanged, even after the form closed. A
        language the version is not written in falls back to its default. The
        answers are judged before the form is locked, and what was read is
        checked again under the lock, so a slow submission delays nobody. The
        response is handed to the form's target after the transaction commits.
        """

        receipt = validate_submission_id(submission_id)
        forms = _model("Form")
        with system_context(reason="webforms.response.receive"):
            # A resend is answered first, whatever became of the form since.
            kept = self._kept(form.pk, receipt)
            if kept is not None:
                return kept, False
            current = forms.system_queryset().accepting().filter(pk=form.pk).first()
            if current is None:
                raise FormUnavailable
            published = _model("FormVersion")._base_manager.filter(
                pk=current.published_version_id, published_at__isnull=False,
            ).first()
            if published is None:
                raise FormUnavailable
            if published.number != version:
                raise FormChanged
            # Judged before the form is locked: the sender decides how long this takes.
            checked = published.validate_answers(answers)
            tag = language if isinstance(language, str) and language in published.languages else None
            with transaction.atomic():
                serving = forms.system_queryset(lock=("self",)).accepting().filter(
                    pk=form.pk, published_version_id=published.pk,
                )
                if not serving.exists():
                    # Closed, or serving another version, since the answers were judged.
                    still = forms.system_queryset().accepting().filter(pk=form.pk).exists()
                    raise FormChanged if still else FormUnavailable
                kept = self._kept(form.pk, receipt)
                if kept is not None:
                    return kept, False
                row: Any = self.model(
                    form_id=form.pk, version_id=published.pk, submission_id=receipt,
                    language=tag or published.default_language, answers=checked,
                    contact=contact_of(checked, published.contact_question),
                    # Should the task queued below be lost, the reconciler finds the response then.
                    retry_at=timezone.now() + FIRST_RETRY,
                )
                row._owner_insert()
                enqueue_task(DELIVER_RESPONSE_TASK, kwargs={"pk": row.pk}, robust=True)
        return row, True

    def _kept(self, form_id: Any, receipt: str) -> Any:
        """Return the response a form already holds under a submission id, or ``None``."""

        return self.model._base_manager.filter(form_id=form_id, submission_id=receipt).first()

    def redeliver(self, *, now: datetime) -> int:
        """Queue again the undelivered responses whose next try has come; return how many.

        Named system work for the scheduled task. It is what delivers a response
        whose first task was lost, failed, or died without a word: each attempt
        counts when it starts, a failure sets the next try (:func:`retry_delay`)
        and an attempt that reported nothing is due again when its lease ends,
        until :data:`MAX_DELIVERY_ATTEMPTS`. After that the response is marked
        failed and waits for staff. A queued response is left alone for
        :data:`REDELIVERY_LEASE`, and a queued task that nobody started by then
        is dropped, so a stopped worker comes back to one task per response.
        """

        with transaction.atomic(), system_context(reason="webforms.response.redeliver"):
            given_up = self.get_queryset().abandoned(now=now)
            given_up.filter(delivery_error="").owner_update(delivery_error="Unfinished")
            given_up.owner_update(delivery=Delivery.FAILED, retry_at=None)
            due = list(
                self.get_queryset().due(now=now).order_by("retry_at", "pk").lock_if_supported(skip_locked=True)
                .values_list("pk", flat=True)[:REDELIVERY_BATCH]
            )
            self.get_queryset().filter(pk__in=due).owner_update(retry_at=now + REDELIVERY_LEASE)
            for pk in due:
                enqueue_task(
                    DELIVER_RESPONSE_TASK, kwargs={"pk": pk}, robust=True, expires=REDELIVERY_LEASE.total_seconds(),
                )
        return len(due)

    def erase_for_record(self, record: models.Model) -> int:
        """Blank the personal answers of the responses ``record`` was made from; return how many this call did.

        Named system work for the owner of a record a form made, when it erases
        the person the record is about. It also runs when the record is deleted
        (:meth:`ResponseRecord.teardown_for_target`), so the answers do not
        outlive it unseen.
        """

        with transaction.atomic(), system_context(reason="webforms.response.erase_for_record"):
            rows = self.get_queryset().for_record(record).filter(erased_at__isnull=True).select_related("version")
            return sum(row._erase() for row in rows)

    def purge_expired(self, *, now: datetime) -> int:
        """Remove the responses received longer ago than their form keeps one; return how many."""

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
    contact = models.CharField(max_length=CONTACT_LIMIT, blank=True, default="", db_index=True)
    """The contact answer, trimmed and in one letter case, by which a person's responses are found; erased with it."""
    erased_at = models.DateTimeField(null=True, blank=True)
    delivery = StateField(choices_enum=Delivery, default=Delivery.PENDING)
    delivery_error = DiagnosticTextField(blank=True, default="")
    """Why the last delivery failed: the failure's class, or ``refused: <code>``. Never its message."""
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(null=True, blank=True)
    """When delivery is next tried by itself; empty once settled, refused, or out of attempts."""
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
        indexes = (
            models.Index(
                fields=("retry_at",), name="%(app_label)s_response_retry", condition=models.Q(retry_at__isnull=False),
            ),
        )

    def __str__(self) -> str:
        """Return the form and the receipt id."""

        return f"{self.form}: {self.submission_id}"

    @property
    def expires_at(self) -> datetime | None:
        """When this response is removed: its form's retention after it was received; ``None`` while kept."""

        days = self.form.retention_days
        return self.created_at + timedelta(days=days) if days else None

    def _stored(self) -> Any:
        """This row's collection, for a compare-and-set owner write."""

        return type(self).objects.filter(pk=self.pk)

    def _record(self) -> Any:
        """Return the record this response became, or ``None``; a system read for the owner's own verbs.

        A record merged into another since is read as its survivor: the link
        keeps naming what was made, and the redirect is identity.
        """

        link = _model("ResponseRecord")._base_manager.filter(response_id=self.pk).first()
        made = link.record if link is not None else None
        return made.canonical() if isinstance(made, MergeableMixin) else made

    def deliver(self) -> Any:
        """Hand this response to its form's target once; return the record it became, if any.

        Named system work: the delivery task calls it, and staff do to try a
        failed one again. The target's verb, the link to the record it made and
        the delivered state are one transaction, so a failure leaves no record
        behind, and a response that already has its link is only marked.

        A failure raises :class:`DeliveryFailed` and is recorded on the
        response, where staff see it, by its class alone: a failure's message
        and the values in its frames may quote an answer, so neither is stored,
        logged or raised. What is logged is where it was raised. The response is
        tried again later (:meth:`ResponseManager.redeliver`) unless its
        target's owner refused it (:class:`~angee.webforms.targets.DeliveryRefused`).

        The attempt is counted and its next try set under the row's lock,
        before the target runs and in a transaction of its own: an attempt whose
        worker dies reports nothing, and must still count against
        :data:`MAX_DELIVERY_ATTEMPTS` and be tried again no sooner than a failed
        one.
        """

        if not self._start_attempt():
            return self._record()
        try:
            with transaction.atomic(), system_context(reason="webforms.response.deliver"):
                row = self._stored().undelivered().lock_if_supported().first()
                if row is None:
                    return self._record()
                made = row._record()
                if made is None:
                    made = row.form.target().deliver(row)
                    if made is not None:
                        if not _model("ResponseRecord").declares_target(type(made)):
                            # Nothing would erase the answers when an undeclared record goes.
                            raise ImproperlyConfigured(
                                f"{type(made)._meta.label} is not a record a response can become: the addon of "
                                "the form's target declares no relation for it on webforms/response_record.",
                            )
                        content_type, object_id = generic_pointer_target(made)
                        _model("ResponseRecord").objects.create(
                            response_id=row.pk, content_type=content_type, object_id=object_id,
                        )
                row._stored().undelivered().owner_update(
                    delivery=Delivery.DELIVERED, delivered_at=timezone.now(), delivery_error="", retry_at=None,
                )
                return made
        except DeliveryRefused as refusal:
            reason, again = f"refused: {refusal.code}", False
        except Exception as error:
            reason, again = type(error).__name__, True
            logger.warning(
                "Delivering response %s failed with %s, raised at:\n%s",
                self.pk, reason, "".join(traceback.format_tb(error.__traceback__)),
            )
        # Raised outside the handler, so the failure that may quote an answer is not carried as its context.
        self._record_failure(reason, again=again)
        raise DeliveryFailed(reason)

    def _start_attempt(self) -> bool:
        """Count one attempt on an undelivered response and set when it is next tried; whether there was one."""

        with transaction.atomic(), system_context(reason="webforms.response.deliver"):
            row = self._stored().undelivered().lock_if_supported().first()
            if row is None:
                return False
            attempts = row.delivery_attempts + 1
            row._stored().undelivered().owner_update(
                delivery_attempts=attempts,
                retry_at=timezone.now() + max(retry_delay(attempts), REDELIVERY_LEASE),
            )
        return True

    def _record_failure(self, reason: str, *, again: bool) -> None:
        """Mark an undelivered response failed, and set when it is tried again, if it is."""

        with transaction.atomic(), system_context(reason="webforms.response.deliver"):
            row = self._stored().undelivered().lock_if_supported().first()
            if row is None:
                return
            retried = again and row.delivery_attempts < MAX_DELIVERY_ATTEMPTS
            row._stored().undelivered().owner_update(
                delivery=Delivery.FAILED, delivery_error=reason,
                retry_at=timezone.now() + retry_delay(row.delivery_attempts) if retried else None,
            )

    def dismiss(self) -> bool:
        """Give up delivering a response that could not be; return whether this call did."""

        self.require_access("write")
        return bool(self._stored().undelivered().owner_update(delivery=Delivery.DISMISSED, retry_at=None))

    def erase(self) -> bool:
        """Blank the answers its version marks as personal; return whether this call did.

        The row stays, with its other answers, its delivery and its link, so
        counts and what was made from it still stand. The record made from it is
        its owner's to erase.
        """

        self.require_access("write")
        return self._erase()

    def _erase(self) -> bool:
        """Blank the personal answers once; the write behind :meth:`erase` and the manager's system erasure."""

        personal = set(self.version.personal_questions or ())
        kept = {
            name: value for name, value in self.answers.items() if name.removesuffix(OTHER_SUFFIX) not in personal
        }
        return bool(self._stored().filter(erased_at__isnull=True).owner_update(
            answers=kept, contact="", erased_at=timezone.now(),
        ))


class ResponseRecord(RecordRefMixin, AngeeDataModel, concrete=True):
    """The record one response became.

    It is what a retried delivery finds instead of making a second record, and
    the path from that record back to the answers it came from. It is evidence
    of what the response became when it was delivered, so a merge of that record
    does not rewrite it (``KEEP``): the survivor's responses are read through its
    redirect group (:meth:`ResponseQuerySet.for_record`), a response's record is
    read as its survivor, and reverting the merge has nothing to restore. It
    goes when the record or the response does and never blocks either. When the
    record is deleted, the response it came from loses its personal answers
    first (:meth:`teardown_for_target`).

    Which record types a response can become is declared, not open: the addon
    that contributes a target names the type its target makes as a relation on
    ``webforms/response_record`` in its ``permissions.extends.zed``
    (``relation enquiry: acme/enquiry // rebac:field=record``). So only those
    types carry this edge in their metadata, and only their deletions and
    merges read it.
    """

    merge_policy = MergePolicy.KEEP
    sqid_prefix = "wbl_"

    response = models.OneToOneField("webforms.Response", on_delete=models.CASCADE, related_name="record_link")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for a response's record link."""

        abstract = True
        rebac_resource_type = "webforms/response_record"
        indexes = (models.Index(fields=("content_type", "object_id"), name="%(app_label)s_record_target"),)

    @classmethod
    def teardown_for_target(cls, target: models.Model) -> None:
        """Erase the personal answers of the responses a deleted record was made from, then drop their links."""

        links = cls._base_manager.filter(cls.record_filter(target))
        if not links.exists():
            return
        _model("Response").objects.erase_for_record(target)
        with system_context(reason="webforms.response_record.target_delete"):
            links.delete()


WebformsRole = role_anchor("webforms/role", name="WebformsRole")
"""Role anchor for ``webforms/role``: the ``webforms_admin`` role manages forms and reads their responses."""
