"""Source models of emailed sign-in links: the single-use ``SignInLink`` and the account action that issues one.

``UserSignInLinks`` adds ``issue_sign_in_link`` to IAM's account actions through
``User.account_actions`` and ``User.account_action_blockers``, so the users list
offers it exactly where the action admits it. A link's mail is the message
template :data:`SIGN_IN_LINK_TEMPLATE`, which this addon ships, sent as system mail.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import nullcontext
from datetime import timedelta
from typing import Any, cast
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_variables
from django_compose.models import Composable
from rebac import SubjectRef, actor_context, system_context

from angee.base.managers import AngeeManager
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeModel
from angee.base.models.identity import instance_from_public_id
from angee.base.models.timestamps import auto_now_stamps
from angee.base.origins import PublicOriginUnavailable, configured_public_origin

ISSUE_SIGN_IN_LINK = "issue_sign_in_link"
"""The account action that emails a sign-in link, named by its Zed permission on ``auth/user``."""

SIGN_IN_LINK_PATH = "/sign-in-link"
"""The public web route a sign-in link opens; this addon's web fragment declares the same path."""

SIGN_IN_LINK_UNAVAILABLE_MESSAGE = (
    "This deployment cannot send sign-in links: it has no public web address to link to, or no system mail."
)
"""Refusal of the sign-in link action where :meth:`SignInLink.deliverable` is false; its remedy is the README's."""

SIGN_IN_LINK_QUEUED_MESSAGE = "Sign-in link queued for the account's email."
"""The action's answer: the link is recorded and its mail queued; delivery is the system channel's to show."""

SIGN_IN_LINK_NO_ADDRESS_MESSAGE = "This account has no valid email address to send a sign-in link to."
"""Refusal of an account whose address system mail cannot be sent to (messaging's ``clean_system_recipient``)."""

SIGN_IN_LINK_COOLDOWN_MESSAGE = "A sign-in link was queued for this account moments ago; wait to send another."
"""Refusal of a link within ``ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN`` seconds of the account's last one."""

SIGN_IN_LINK_TEMPLATE = "iam_messaging.sign_in_link"
"""The key of the message template a sign-in link is mailed with; this addon ships it in English and Spanish."""

SIGN_IN_LINK_TEMPLATE_MISSING_MESSAGE = (
    "This deployment has not installed the sign-in link's email template; its operator loads its resources "
    "(provision does)."
)
"""Refusal of a link on a deployment whose resources never loaded the template, code ``template_missing``."""

SIGN_IN_LINK_NOT_SHOWN_MESSAGE = (
    "The sign-in link's email template does not show the link, so no link was sent: "
    "edit or delete the deployment's version of the template, or of a block or part it includes."
)
"""Refusal of a link whose effective template, as the deployment wrote or edited it or a row it includes, does not show
the link (the template's ``value_not_printed``), code ``link_not_shown``."""

logger = logging.getLogger(__name__)


class UserSignInLinks(Composable, extends="iam.User"):
    """Same-row donor adding the sign-in link action to IAM's account actions."""

    class Meta:
        abstract = True

    @classmethod
    def account_actions(cls) -> tuple[str, ...]:
        """Offer the sign-in link after IAM's own actions."""

        return (*super().account_actions(), ISSUE_SIGN_IN_LINK)

    @classmethod
    def account_action_blockers(
        cls, action: str, protection: Q, inactive: Q, own: Q | None, *, system: bool,
    ) -> tuple[tuple[Q, str], ...]:
        """Refuse the link for an inactive account and on a deployment that cannot deliver it.

        The deployment check reads settings only, once per call and never per row.
        """

        blockers = super().account_action_blockers(action, protection, inactive, own, system=system)
        if action != ISSUE_SIGN_IN_LINK:
            return blockers
        unavailable = () if SignInLink.deliverable() else ((Q(pk__isnull=False), SIGN_IN_LINK_UNAVAILABLE_MESSAGE),)
        return (*blockers, (inactive, "Only active accounts can receive a sign-in link."), *unavailable)

    def issue_sign_in_link(self) -> None:
        """Mail an active, unprotected person a single-use sign-in link, as system mail.

        Permission and eligibility are checked on the locked row, as for every
        account action (``User.lock_for_account_action``), and the account's
        ``email`` must be an address system mail can be sent to, as messaging's
        ``Message.objects.clean_system_recipient`` decides; the account's own
        refusal (:data:`SIGN_IN_LINK_NO_ADDRESS_MESSAGE`) names no address. Then
        :meth:`SignInLinkManager.issue` records the link and its mail together,
        in this transaction, or refuses with nothing written. The action never
        shows the link.
        """

        account: Any = self  # Composed into iam.User, which owns the lock and the actor.
        with transaction.atomic():
            user = account.lock_for_account_action(ISSUE_SIGN_IN_LINK, expected_revision=None)
            messages = cast(Any, apps.get_model("messaging", "Message")).objects
            try:
                address = messages.clean_system_recipient(user.email)[1]
            except ValidationError:
                raise ValidationError(SIGN_IN_LINK_NO_ADDRESS_MESSAGE) from None
            links = cast(Any, apps.get_model("iam_messaging", "SignInLink")).objects
            links.issue(user, address, actor=account.effective_actor(strict=True)[0])


