"""Open decisions mailed to the people they can be put to, and the record of whom each mail reached.

A question is mailed to the audience its kind has (:class:`MailAudience`, chosen by
``ANGEE_DECISIONS_MESSAGING_AUDIENCES``: its deciders by default) when it is asked and
when its reminder is due; a daily digest mails each active person the open questions
whose audience includes them, the same rule read from their side. Each mail is a shipped message
template sent as named system work through ``MessageTemplate.objects.send_as_system``,
in the recipient's language, from values read in that recipient's own scope, and it
links the question's page in the app, where the person decides. "Mailed" means
recorded on messaging's system channel and queued; delivery follows the commit.
``DecisionNotice`` and ``DigestNotice`` record each person mailed in the transaction
of that one send, so a retried task mails nobody twice.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, prefetch_related_objects
from django.utils import timezone, translation
from django.utils.text import Truncator
from django.views.decorators.debug import sensitive_variables
from rebac import system_context

from angee.base.fields.state import StateField
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.audit import retained_set_null
from angee.base.models.base import AngeeModel
from angee.base.origins import configured_public_origin
from angee.base.querysets import AngeeQuerySet
from angee.decisions.contracts import DecisionProposal
from angee.messaging.backends import OutboundEmailChannelMissing, SystemChannelMissing
from angee.messaging_templates.engine import (
    INVALID_VALUES,
    MESSAGE_TEMPLATE_REFUSED,
    NO_BRAND_KIT,
    NOT_A_TEMPLATE,
    NOT_EFFECTIVE,
    RENDER_BUDGET_EXCEEDED,
    RENDER_FAILED,
    RENDER_ITERATION_LIMIT,
    SUBJECT_TOO_LONG,
    VALUE_ITEMS_LIMIT,
    VALUE_NOT_LINKABLE,
    VALUE_NOT_PRINTED,
    VALUE_STRING_LIMIT,
    VALUES_JSON_LIMIT,
    VALUES_TOO_LARGE,
)

logger = logging.getLogger(__name__)

QUESTION_RECORD_LIMIT = 5
"""The most concerned records one question names by label; it counts the rest.

A question may concern any number of records, while a mail's values and the work of
its render are bounded (``angee.messaging_templates.walker.VALUE_ITEMS_LIMIT``,
``angee.messaging_templates.engine.RenderBudget``).
"""

QUESTION_FACT_LIMIT = 15
"""The most facts one question shows (``Decision.facts_for``); it counts the rest.

A bill's checklist runs to about a dozen facts, and every fact is a list item and a
loop render of the mail (the bounds :data:`QUESTION_RECORD_LIMIT` cites).
"""

DIGEST_QUESTION_LIMIT = 10
"""The most questions one daily digest lists; it counts the rest (the bounds :data:`QUESTION_RECORD_LIMIT` cites)."""


def _text_limit(questions: int, *, texts: int, share: int) -> int:
    """The longest one text runs when ``texts`` texts of each of ``questions`` questions take ``1/share`` of a mail.

    A mail's values are at most ``VALUES_JSON_LIMIT`` characters of JSON, each string at
    most ``VALUE_STRING_LIMIT``. The facts of the questions a mail lists take at most
    half of it (a label and a text each, at most :data:`QUESTION_FACT_LIMIT` per
    question), their reasons a quarter, and the rest of the mail the last quarter. An
    ellipsis ends what is cut; the app shows it whole. The bound counts characters as
    Django's ``Truncator`` does, while JSON writes a quote, backslash or newline in two
    (the debt the README names), so a mail whose texts are mostly those can still pass
    ``VALUES_JSON_LIMIT``, and that person's mail then fails as a defect.
    """

    return min(VALUE_STRING_LIMIT, VALUES_JSON_LIMIT // (share * questions * texts))


FACT_TEXT_LIMIT = _text_limit(1, texts=2 * QUESTION_FACT_LIMIT, share=2)
"""The cut of a fact's label and text in a mail about one question: the asked and reminder mails."""

DIGEST_FACT_TEXT_LIMIT = _text_limit(DIGEST_QUESTION_LIMIT, texts=2 * QUESTION_FACT_LIMIT, share=2)
"""The cut of a fact's label and text in a daily digest, which lists up to :data:`DIGEST_QUESTION_LIMIT` questions."""

REASON_TEXT_LIMIT = _text_limit(1, texts=1, share=4)
"""The cut of the question's reason in the asked and reminder mails."""

DIGEST_REASON_TEXT_LIMIT = _text_limit(DIGEST_QUESTION_LIMIT, texts=1, share=4)
"""The cut of each question's reason in a daily digest."""


def _choice_limit(questions: int) -> int:
    """The most choices each of ``questions`` questions names in a mail listing them; it counts the rest.

    Each choice named is one item of the mail's values (``VALUE_ITEMS_LIMIT``) and one
    loop body of its render (``RENDER_ITERATION_LIMIT``); the choices of the
    questions a mail lists take at most a quarter of the smaller, and the facts,
    records and includes the rest. A question's alternatives are not bounded, so
    without this one question of a few hundred choices, or a digest of a few dozen
    each, passes the render's budget.
    """

    return min(VALUE_ITEMS_LIMIT, RENDER_ITERATION_LIMIT) // (4 * questions)


QUESTION_CHOICE_LIMIT = _choice_limit(1)
"""The most choices an asked or reminder mail names for its one question."""

DIGEST_CHOICE_LIMIT = _choice_limit(DIGEST_QUESTION_LIMIT)
"""The most choices a daily digest names for each question it lists."""

