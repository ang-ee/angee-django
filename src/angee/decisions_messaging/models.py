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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, prefetch_related_objects
from django.utils import translation
from django.utils.text import Truncator
from rebac import system_context

from angee.base.fields.state import StateField
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.audit import retained_set_null
from angee.base.models.base import AngeeModel
from angee.base.origins import public_origin
from angee.base.querysets import AngeeQuerySet
from angee.decisions.contracts import DecisionProposal
from angee.messaging.backends import OutboundEmailChannelMissing, SystemChannelMissing
from angee.messaging_templates.walker import VALUE_STRING_LIMIT, VALUES_JSON_LIMIT

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

DIGEST_WINDOW = timedelta(days=1)
"""A question an asked or reminder mail reached a person about this recently is listed last in their digest.

When every question waiting on a person was mailed to them this recently, they get no digest.
"""

DIGEST_TEMPLATE_KEY = "decisions_messaging.decision_digest"
"""The shipped message template the daily digest is written from."""

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


def decision_link_origin() -> str:
    """Return the deployment's public origin a mailed question links from, or refuse before anything is mailed.

    Blocked debt: ``SignInLink.url_for`` spells the same rule, the configured public
    origin or ``ImproperlyConfigured``. Its owner is ``angee.base.origins``, beside
    ``public_origin``; this copy goes when the accessor lands there.
    """

    origin = public_origin(settings.ANGEE_PUBLIC_ORIGIN, debug=settings.DEBUG)
    if origin is None:
        raise ImproperlyConfigured("Decision mail links the app: set ANGEE_PUBLIC_ORIGIN.")
    return origin