def _token_parts(token: str) -> tuple[str, str]:
    """Split a token into its account public id and secret; both are empty for a malformed one."""

    selector, _, secret = token.partition(".") if len(token) <= _SIGN_IN_TOKEN_MAX_LENGTH else ("", "", "")
    return (selector, secret) if selector and secret else ("", "")


class SignInLinkSpent(Exception):
    """A genuine sign-in link that can no longer sign in.

    It is used, expired or retired, no longer its account's newest link,
    bound to account facts that have since changed, or for an account that is
    inactive or protected. Its holder had the real link, so this refusal is
    not a guess.
    """


class SignInLinkManager[LinkT: SignInLink](AngeeManager[LinkT]):
    """Issue, redeem and retire sign-in links, always as system work.

    Not a GraphQL surface: ``UserSignInLinks.issue_sign_in_link`` issues a link
    under the account's lock and ``SignInLinkBackend`` redeems one.
    """

    @sensitive_variables()
    def issue(self, user: Any, address: str, *, actor: SubjectRef | None) -> LinkT:
        """Mail ``user`` a fresh link at ``address`` and store it; return the link. The token exists only in the mail.

        The caller holds ``user``'s row lock, has admitted the issue and gives
        the account's address as messaging's ``clean_system_recipient`` returns
        it. In order, each refusal before anything is written: a link
        within ``ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN`` seconds of the
        account's previous one; no row for :data:`SIGN_IN_LINK_TEMPLATE`
        (``template_missing``), looked up for the recipient's person with
        ``MessageTemplateQuerySet.effective`` inside the send's named work; then
        whatever ``MessageTemplateManager.send_as_system`` refuses, as its
        docstring orders it (the system channel missing or paused among it),
        a template whose bodies do not both show the link (``must_print``)
        refused as the deployment's fault (``link_not_shown``, logged as a
        warning naming the row). That send renders the template with the link,
        its expiry and the person's first name, and records it on messaging's system channel,
        queued for delivery when the outermost transaction commits. Only then is
        the row saved, its ``sent_at`` stamped: recorded and queued, which makes
        it the account's current link at once. The send and the save are one
        atomic block of their own, so the mail and its link commit together or
        not at all, whether or not the caller holds a transaction. The token is ``<user public
        id>.<secret>``; the row keeps the secret's keyed digest and the account
        digest (:meth:`SignInLink.token_digest`, :meth:`SignInLink.account_digest`).
        The send runs as ``actor``, so the link and its mail record one causing
        actor as ``created_by``; with none, both take the ambient one, empty for
        system work. Nothing here logs the token or holds it past the send.
        """

        now = timezone.now()
        cooldown = timedelta(seconds=int(settings.ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN))
        with system_context(reason="iam_messaging.sign_in_link.issue"):
            if cooldown and self.filter(user=user, created_at__gt=now - cooldown).exists():
                raise ValidationError(SIGN_IN_LINK_COOLDOWN_MESSAGE)
        reason = "iam_messaging.sign_in_link.send"
        templates = cast(Any, apps.get_model("messaging_templates", "MessageTemplate")).objects
        with system_context(reason=reason):
            person = apps.get_model("parties", "Person").objects.filter(user_id=user.pk).first()
            try:
                template = templates.effective("template", SIGN_IN_LINK_TEMPLATE, person=person)
            except templates.model.DoesNotExist:
                raise ValidationError(SIGN_IN_LINK_TEMPLATE_MISSING_MESSAGE, code="template_missing") from None
        secret = secrets.token_urlsafe(32)
        token = f"{user.public_id}.{secret}"
        link = self.model(
            user=user,
            token_hash=self.model.token_digest(user.pk, secret),
            account_hash=self.model.account_digest(user),
            expires_at=now + timedelta(seconds=int(settings.ANGEE_IAM_MESSAGING_SIGN_IN_LINK_TTL)),
        )
        values = {
            "link": link.url_for(token),
            "expires_at": link.expires_at,
            "person": {"first_name": user.first_name},
        }
        with transaction.atomic():
            try:
                with actor_context(actor) if actor is not None else nullcontext():
                    templates.send_as_system(template, reason=reason, to=address, values=values, must_print=["link"])
            except ValidationError as refusal:
                # The template addon refuses a body that hides a value with items coded ``value_not_printed`` alone
                # (``MessageTemplate.render``); Django's own merge reads the codes of a refusal of either shape. A
                # refusal class of the template addon's own replaces this reading once it has one.
                if {item.code for items in refusal.update_error_dict({}).values() for item in items} != {
                    "value_not_printed",
                }:
                    raise
                logger.warning(
                    "Refused a sign-in link: the message template %s (%s), row %s, or a block or part it includes, "
                    "does not show the link.",
                    template.handler.template_name(template.key), template.language, template.public_id,
                )
                raise ValidationError(SIGN_IN_LINK_NOT_SHOWN_MESSAGE, code="link_not_shown") from None
            link.sent_at = timezone.now()
            with system_context(reason="iam_messaging.sign_in_link.issue"):
                if actor is not None:
                    link.with_actor(actor)
                link.save()
        logger.info("Queued a sign-in link for user %s.", user.pk)
        return link

    @sensitive_variables()
    def redeem(self, token: str) -> Any | None:
        """Consume ``token`` and return the person it signs in.

        ``None`` means the token matches no link of a person, as a guess does;
        :class:`SignInLinkSpent` means it matches one that can no longer sign
        in. The token's account public id is not a secret, and an unknown one
        refuses like a wrong secret. The secret's digest is compared with every
        link of the account through ``constant_time_compare``, and only the
        newest sent link signs in. It must be unused and unexpired, its account
        facts (:meth:`SignInLink.account_digest`) unchanged, and its account an
        active, unprotected person. The account's
        row lock serializes redemption with issuing, and a conditional update
        consumes the link, so a token signs in once on every database backend.
        """

        selector, secret = _token_parts(token)
        reason = "iam_messaging.sign_in_link.redeem"
        users = get_user_model().objects.system_context(reason=reason).people().lock_if_supported()
        with transaction.atomic(), system_context(reason=reason):
            user = instance_from_public_id(get_user_model(), selector, queryset=users) if selector else None
            if user is None:
                return None
            digest = self.model.token_digest(user.pk, secret)
            links = list(self.filter(user=user).order_by("-pk"))
            matched = None
            for link in links:
                if constant_time_compare(link.token_hash, digest):
                    matched = link
            if matched is None:
                return None
            now = timezone.now()
            current = next((link for link in links if link.sent_at is not None), None)
            if (
                matched is not current
                or matched.used_at is not None
                or matched.expires_at <= now
                or not constant_time_compare(matched.account_hash, self.model.account_digest(user))
                or not user.is_active
                or user.protected_against(None)
            ):
                raise SignInLinkSpent
            consumed = self.filter(pk=matched.pk, used_at__isnull=True).update(
                used_at=now,
                **auto_now_stamps(self.model, now),
            )
            if consumed != 1:
                raise SignInLinkSpent
        logger.info("Signed in user %s with a sign-in link.", user.pk)
        return user

    def retire(self, user: Any) -> int:
        """Expire ``user``'s unused links now, so none of them signs in later; the rows stay."""

        now = timezone.now()
        with system_context(reason="iam_messaging.sign_in_link.retire"):
            return self.filter(user=user, used_at__isnull=True, expires_at__gt=now).update(
                expires_at=now,
                **auto_now_stamps(self.model, now),
            )