DIGEST_WINDOW = timedelta(days=1)
"""A question an asked or reminder mail reached a person about this recently is listed last in their digest.

When every question waiting on a person was mailed to them this recently, they get no digest.
"""

DIGEST_TEMPLATE_KEY = "decisions_messaging.decision_digest"
"""The shipped message template the daily digest is written from."""

QUESTION_LINK = ("question.url",)
"""What an asked or reminder mail must show (``send_as_system``'s ``must_print``): its question's link."""

DIGEST_LINKS = ("questions.*.url",)
"""What a digest must be able to print for every question it lists: each one's link."""

RUN_REFUSALS: dict[str, str] = {
    NOT_A_TEMPLATE: "the row read for {key} is not a template",
    VALUE_NOT_PRINTED: "the deployment's own row of {key} does not show every question's link",
    VALUE_NOT_LINKABLE: "the deployment's own row of {key} prints a value that is not a link as a link",
    MESSAGE_TEMPLATE_REFUSED: "the deployment's own row of {key}, or a row it includes, no longer renders",
    RENDER_FAILED: "{key} failed to render; the worker's log holds the template library's traceback",
    NO_BRAND_KIT: "no brand kit is the default; set a default brand kit",
}
"""A send's refusals that are the deployment's or a template row's, the same for every recipient: what to fix.

Each fails the run once (:class:`DecisionMailUnsendable`), never a person's skip.
"""

QUESTION_REFUSALS = frozenset({INVALID_VALUES, VALUES_TOO_LARGE, RENDER_BUDGET_EXCEEDED, SUBJECT_TOO_LONG})
"""A send's refusals that may be the values one question carries for one reader, or a row's.

The owner raises each for a row too (a path the row requires, a date in its own
format, its own subject), so the row is judged first by its own sample
(:meth:`_Run.refuse_row_faults`). Otherwise the question is skipped for that one
reader, logged: a question's labels, facts and asker are read in each reader's scope,
and a row may print any of them, so none of these codes is known not to depend on the
reader, and no skip is shared. A run that skipped any question then fails once, at
the end, naming the questions (:func:`_mail_each`). The fact and reason cuts and the
choice cap keep the shipped rows inside the limits.
"""