def shared_values(decision: Any, *, origin: str) -> dict[str, Any]:
    """What every person mailed about ``decision`` reads alike: its kind, due time, choices and link.

    Each recipient may read the question, so these come from the question itself.
    """

    proposal = DecisionProposal.model_validate(decision.proposal)
    return {
        "title": decision.kind_label,
        "due_at": decision.due_at,
        "url": f"{origin}{decision.get_absolute_url()}",
        "choices": [alternative.label for alternative in proposal.alternatives],
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
    """What one run reads once, before anyone is mailed: the link origin and each person's template rows."""

    origin: str
    rows: dict[tuple[Any, str], Any]

    @classmethod
    def start(cls, people: Iterable[Any], keys: Sequence[str]) -> Self:
        """Read the origin, refusing without one, and the row of each of ``keys`` each of ``people`` reads.

        Each row is the library's own answer for the person (``effective(person=)``),
        read once per list of languages the library tries for them
        (``language_candidates``), all in one elevated read here, so its audit stays
        whatever a person's own transaction does later. A key with no row refuses
        with ``DoesNotExist`` before anyone is mailed.
        """

        origin = decision_link_origin()
        people = list(people)
        templates = apps.get_model("messaging_templates", "MessageTemplate").objects
        rows: dict[tuple[Any, str], Any] = {}
        with system_context(reason="decisions_messaging.templates"):
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


PERMANENT_ERRORS: tuple[type[Exception], ...] = (ImproperlyConfigured, ObjectDoesNotExist, ValidationError, ValueError)
"""Failures no retry cures: no public origin, a missing template row, a refusal, a bad argument.

A refusal is permanent when it comes from the values, the row or the settings: a
render refusal, an address the transport cannot carry (``to``), system mail that is
not configured. :data:`RETRIED_REFUSALS` are the exception. Every other failure, a
database or broker hiccup, is retried.
"""

RETRIED_REFUSALS: tuple[type[ValidationError], ...] = (SystemChannelMissing, OutboundEmailChannelMissing)
"""Messaging's channel refusals a retry may cure: the system channel's row is missing or not connected.

That is a deployment's state, which an operator can fix while the task waits.
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


def _mail_one(
    ledger: MailNoticeManager[Any], user: Any, *, key: str, run: _Run,
    values: Callable[[], dict[str, Any] | None], **identity: Any,
) -> Mailed:
    """Mail ``user`` the template ``key`` once for ``identity`` in ``ledger``, in a transaction of its own.

    The one send path both ledgers share. An address messaging would refuse
    (``Message.objects.clean_recipient``, asked before the reads and the render) is
    skipped as ``refused``, as is a refusal of the send keyed ``to``, which wrote
    nothing. ``values`` is read inside the transaction; ``None`` means nothing is left
    to mail (``skipped``); it is read in the language of ``user``'s template row, so
    the text it gives reads in that language. The notice is written after the send
    (:meth:`MailNoticeManager.record`), so a send that raises writes none and the next
    run mails the person; a notice another run wrote first is ``already``, and this
    run's message rolls back with this notice. The transaction is durable, never a
    savepoint: inside an outer transaction the send's delivery would wait for it, and
    one person's failure would roll the others back.
    """

    messages = apps.get_model("messaging", "Message").objects
    try:
        _name, address = messages.clean_recipient(user.email or "")
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
                row, reason=mail_reason(key), to=address,
                values={"person": {"first_name": user.first_name}, **content},
            )
            if not ledger.record(user=user, message=message, **identity):
                transaction.set_rollback(True)
                return "already"
    except ValidationError as refusal:
        if not (hasattr(refusal, "error_dict") and set(refusal.error_dict) == {"to"}):
            raise
        return _refused(user, key, refusal)
    return "mailed"


def _failure(error: Exception) -> str:
    """A failure as its log names it: a refusal's codes, else the exception's class."""

    return ", ".join(sorted(refusal_codes(error))) if isinstance(error, ValidationError) else type(error).__name__


def _mail_each(people: Iterable[Any], mail: Callable[[Any], Mailed], *, what: str) -> Counter[str]:
    """Mail each of ``people`` through ``mail``, one at a time, so one person's failure never stops the others.

    A failure is logged by the person's primary key and its refusal code, and counted.
    Once everyone was tried, the run raises: the first failure a retry may cure
    (``retry_may_cure``), else the first, so the task still fails visibly. Celery's
    ``SoftTimeLimitExceeded`` is no person's failure: it is raised at once, so the
    task stops inside its time limit.
    """

    outcomes: Counter[str] = Counter()
    failures: list[Exception] = []
    for user in people:
        try:
            outcomes[mail(user)] += 1
        except SoftTimeLimitExceeded:
            raise
        except Exception as error:
            outcomes["failed"] += 1
            failures.append(error)
            logger.warning("%s to user %s failed (%s).", what, user.pk, _failure(error))
    logger.info("%s: %s.", what, ", ".join(f"{count} {outcome}" for outcome, count in sorted(outcomes.items())))
    if failures:
        raise next((error for error in failures if retry_may_cure(error)), failures[0])
    return outcomes


class MailNoticeManager[NoticeT: MailNotice](AngeeManager[NoticeT]):
    """The send-once write both ledgers share."""

    def record(self, *, user: Any, message: Any, **identity: Any) -> bool:
        """Write the notice that ``message`` mailed ``user`` for ``identity``; ``False`` when another run wrote it.

        Inside the caller's send transaction, in a savepoint of its own. The ledger's
        unique key is the arbiter; a conflict is told from any other integrity failure
        by reading the notice again, and anything else raises.
        """

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
    empties ``message`` and keeps the notice, so nobody is mailed again. Notices are
    otherwise kept as written. Internal to this addon, like a sequence counter: no
    public id, no GraphQL surface and no REBAC resource type.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    message = models.ForeignKey(
        "messaging.Message", null=True, blank=True, on_delete=retained_set_null, related_name="+",
    )

    class Meta:
        """Django model options shared by the decision mail ledgers."""

        abstract = True


class DecisionNoticeQuerySet[NoticeT: DecisionNotice](AppendOnlyQuerySet[NoticeT], AngeeQuerySet[NoticeT]):
    """Question notices are written once, with the message they record."""

    def mailed_since(self, moment: datetime) -> Self:
        """The notices of mails sent since ``moment``."""

        return self.filter(created_at__gte=moment)


_DecisionNoticeManagerBase = AngeeManager.from_queryset(DecisionNoticeQuerySet)


class DecisionNoticeManager[NoticeT: DecisionNotice](
    MailNoticeManager[NoticeT], _DecisionNoticeManagerBase[NoticeT],
):
    """Mails one question to its deciders, once per occasion each."""

    def mail_decision(self, decision_id: Any, *, occasion: str) -> int:
        """Mail an open question to each person in its kind's audience once for ``occasion``; return how many.

        ``occasion`` is ``asked`` or ``reminder``. The audience (:meth:`MailAudience.of`
        the question's kind) is read once, from the question's side
        (:meth:`MailAudience.people`); a person the ledger already holds for the
        question and occasion is not mailed again. A question that closed since, or that nobody
        can be put to, mails nobody. Each person's values are read in their own scope
        as their mail is sent (:func:`question_values`), so a question since closed or
        no longer readable to them is not mailed (``skipped``); this does not depend
        on the authority ``decision_asked`` was sent under, which a supersede elevates.
        Each person is mailed as :func:`_mail_one` says and isolated as
        :func:`_mail_each` says. An unset public origin refuses before anyone is
        mailed. Each person's mail is a durable transaction, so this runs outside any
        transaction, as a task does.
        """

        occasion = DecisionNoticeOccasion(occasion)
        key = occasion.template_key
        questions = apps.get_model("decisions", "Decision")
        with system_context(reason=mail_reason(key)):
            rows = questions.system_queryset().open().filter(pk=decision_id)
            decision = rows.first()
            if decision is None:
                return 0
            mailed = set(self.filter(decision=decision, occasion=occasion).values_list("user_id", flat=True))
            audience = MailAudience.of(decision.kind)
            people = [user for user in audience.people(rows).get(decision.pk, []) if user.pk not in mailed]
        if not people:
            return 0
        run = _Run.start(people, [key])
        shared = shared_values(decision, origin=run.origin)

        def values_for(user: Any) -> dict[str, Any] | None:
            row = (
                questions.objects.with_actor(user).open().filter(pk=decision.pk)
                .prefetch_related(*questions.reader_prefetches(user), questions.requester_names_prefetch(user)).first()
            )
            return None if row is None else {"question": question_values(
                row, user, shared, text_limit=FACT_TEXT_LIMIT, reason_limit=REASON_TEXT_LIMIT,
            )}

        outcomes = _mail_each(
            people,
            lambda user: _mail_one(
                self, user, key=key, run=run, values=lambda: values_for(user), decision=decision, occasion=occasion,
            ),
            what=f"Decision {decision.pk} {occasion.value} mail",
        )
        return outcomes["mailed"]


class DigestNoticeQuerySet[NoticeT: DigestNotice](AppendOnlyQuerySet[NoticeT], AngeeQuerySet[NoticeT]):
    """Digest notices are written once, with the message they record."""


_DigestNoticeManagerBase = AngeeManager.from_queryset(DigestNoticeQuerySet)


class DigestNoticeManager[NoticeT: DigestNotice](MailNoticeManager[NoticeT], _DigestNoticeManagerBase[NoticeT]):
    """Mails each active person one daily digest of the open questions waiting on them."""

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
        the same day mails nobody. Each person's mail is a durable transaction, so this
        runs outside any transaction, as a task does.
        """

        day = now.astimezone(UTC).date()
        questions = apps.get_model("decisions", "Decision")
        notices = apps.get_model("decisions_messaging", "DecisionNotice")
        with system_context(reason=mail_reason(DIGEST_TEMPLATE_KEY)):
            candidates = get_user_model().system_queryset().active_people().exclude(
                pk__in=self.filter(day=day).values("user_id"),
            )
            people = list(candidates.order_by("pk"))
            recent = set(
                notices.objects.mailed_since(now - DIGEST_WINDOW)
                .filter(user__in=candidates.values("pk")).values_list("user_id", "decision_id")
            )
        if not people:
            return 0
        run = _Run.start(people, [DIGEST_TEMPLATE_KEY])
        shared: dict[Any, dict[str, Any]] = {}

        def mail(user: Any) -> Mailed:
            put = models.Q(pk__in=[])
            for audience in MailAudience.used():
                put |= models.Q(pk__in=audience.put_to(questions.objects, user).filter(audience.kinds()).values("pk"))
            waiting = (
                questions.objects.with_actor(user).open().filter(put).order_by(F("due_at").asc(nulls_last=True), "pk")
            )
            listed = sorted(waiting, key=lambda row: (user.pk, row.pk) in recent)
            if all((user.pk, row.pk) in recent for row in listed):
                return "skipped"
            shown = listed[:DIGEST_QUESTION_LIMIT]
            prefetch_related_objects(
                shown, *questions.reader_prefetches(user), questions.requester_names_prefetch(user),
            )
            for row in shown:
                if row.pk not in shared:
                    shared[row.pk] = shared_values(row, origin=run.origin)
            return _mail_one(
                self, user, key=DIGEST_TEMPLATE_KEY, run=run, day=day,
                values=lambda: {
                    "count": len(listed),
                    "more": len(listed) - len(shown),
                    "questions": [
                        question_values(
                            row, user, shared[row.pk],
                            text_limit=DIGEST_FACT_TEXT_LIMIT, reason_limit=DIGEST_REASON_TEXT_LIMIT,
                        )
                        for row in shown
                    ],
                },
            )

        outcomes = _mail_each(people, mail, what=f"Decision digest {day}")
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