_SIGN_IN_TOKEN_MAX_LENGTH = 256
"""Longest token ``redeem`` reads; issued tokens are a public id, a dot and 43 characters."""

_SIGN_IN_LINK_SALT = "angee.iam_messaging.sign_in_link"
"""``salted_hmac`` key salt of the stored token digest."""

_SIGN_IN_ACCOUNT_SALT = "angee.iam_messaging.sign_in_link.account"
"""``salted_hmac`` key salt of the stored account digest."""


class SignInLink(AuditMixin, AngeeModel, concrete=True):
    """One emailed, single-use sign-in link for a person account.

    No public id, no GraphQL surface, no REBAC resource type.
    ``UserSignInLinks.issue_sign_in_link`` issues links and ``SignInLinkBackend``
    redeems them through :class:`SignInLinkManager`. A row keeps the token's
    keyed digest, never the token; a digest of the account facts the link is
    bound to; when it expires; when its mail was recorded and queued on the
    system channel (``sent_at``); and when it signed in (``used_at``).
    ``created_by`` is the issuer, empty for a system issue. Rows stay as the
    issue record, and only the account's newest sent link signs in.

    Django's ``PasswordResetTokenGenerator`` derives its tokens from account
    state: two links issued for one account stay valid together until the
    account next signs in or changes its password, and nothing records who
    issued them. Retiring the previous link on reissue and the issuer record
    need a row. ``SECRET_KEY`` keys both digests and ``salted_hmac`` ignores
    ``SECRET_KEY_FALLBACKS``, so rotating the key voids every outstanding link.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sign_in_links")
    token_hash = models.CharField(max_length=64)
    account_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    objects = SignInLinkManager()

    class Meta:
        """Django model options for the sign-in link source."""

        abstract = True

    @staticmethod
    def token_digest(user_pk: Any, secret: str) -> str:
        """Return the stored digest of ``secret`` for the account ``user_pk``, keyed by ``SECRET_KEY``."""

        return salted_hmac(_SIGN_IN_LINK_SALT, f"{user_pk}\x00{secret}", algorithm="sha256").hexdigest()

    @staticmethod
    def account_digest(user: Any) -> str:
        """Return the digest of the account facts a link is bound to: email, password and last sign-in.

        As in Django's ``PasswordResetTokenGenerator``, a new email address, a
        password set or changed and any sign-in each retire the account's
        outstanding links.
        """

        last_login = "" if user.last_login is None else user.last_login.replace(microsecond=0, tzinfo=None)
        value = f"{user.pk}\x00{user.email}\x00{user.password}\x00{last_login}"
        return salted_hmac(_SIGN_IN_ACCOUNT_SALT, value, algorithm="sha256").hexdigest()

    @staticmethod
    def token_account(token: str) -> str:
        """Return the account public id ``token`` names, or ``""``: the per-account key for login throttling."""

        return _token_parts(token)[0]

    @classmethod
    def deliverable(cls) -> bool:
        """Whether this deployment can build links and send their mail, read from settings alone.

        Its public origin (``angee.base.origins.configured_public_origin``) and
        system mail configured (``Channel.objects.system_mail_configured``). What
        settings cannot tell, the system channel's row and the template's, the
        send refuses in band with nothing written.
        """

        try:
            configured_public_origin()
        except PublicOriginUnavailable:
            return False
        return bool(cast(Any, apps.get_model("messaging", "Channel")).objects.system_mail_configured())

    @staticmethod
    def url_for(token: str) -> str:
        """Return the public URL that redeems ``token``; the token rides the fragment, which no request carries.

        The origin is base's (``configured_public_origin``), which raises ``PublicOriginUnavailable`` without one.
        """

        return f"{configured_public_origin()}{SIGN_IN_LINK_PATH}#{urlencode({'token': token})}"