class DecisionMailUnsendable(ImproperlyConfigured):
    """Decision mail cannot be sent this run: the deployment, or its own row of a template, must change.

    Raised once per run, worded for an administrator, and never a person's skip: by
    :meth:`_Run.start` before anyone is mailed, or once a run tried everyone, when a
    send refused what only the deployment can fix (:data:`RUN_REFUSALS`), or skipped
    questions. It carries no link and no address; ``codes`` holds the refusals' codes
    and ``skipped`` each skipped question's public id with its codes, so a reader
    classifies it by those, never by its words.
    """

    def __init__(
        self, message: str, *, codes: Iterable[str] = (), skipped: dict[str, set[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.codes = frozenset(codes)
        self.skipped = {public_id: frozenset(found) for public_id, found in (skipped or {}).items()}


class QuestionUnsendable(Exception):
    """One reader's mail of one question refuses that question's values (:data:`QUESTION_REFUSALS`).

    Carries the codes alone, no link and no address. Whether the row or the question
    is at fault is decided where it is caught (:meth:`_Run.refuse_row_faults`).
    """

    def __init__(self, codes: Iterable[str]) -> None:
        self.codes = frozenset(codes)
        super().__init__(", ".join(sorted(self.codes)))


class TemplateRowChanged(ValidationError):
    """The row read for a template stopped being the one that renders while the run sent (``NOT_EFFECTIVE``).

    A deployment saved an override mid-run; the task's retry reads the rows again
    (:data:`RETRIED_REFUSALS`).
    """


type Mailed = Literal["mailed", "already", "skipped", "refused"]
"""What mailing one person came to: mailed now, mailed before, nothing to mail, or their address refused."""


def mail_reason(template_key: str) -> str:
    """The named system work the mail of one shipped template runs as: ``<template key>.send``."""

    return f"{template_key}.send"


class DecisionNoticeOccasion(models.TextChoices):
    """Why one question was mailed to a person; each occasion has its shipped template."""

    ASKED = "asked", "Asked"
    REMINDER = "reminder", "Reminder"

    @property
    def template_key(self) -> str:
        """The key of the shipped message template this occasion's mail is written from."""

        return f"decisions_messaging.decision_{self.value}"


class MailAudience(models.TextChoices):
    """Who a question's mail goes to, chosen per decision kind by ``ANGEE_DECISIONS_MESSAGING_AUDIENCES``.

    Each audience is one decisions rule read from both sides: the people of each
    question (:meth:`people`) and the questions put to one person (:meth:`put_to`).
    ``deciders`` is ``DecisionQuerySet.deciders`` and ``put_to``: who holds both
    ``act`` and ``read``, with no "assigned to me" clause, the digest included.
    ``administrators`` is ``DecisionQuerySet.administrators`` and
    ``for_administrator``: the platform administrators among the deciders.
    """

    DECIDERS = "deciders", "Deciders"
    ADMINISTRATORS = "administrators", "Administrators"

    @classmethod
    def _table(cls) -> tuple[dict[str, MailAudience], MailAudience]:
        """The setting read once: each named kind's audience, and every other kind's (``"*"``, else ``deciders``).

        The one lookup :meth:`of` and :meth:`kinds` both answer from.
        """

        audiences = settings.ANGEE_DECISIONS_MESSAGING_AUDIENCES
        named = {kind: cls(audience) for kind, audience in audiences.items() if kind != "*"}
        return named, cls(audiences.get("*", cls.DECIDERS))

    @classmethod
    def of(cls, kind: str) -> MailAudience:
        """The audience the setting gives ``kind``: its own entry, else every other kind's."""

        named, others = cls._table()
        return named.get(kind, others)

    @classmethod
    def used(cls) -> list[MailAudience]:
        """The audiences the setting gives some kind, in declaration order; under the default, ``deciders`` alone."""

        named, others = cls._table()
        return [audience for audience in cls if audience is others or audience in named.values()]

    def kinds(self) -> models.Q:
        """The decision kinds :meth:`of` gives this audience, as a filter on questions."""

        named, others = type(self)._table()
        if others is self:
            return ~models.Q(kind__in=sorted(kind for kind, audience in named.items() if audience is not self))
        return models.Q(kind__in=sorted(kind for kind, audience in named.items() if audience is self))

    def people(self, questions: Any) -> dict[Any, list[Any]]:
        """Each of ``questions``' people in this audience, keyed by primary key, from the question's side."""

        return questions.deciders() if self is MailAudience.DECIDERS else questions.administrators()

    def put_to(self, questions: Any, person: Any) -> Any:
        """The open ``questions`` whose people in this audience include ``person``, from the person's side."""

        return questions.put_to(person) if self is MailAudience.DECIDERS else questions.for_administrator(person)


def mail_hints(questions: Any, reader: Any) -> tuple[models.Prefetch, ...]:
    """The per-reader hints a mail's list of questions takes: ``Decision.reader_prefetches`` and the asker's name."""

    return (*questions.reader_prefetches(reader), questions.requester_names_prefetch(reader))


def mail_values(user: Any, content: dict[str, Any]) -> dict[str, Any]:
    """The values one mail to ``user`` is sent with: their first name, then ``content``."""

    return {"person": {"first_name": user.first_name}, **content}


@sensitive_variables()
def digest_values(user: Any, listed: Sequence[Any], shown: Sequence[Any], shared: dict[Any, Any]) -> dict[str, Any]:
    """The values of ``user``'s digest: how many questions wait, how many more than ``shown`` it lists, and each."""

    return {
        "count": len(listed),
        "more": len(listed) - len(shown),
        "questions": [
            question_values(
                row, user, shared[row.pk], text_limit=DIGEST_FACT_TEXT_LIMIT, reason_limit=DIGEST_REASON_TEXT_LIMIT,
            )
            for row in shown
        ],
    }


@sensitive_variables()
def unsendable_questions(
    template: Any, user: Any, shown: Sequence[Any], shared: dict[Any, Any],
) -> dict[Any, frozenset[str]]:
    """Each question of ``shown`` whose values ``user``'s digest row refuses on its own, with its own codes.

    Each is rendered alone in ``template`` (``MessageTemplate.render``, nothing sent), in
    the row's language and the digest's named work; asked only once a digest's send
    refused a question's values, to find which.
    """

    faulty: dict[Any, frozenset[str]] = {}
    with system_context(reason=mail_reason(DIGEST_TEMPLATE_KEY)), translation.override(template.language):
        for row in shown:
            try:
                template.render(mail_values(user, digest_values(user, [row], [row], shared)), must_print=DIGEST_LINKS)
            except ValidationError as refusal:
                if codes := refusal_codes(refusal) & QUESTION_REFUSALS:
                    faulty[row.pk] = codes
    return faulty


@sensitive_variables()
def shared_values(decision: Any, *, origin: str, choice_limit: int) -> dict[str, Any]:
    """What every person mailed about ``decision`` reads alike: its kind, due time, choices and link.

    Each recipient may read the question, so these come from the question itself. The
    first ``choice_limit`` choices are named (:data:`QUESTION_CHOICE_LIMIT` or
    :data:`DIGEST_CHOICE_LIMIT`), with a count of the rest.
    """

    choices = [alternative.label for alternative in DecisionProposal.model_validate(decision.proposal).alternatives]
    return {
        "title": decision.kind_label,
        "due_at": decision.due_at,
        "url": f"{origin}{decision.get_absolute_url()}",
        "choices": choices[:choice_limit],
        "more_choices": max(len(choices) - choice_limit, 0),
    }


def fact_values(fact: Any, *, text_limit: int) -> dict[str, Any]:
    """One fact of ``Decision.facts_for`` as ``parts/decisions_messaging.question`` writes it.

    Its label and its text (``DecisionFact.as_text``, the card's form), each cut to
    ``text_limit`` (:data:`FACT_TEXT_LIMIT` or :data:`DIGEST_FACT_TEXT_LIMIT`), and its
    authority code, which the template marks when the value is not the source's.
    """

    return {
        "label": Truncator(fact.label).chars(text_limit),
        "value": Truncator(fact.as_text()).chars(text_limit),
        "authority": str(fact.authority),
    }


@sensitive_variables()
def question_values(
    row: Any, reader: Any, shared: dict[str, Any], *, text_limit: int, reason_limit: int,
) -> dict[str, Any]:
    """The ``question`` argument of ``parts/decisions_messaging.question`` for ``reader``: ``shared`` and their reads.

    ``row`` is the question as ``reader`` reads it. The concerned records are named by
    ``Decision.concern_labels(reader)``, the first :data:`QUESTION_RECORD_LIMIT` of
    them with a count of the rest, its facts by ``Decision.facts_for(reader)``, at most
    :data:`QUESTION_FACT_LIMIT`, each cut to ``text_limit`` (:func:`fact_values`), its
    reason by ``Decision.context_for(reader)``, cut to ``reason_limit``
    (:data:`REASON_TEXT_LIMIT` or :data:`DIGEST_REASON_TEXT_LIMIT`), and who asked by
    ``Decision.requester_name(reader)``,
    so a person never reads a label, a fact or a name in a mail they could not read
    in the app. Nothing here is secret: the values are kept with the message, which
    administrators read.
    """

    labels = row.concern_labels(reader)
    facts = row.facts_for(reader)
    return {
        **shared,
        "reason": Truncator(row.context_for(reader).reason).chars(reason_limit),
        "records": labels[:QUESTION_RECORD_LIMIT],
        "more_records": max(len(labels) - QUESTION_RECORD_LIMIT, 0),
        "facts": [fact_values(fact, text_limit=text_limit) for fact in facts[:QUESTION_FACT_LIMIT]],
        "more_facts": max(len(facts) - QUESTION_FACT_LIMIT, 0),
        "asked_by": row.requester_name(reader),
    }


@dataclass(frozen=True)
class _Run:
    """What one run reads once, before anyone is mailed: the link origin and each person's template rows.

    Frozen in what it read; ``samples`` is a cache it fills as the run goes, each row's
    verdict on its own sample (:meth:`refuse_row_faults`).
    """

    origin: str
    rows: dict[tuple[Any, str], Any]
    samples: dict[Any, frozenset[str]] = field(default_factory=dict)

    @classmethod
    @sensitive_variables()
    def start(cls, people: Iterable[Any], keys: Sequence[str]) -> Self:
        """Refuse a run no mail of could be sent, then read the row of each of ``keys`` each of ``people`` reads.

        In order, each once for the whole run and before any render: the public origin
        (``configured_public_origin``, which raises ``PublicOriginUnavailable``), and
        system mail (``Channel.objects.system_mail_configured``, else
        :class:`DecisionMailUnsendable`). A link the send cannot carry is refused at the
        send (``value_not_linkable``, one of :data:`RUN_REFUSALS`). Each row is the
        library's own answer for the person (``effective(person=)``), read once per
        list of languages the library tries for them (``language_candidates``), all in
        one elevated read here, so its audit stays whatever a person's own transaction
        does later. A key with no row refuses with ``DoesNotExist`` before anyone is
        mailed.
        """

        origin = configured_public_origin()
        if not apps.get_model("messaging", "Channel").objects.system_mail_configured():
            raise DecisionMailUnsendable(
                "Decision mail cannot be sent: this deployment's system mail is not configured "
                "(messaging's README, \"System mail\").",
            )
        people = list(people)
        templates = apps.get_model("messaging_templates", "MessageTemplate").objects
        rows: dict[tuple[Any, str], Any] = {}
        with system_context(reason="decisions_messaging.templates"):
            if apps.get_model("brandkit", "BrandKit").objects.default() is None:
                raise DecisionMailUnsendable(
                    f"Decision mail cannot be sent ({NO_BRAND_KIT}): {RUN_REFUSALS[NO_BRAND_KIT]}.",
                    codes=[NO_BRAND_KIT],
                )
            # Blocked debt: the user's Person for ``effective(person=)`` is a parties read (one
            # that creates nothing, beside ``Person.objects.for_user``); this goes when it lands.
            person_model = apps.get_model("parties", "Person")
            persons = {
                person.user_id: person
                for person in person_model.system_queryset().filter(user__in=[user.pk for user in people])
            }
            read: dict[tuple[tuple[str, ...], str], Any] = {}
            for user in people:
                person = persons.get(user.pk)
                candidates = tuple(templates.language_candidates(person=person))
                for key in keys:
                    if (candidates, key) not in read:
                        read[candidates, key] = templates.effective("template", key, person=person)
                    rows[user.pk, key] = read[candidates, key]
        return cls(origin=origin, rows=rows)

    def template(self, user: Any, key: str) -> Any:
        """The row of ``key`` ``user`` reads, as :meth:`start` read it."""

        return self.rows[user.pk, key]

    def refuse_row_faults(self, user: Any, key: str, must_print: Iterable[str]) -> None:
        """Raise :class:`DecisionMailUnsendable` when ``user``'s row of ``key`` refuses its own ``sample``.

        Asked once a send refused a question's values (:class:`QuestionUnsendable`):
        a row that cannot render its own sample (``MessageTemplate.render`` with the
        mail's ``must_print``) is the row's fault for every question, not one
        question's; a row whose sample renders is sound, and the question is at fault.
        So a deployment's own row must keep a sample that renders: a stale one turns
        each question's fault into the row's. Each row is judged once per run.
        """

        row = self.template(user, key)
        if row.pk not in self.samples:
            with system_context(reason=mail_reason(key)), translation.override(row.language):
                try:
                    row.render(row.sample, must_print=must_print)
                    self.samples[row.pk] = frozenset()
                except ValidationError as refusal:
                    self.samples[row.pk] = refusal_codes(refusal)
        if codes := self.samples[row.pk]:
            raise DecisionMailUnsendable(
                f"Decision mail cannot be sent ({', '.join(sorted(codes))}): the deployment's own row of {key} "
                "refuses its own sample.",
                codes=codes,
            )


PERMANENT_ERRORS: tuple[type[Exception], ...] = (ImproperlyConfigured, ObjectDoesNotExist, ValidationError, ValueError)
"""Failures no retry cures: no public origin, a missing template row, a refusal, a bad argument.

A refusal is permanent when it comes from the values, the row or the settings: a
render refusal, an address the transport cannot carry (``to``), system mail that is
not configured. :data:`RETRIED_REFUSALS` are the exception. Every other failure, a
database or broker hiccup, is retried.
"""

RETRIED_REFUSALS: tuple[type[ValidationError], ...] = (
    SystemChannelMissing, OutboundEmailChannelMissing, TemplateRowChanged,
)
"""The refusals a retry may cure: messaging's system channel missing or not connected, and a row changed mid-run.

The first two are a deployment's state, which an operator can fix while the task
waits; the last is cured by the retry's own read of the rows.
"""


def retry_may_cure(error: BaseException) -> bool:
    """Whether a retry may cure ``error``: one of :data:`RETRIED_REFUSALS`, or one outside :data:`PERMANENT_ERRORS`."""

    return isinstance(error, RETRIED_REFUSALS) or not isinstance(error, PERMANENT_ERRORS)


def refusal_codes(error: ValidationError) -> frozenset[str]:
    """The codes of every item of ``error``, keyed or not; an item without a code counts as ``invalid``."""

    # Debt: ``_codes`` in angee/calendar/models.py reads the same; base has no reader of a
    # ``ValidationError``'s codes for both to compose.
    items = [item for items in error.error_dict.values() for item in items] if hasattr(error, "error_dict") else []
    return frozenset(item.code or "invalid" for item in items or error.error_list)


def _refused(user: Any, key: str, refusal: ValidationError) -> Mailed:
    logger.info("%s mail to user %s: address refused (%s).", key, user.pk, ", ".join(sorted(refusal_codes(refusal))))
    return "refused"


@sensitive_variables()
def _mail_one(
    ledger: MailNoticeManager[Any], user: Any, *, key: str, run: _Run, must_print: Iterable[str],
    values: Callable[[], dict[str, Any] | None], replacing: Any = None, **identity: Any,
) -> Mailed:
    """Mail ``user`` the template ``key`` once for ``identity`` in ``ledger``, in a transaction of its own.

    The one send path both ledgers share. An address system mail would refuse
    (``Message.objects.clean_sendable_recipient``, asked before the reads and the
    render) is skipped as ``refused``, as is a refusal of the send keyed ``to``,
    which wrote nothing. ``values`` is read inside the transaction; ``None`` means
    nothing is left to mail (``skipped``); it is read in the language of ``user``'s
    template row, so the text it gives reads in that language. The send names what
    the mail must show (``must_print``); a refusal of that, or of a link, is the
    template row's or the deployment's (:data:`RUN_REFUSALS`) and raises
    :class:`DecisionMailUnsendable`, never a person's skip, with no link or address in it;
    a refusal of the question's own values (:data:`QUESTION_REFUSALS`) raises
    :class:`QuestionUnsendable` for the run verb to skip that question. No refusal's
    own message leaves this frame, since a render's (``render_failed``) quotes the
    exception, which may quote a value such as the link: every one is raised again
    as its codes and this addon's words, ``from None``, except messaging's channel
    refusals (:data:`RETRIED_REFUSALS`), whose fixed sentences the task retries. The notice is written
    after the send (:meth:`MailNoticeManager.record`), replacing the failed message
    ``replacing`` when the person's notice holds one, so a send that raises writes
    none and the next run mails the person; a notice another run wrote or replaced
    first is ``already``, and this run's message rolls back with this notice. The
    transaction is durable, never a savepoint: inside an outer transaction the
    send's delivery would wait for it, and one person's failure would roll the
    others back.
    """

    messages = apps.get_model("messaging", "Message").objects
    try:
        _name, address = messages.clean_sendable_recipient(user.email or "")
    except ValidationError as refusal:
        return _refused(user, key, refusal)
    templates = apps.get_model("messaging_templates", "MessageTemplate").objects
    try:
        with transaction.atomic(durable=True):
            row = run.template(user, key)
            with translation.override(row.language):
                content = values()
            if content is None:
                return "skipped"
            message = templates.send_as_system(
                row, reason=mail_reason(key), to=address, must_print=must_print, values=mail_values(user, content),
            )
            if not ledger.record(user=user, message=message, replacing=replacing, **identity):
                transaction.set_rollback(True)
                return "already"
    except ValidationError as refusal:
        codes = refusal_codes(refusal)
        if NOT_EFFECTIVE in codes:
            raise TemplateRowChanged(
                f"The row of {key} stopped being the one that renders while the run sent; the task retries.",
                code=NOT_EFFECTIVE,
            ) from None
        if broken := sorted(codes & RUN_REFUSALS.keys()):
            raise DecisionMailUnsendable(
                f"Decision mail cannot be sent ({', '.join(broken)}): "
                f"{'; '.join(RUN_REFUSALS[code].format(key=key) for code in broken)}.",
                codes=broken,
            ) from None
        if faults := codes & QUESTION_REFUSALS:
            raise QuestionUnsendable(faults) from None
        if hasattr(refusal, "error_dict") and set(refusal.error_dict) == {"to"}:
            return _refused(user, key, refusal)
        if isinstance(refusal, RETRIED_REFUSALS):
            raise  # Messaging's own channel refusals: fixed sentences, which the task retries by their class.
        raise ValidationError([
            ValidationError(f"Decision mail's send was refused ({code}).", code=code) for code in sorted(codes)
        ]) from None
    return "mailed"


def _failure(error: Exception) -> str:
    """A failure as its log names it: a refusal's codes, else the exception's class."""

    return ", ".join(sorted(refusal_codes(error))) if isinstance(error, ValidationError) else type(error).__name__


def _mail_each(
    people: Iterable[Any], mail: Callable[[Any], Mailed], *, what: str, skipped: dict[str, set[str]] | None = None,
) -> Counter[str]:
    """Mail each of ``people`` through ``mail``, one at a time, so one person's failure never stops the others.

    A failure is logged by the person's primary key and its refusal code, and counted.
    Once everyone was tried, the run raises: the first failure a retry may cure
    (``retry_may_cure``), else the run's own defect, else the questions ``mail`` had
    to skip (``skipped``, public id to codes), else the first failure, so the task
    fails visibly. Celery's ``SoftTimeLimitExceeded`` is no person's failure: it is
    raised at once, so the task stops inside its time limit. Nor is
    :class:`DecisionMailUnsendable`: it is the deployment's row's, which is one
    language's, so the others are still mailed, and it is logged and raised once,
    never per person. A failure that is no refusal is raised again as its own class
    with this addon's words (:func:`_reworded`).
    """

    outcomes: Counter[str] = Counter()
    failures: list[Exception] = []
    defect: DecisionMailUnsendable | None = None
    for user in people:
        try:
            outcomes[mail(user)] += 1
        except SoftTimeLimitExceeded:
            raise
        except DecisionMailUnsendable as error:
            outcomes["failed"] += 1
            defect = defect or error
        except Exception as error:
            outcomes["failed"] += 1
            failures.append(error)
            logger.warning("%s to user %s failed (%s).", what, user.pk, _failure(error))
    logger.info("%s: %s.", what, ", ".join(f"{count} {outcome}" for outcome, count in sorted(outcomes.items())))
    if defect is not None:
        logger.error("%s: %s", what, defect)
    if retryable := next((error for error in failures if retry_may_cure(error)), None):
        raise _reworded(retryable, what) from None
    if defect is not None:
        raise defect
    if skipped:
        named = "; ".join(f"{public_id} ({', '.join(sorted(codes))})" for public_id, codes in sorted(skipped.items()))
        raise DecisionMailUnsendable(f"{what} skipped questions whose values cannot be sent: {named}.", skipped=skipped)
    if failures:
        raise _reworded(failures[0], what) from None
    return outcomes


def _reworded(error: Exception, what: str) -> Exception:
    """``error`` as the run raises it: a refusal as the send path left it, any other failure as its class, reworded.

    The send path already raises each refusal as its codes and this addon's words
    (:func:`_mail_one`). Any other failure is a database's, a broker's or a bug's,
    and its own text may quote what was written: a unique violation's detail names
    the key's values, and a recipient's handle is keyed by its address. So it is
    raised again as a new instance of its own class, which the task's retry policy
    still recognises, saying only where it happened. A class that cannot be made from
    one message (``UnicodeDecodeError``) becomes the nearest of
    :data:`PERMANENT_ERRORS` it belongs to, so a permanent failure is not retried,
    else a ``RuntimeError``. The new error keeps the old one's traceback: its frames
    name code lines, not values, so a bug stays debuggable, as the GraphQL schema's
    error log keeps an error's class and frames but not its message
    (``angee/graphql/schema.py``). Django's
    ``@sensitive_variables()`` hides frame locals from Django's own error reports
    only; a plain traceback never prints locals.
    """

    if isinstance(error, ValidationError):
        return error
    words = f"{what} failed ({type(error).__name__}); the worker's log names the person."
    try:
        reworded = type(error)(words)
    except Exception:
        reworded = next((kind for kind in PERMANENT_ERRORS if isinstance(error, kind)), RuntimeError)(words)
    return reworded.with_traceback(error.__traceback__)


class MailNoticeManager[NoticeT: MailNotice](AngeeManager[NoticeT]):
    """The send-once write both ledgers share."""

    def record(self, *, user: Any, message: Any, replacing: Any = None, **identity: Any) -> bool:
        """Write the notice that ``message`` mailed ``user`` for ``identity``; ``False`` when another run wrote it.

        Inside the caller's send transaction, in a savepoint of its own. The ledger's
        unique key is the arbiter; a conflict is told from any other integrity failure
        by reading the notice again, and anything else raises. With ``replacing``, the
        failed message the person's one notice holds, the notice is pointed at
        ``message`` instead, only while it still holds ``replacing``: a compare on the
        notice's own column, so of two runs re-sending at once one counts and the other
        is ``False``, and the notice is never written twice.
        """

        if replacing is not None:
            return bool(self.model._base_manager.filter(user=user, message=replacing, **identity).owner_update(
                message=message, updated_at=timezone.now(),
            ))
        notice: Any = self.model(user=user, message=message, **identity)
        try:
            with transaction.atomic():
                notice._owner_insert()
        except IntegrityError:
            if not self.model._base_manager.filter(user=user, **identity).exists():
                raise
            return False
        return True


class MailNotice(AppendOnlyModel, AngeeModel):
    """One person a mail reached, with the message it recorded: the send-once key of one kind of mail.

    Written in the transaction of that one send (``MailNoticeManager.record``); a send
    that raises writes none. The message is messaging's to keep or purge: purging it
    empties ``message`` and keeps the notice, so nobody is mailed again. A notice
    whose message messaging recorded as failed holds no mail that reached anyone:
    the occasion's next run mails the person again and points this one notice at the
    new message (its ``updated_at``). An uncertain one is never sent again. Notices
    are otherwise kept as written. Internal to this addon, like a sequence counter: no
    public id, no GraphQL surface and no REBAC resource type.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    message = models.ForeignKey(
        "messaging.Message", null=True, blank=True, on_delete=retained_set_null, related_name="+",
    )

    class Meta:
        """Django model options shared by the decision mail ledgers."""

        abstract = True


class MailNoticeQuerySet[NoticeT: MailNotice](AppendOnlyQuerySet[NoticeT], AngeeQuerySet[NoticeT]):
    """What each notice's mail came to, as messaging recorded the delivery of its message.

    Debt: messaging has no boolean "this outbound may have reached its recipient"
    predicate to compose, so this reads its named set, ``ACTIVE_OUTBOUND_STATUSES``,
    as ``angee.posts`` does. For a locally sent mail that set is every delivery state
    but ``failed`` (``synced`` and ``edited`` are ingested states); messaging records
    ``failed`` only once nothing was accepted.
    """

    def standing(self) -> Self:
        """The notices whose mail counts as sent: an active outbound state, or a message messaging purged.

        Queued and sent count, and so does ``uncertain``: that mail may have been
        delivered, so it is never sent again.
        """

        return self.filter(
            models.Q(message__isnull=True)
            | models.Q(message__status__in=apps.get_model("messaging", "Message").ACTIVE_OUTBOUND_STATUSES),
        )

    def failed(self) -> Self:
        """The notices whose mail messaging recorded as ``failed``: it reached nobody, so its occasion mails again."""

        return self.filter(message__status=apps.get_model("messaging", "Message").MessageStatus.FAILED)


class DecisionNoticeQuerySet[NoticeT: DecisionNotice](MailNoticeQuerySet[NoticeT]):
    """Question notices, each holding the one message its occasion's mail recorded."""

    def mailed_since(self, moment: datetime) -> Self:
        """The notices whose mail was recorded since ``moment``: a notice's ``updated_at``, its send or its re-send."""

        return self.filter(updated_at__gte=moment)


_DecisionNoticeManagerBase = AngeeManager.from_queryset(DecisionNoticeQuerySet)


class DecisionNoticeManager[NoticeT: DecisionNotice](
    MailNoticeManager[NoticeT], _DecisionNoticeManagerBase[NoticeT],
):
    """Mails one question to its deciders, once per occasion each."""

    @sensitive_variables()
    def mail_decision(self, decision_id: Any, *, occasion: str) -> int:
        """Mail an open question to each person in its kind's audience once for ``occasion``; return how many.

        ``occasion`` is ``asked`` or ``reminder``. The audience (:meth:`MailAudience.of`
        the question's kind) is read once, from the question's side
        (:meth:`MailAudience.people`); a person the ledger already holds for the
        question and occasion is not mailed again unless messaging recorded that mail
        as failed (:meth:`MailNoticeQuerySet.standing`), when they are mailed once more
        and their one notice points at the new mail. An uncertain one is never sent
        again. A question that closed since, or that nobody
        can be put to, mails nobody. Each person's values are read in their own scope
        as their mail is sent (:func:`question_values`), so a question since closed or
        no longer readable to them is not mailed (``skipped``); this does not depend
        on the authority ``decision_asked`` was sent under, which a supersede elevates.
        Each person is mailed as :func:`_mail_one` says and isolated as
        :func:`_mail_each` says. A run no mail of could be sent refuses before anyone
        is mailed (:meth:`_Run.start`). Each person's mail is a durable transaction,
        so this runs outside any transaction, as a task does.
        """

        occasion = DecisionNoticeOccasion(occasion)
        key = occasion.template_key
        questions = apps.get_model("decisions", "Decision")
        with system_context(reason=mail_reason(key)):
            rows = questions.system_queryset().open().filter(pk=decision_id)
            decision = rows.first()
            if decision is None:
                return 0
            notices = self.filter(decision=decision, occasion=occasion)
            mailed = set(notices.standing().values_list("user_id", flat=True))
            failed = dict(notices.failed().values_list("user_id", "message_id"))
            audience = MailAudience.of(decision.kind)
            people = [user for user in audience.people(rows).get(decision.pk, []) if user.pk not in mailed]
        if not people:
            return 0
        run = _Run.start(people, [key])
        shared = shared_values(decision, origin=run.origin, choice_limit=QUESTION_CHOICE_LIMIT)

        @sensitive_variables()
        def values_for(user: Any) -> dict[str, Any] | None:
            row = questions.objects.with_actor(user).open().filter(pk=decision.pk).prefetch_related(
                *mail_hints(questions, user),
            ).first()
            return None if row is None else {"question": question_values(
                row, user, shared, text_limit=FACT_TEXT_LIMIT, reason_limit=REASON_TEXT_LIMIT,
            )}

        skipped: dict[str, set[str]] = {}

        def mail(user: Any) -> Mailed:
            try:
                return _mail_one(
                    self, user, key=key, run=run, must_print=QUESTION_LINK, values=lambda: values_for(user),
                    replacing=failed.get(user.pk), decision=decision, occasion=occasion,
                )
            except QuestionUnsendable as fault:
                run.refuse_row_faults(user, key, QUESTION_LINK)
                logger.warning(
                    "Decision %s %s mail to user %s skipped: its values cannot be sent (%s).",
                    decision.public_id, occasion.value, user.pk, fault,
                )
                skipped.setdefault(decision.public_id, set()).update(fault.codes)
                return "skipped"

        outcomes = _mail_each(people, mail, what=f"Decision {decision.pk} {occasion.value} mail", skipped=skipped)
        return outcomes["mailed"]


class DigestNoticeQuerySet[NoticeT: DigestNotice](MailNoticeQuerySet[NoticeT]):
    """Digest notices, each holding the one message its day's digest recorded."""


_DigestNoticeManagerBase = AngeeManager.from_queryset(DigestNoticeQuerySet)


class DigestNoticeManager[NoticeT: DigestNotice](MailNoticeManager[NoticeT], _DigestNoticeManagerBase[NoticeT]):
    """Mails each active person one daily digest of the open questions waiting on them."""

    @sensitive_variables()
    def mail_digests(self, *, now: datetime) -> int:
        """Mail each active person one digest, once per UTC day; return how many were mailed.

        A person's digest lists the open questions whose audience includes them, older
        ones included: for each :class:`MailAudience`, its kinds' questions put to them
        from their side (:meth:`MailAudience.put_to`), so it lists exactly the questions
        whose asked and reminder mails would reach them. The ones an asked or reminder
        mail did not reach them about within
        :data:`DIGEST_WINDOW` first, then due first, at most
        :data:`DIGEST_QUESTION_LIMIT` with a count of the rest. Each person's list is
        their own scoped read, a few statements whatever the number of open questions,
        made inside their own isolated attempt. A person with nothing waiting gets no
        digest, and neither does one every waiting question was mailed to within the
        window; a person whose address messaging refuses, none included, is refused and
        counted as the asked and reminder mails refuse them. Each person is mailed as
        :func:`_mail_one` says and isolated as :func:`_mail_each` says. A second run on
        the same day mails nobody, except a person whose digest messaging recorded as
        failed, who is mailed once more on their one notice; the overlap reads only
        mails that stand (:meth:`MailNoticeQuerySet.standing`), so a failed asked mail
        counts as never sent. Each person's mail is a durable
        transaction, so this runs outside any transaction, as a task does.
        """

        day = now.astimezone(UTC).date()
        questions = apps.get_model("decisions", "Decision")
        notices = apps.get_model("decisions_messaging", "DecisionNotice")
        with system_context(reason=mail_reason(DIGEST_TEMPLATE_KEY)):
            today = self.filter(day=day)
            candidates = get_user_model().system_queryset().active_people().exclude(
                pk__in=today.standing().values("user_id"),
            )
            people = list(candidates.order_by("pk"))
            failed = dict(today.failed().values_list("user_id", "message_id"))
            recent = set(
                notices.objects.mailed_since(now - DIGEST_WINDOW).standing()
                .filter(user__in=candidates.values("pk")).values_list("user_id", "decision_id")
            )
        if not people:
            return 0
        run = _Run.start(people, [DIGEST_TEMPLATE_KEY])
        shared: dict[Any, dict[str, Any]] = {}
        skipped: dict[str, set[str]] = {}

        @sensitive_variables()
        def mail(user: Any) -> Mailed:
            put = models.Q(pk__in=[])
            for audience in MailAudience.used():
                put |= models.Q(pk__in=audience.put_to(questions.objects, user).filter(audience.kinds()).values("pk"))
            waiting = (
                questions.objects.with_actor(user).open().filter(put).order_by(F("due_at").asc(nulls_last=True), "pk")
            )
            listed = sorted(waiting, key=lambda row: (user.pk, row.pk) in recent)
            left, limit = listed, DIGEST_QUESTION_LIMIT
            while True:
                if all((user.pk, row.pk) in recent for row in left):
                    return "skipped"
                shown = left[:limit]
                prefetch_related_objects(shown, *mail_hints(questions, user))
                for row in shown:
                    if row.pk not in shared:
                        shared[row.pk] = shared_values(row, origin=run.origin, choice_limit=DIGEST_CHOICE_LIMIT)
                try:
                    return _mail_one(
                        self, user, key=DIGEST_TEMPLATE_KEY, run=run, must_print=DIGEST_LINKS,
                        replacing=failed.get(user.pk), day=day,
                        values=lambda: digest_values(user, listed, shown, shared),
                    )
                except QuestionUnsendable as fault:
                    run.refuse_row_faults(user, DIGEST_TEMPLATE_KEY, DIGEST_LINKS)
                    if len(shown) == 1:
                        faulty = {shown[0].pk: fault.codes}
                    else:
                        faulty = unsendable_questions(run.template(user, DIGEST_TEMPLATE_KEY), user, shown, shared)
                    culprits = [row for row in shown if row.pk in faulty]
                    if not culprits:
                        limit = len(shown) // 2
                        logger.warning(
                            "Decision digest %s to user %s lists %s questions: more do not fit together (%s).",
                            day, user.pk, limit, fault,
                        )
                        continue
                    for row in culprits:
                        logger.warning(
                            "Decision %s skipped in the digest of %s to user %s: its values cannot be sent (%s).",
                            row.public_id, day, user.pk, ", ".join(sorted(faulty[row.pk])),
                        )
                        skipped.setdefault(row.public_id, set()).update(faulty[row.pk])
                    left = [row for row in left if row not in culprits]

        outcomes = _mail_each(people, mail, what=f"Decision digest {day}", skipped=skipped)
        return outcomes["mailed"]


class DecisionNotice(MailNotice, concrete=True):
    """One person mailed about one question on one occasion: the send-once key of the asked and reminder mails."""

    decision = models.ForeignKey("decisions.Decision", on_delete=models.PROTECT, related_name="+")
    occasion = StateField(choices_enum=DecisionNoticeOccasion)

    objects = DecisionNoticeManager()

    class Meta:
        """Django model options for a decision notice."""

        abstract = True
        constraints = [
            models.UniqueConstraint(fields=("decision", "user", "occasion"), name="%(app_label)s_decision_notice_once"),
        ]

    def __str__(self) -> str:
        """Return who was mailed, about which question, and why."""

        return f"{self.user_id}:{self.decision_id}:{self.occasion}"


class DigestNotice(MailNotice, concrete=True):
    """One person mailed one daily digest on one UTC day: the send-once key of the digest."""

    day = models.DateField()

    objects = DigestNoticeManager()

    class Meta:
        """Django model options for a digest notice."""

        abstract = True
        constraints = [models.UniqueConstraint(fields=("day", "user"), name="%(app_label)s_digest_notice_once")]

    def __str__(self) -> str:
        """Return who was mailed the digest, and for which day."""

        return f"{self.user_id}:{self.day}"
