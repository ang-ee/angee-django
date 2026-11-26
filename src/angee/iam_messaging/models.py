"""Source models of emailed sign-in links: the single-use ``SignInLink`` and the account action that issues one.

``UserSignInLinks`` adds ``issue_sign_in_link`` to IAM's account actions through
``User.account_actions`` and ``User.account_action_blockers``, so the users list
offers it exactly where the action admits it.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from email.utils import parseaddr
from functools import partial
from typing import Any, cast
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import EmailMessage, mailers
from django.core.mail.handler import DEFAULT_MAILER_ALIAS
from django.core.validators import validate_email
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_variables
from django_compose.models import Composable
from rebac import SubjectRef, system_context

from angee.base.managers import AngeeManager
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeModel
from angee.base.models.identity import instance_from_public_id
from angee.base.models.timestamps import auto_now_stamps
from angee.base.origins import public_origin

ISSUE_SIGN_IN_LINK = "issue_sign_in_link"
"""The account action that emails a sign-in link, named by its Zed permission on ``auth/user``."""

SIGN_IN_LINK_PATH = "/sign-in-link"
"""The public web route a sign-in link opens; this addon's web fragment declares the same path."""

SIGN_IN_LINK_UNAVAILABLE_MESSAGE = "Sign-in links need a default mailer and ANGEE_PUBLIC_ORIGIN."
"""Refusal of the sign-in link action on a deployment that cannot build or send the link."""

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

    @sensitive_variables("token")
    def issue_sign_in_link(self) -> None:
        """Email an active, unprotected person a single-use sign-in link.

        Permission and eligibility are checked on the locked row, as for every
        account action (``User.lock_for_account_action``), and the account needs a
        valid email address: the link goes to ``email`` only. A link issued
        within ``ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN`` seconds of the
        account's previous one is refused. The row keeps the token's keyed
        digest, never the token. The link leaves through Django's default mailer
        once the issuing transaction commits, so inside a caller's transaction it
        is sent only if that transaction commits, and a mailer failure raises
        from that commit. Where that mailer records mail, the link is stored with
        it, and "accepted" means recorded. The newest link the mailer accepted is
        the only one that signs in.
        """

        account: Any = self  # Composed into iam.User, which owns the lock and the actor.
        with transaction.atomic():
            user = account.lock_for_account_action(ISSUE_SIGN_IN_LINK, expected_revision=None)
            try:
                validate_email(user.email)
            except ValidationError:
                raise ValidationError("This account has no valid email address to send a sign-in link to.") from None
            links = cast(Any, apps.get_model("iam_messaging", "SignInLink")).objects
            link, token = links.issue(user, actor=account.effective_actor(strict=True)[0])
            transaction.on_commit(partial(link.send, token))


def _default_sender_usable() -> bool:
    """Whether ``DEFAULT_FROM_EMAIL`` is an address Django's email validator accepts.

    This is this addon's own check. Messaging's sender rule, which decides whether a
    link's mail can leave where messaging is installed, is stricter: one address as
    written, which the transport can carry (``ChannelManager.system_mail_configured``).
    """

    _name, address = parseaddr(str(settings.DEFAULT_FROM_EMAIL))
    try:
        validate_email(address)
    except ValidationError:
        return False
    return True


def _token_parts(token: str) -> tuple[str, str]:
    """Split a token into its account public id and secret; both are empty for a malformed one."""

    selector, _, secret = token.partition(".") if len(token) <= _SIGN_IN_TOKEN_MAX_LENGTH else ("", "", "")
    return (selector, secret) if selector and secret else ("", "")


class SignInLinkSpent(Exception):
    """A genuine sign-in link that can no longer sign in.

    It is used, expired or retired, no longer its account's newest sent link,
    bound to account facts that have since changed, or for an account that is
    inactive or protected. Its holder had the real link, so this refusal is
    not a guess.
    """


class SignInLinkManager[LinkT: SignInLink](AngeeManager[LinkT]):
    """Issue, redeem and retire sign-in links, always as system work.

    Not a GraphQL surface: ``UserSignInLinks.issue_sign_in_link`` issues a link
    under the account's lock and ``SignInLinkBackend`` redeems one.
    """

    @sensitive_variables("secret")
    def issue(self, user: Any, *, actor: SubjectRef | None) -> tuple[LinkT, str]:
        """Store a fresh link for ``user`` and return it with its token, the token's only copy.

        The caller holds ``user``'s row lock and has admitted the issue. A link
        within ``ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN`` seconds of the
        account's previous one is refused. The token is ``<user public
        id>.<secret>``; the row keeps the secret's keyed digest and the account
        digest (:meth:`SignInLink.token_digest`, :meth:`SignInLink.account_digest`),
        and ``created_by`` is ``actor``.
        """

        now = timezone.now()
        cooldown = timedelta(seconds=int(settings.ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN))
        with system_context(reason="iam_messaging.sign_in_link.issue"):
            if cooldown and self.filter(user=user, created_at__gt=now - cooldown).exists():
                raise ValidationError("A sign-in link was sent to this account moments ago; wait to send another.")
            secret = secrets.token_urlsafe(32)
            link = self.model(
                user=user,
                token_hash=self.model.token_digest(user.pk, secret),
                account_hash=self.model.account_digest(user),
                expires_at=now + timedelta(seconds=int(settings.ANGEE_IAM_MESSAGING_SIGN_IN_LINK_TTL)),
            )
            if actor is not None:
                link.with_actor(actor)
            link.save()
        return link, f"{user.public_id}.{secret}"

    @sensitive_variables("token", "secret", "digest")
    def redeem(self, token: str) -> Any | None:
        """Consume ``token`` and return the person it signs in.

        ``None`` means the token matches no link of a person, as a guess does;
        :class:`SignInLinkSpent` means it matches one that can no longer sign
        in. The token's account public id is not a secret, and an unknown one
        refuses like a wrong secret. The secret's digest is compared with every
        link of the account through ``constant_time_compare``, and only the
        newest link the mailer accepted signs in. It must be unused and
        unexpired, its account facts (:meth:`SignInLink.account_digest`)
        unchanged, and its account an active, unprotected person. The account's
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

SIGN_IN_LINK_SUBJECT = "Your sign-in link"
"""Subject of the email that carries a sign-in link."""


class SignInLink(AuditMixin, AngeeModel, concrete=True):
    """One emailed, single-use sign-in link for a person account.

    No public id, no GraphQL surface, no REBAC resource type.
    ``UserSignInLinks.issue_sign_in_link`` issues links and ``SignInLinkBackend``
    redeems them through :class:`SignInLinkManager`. A row keeps the token's
    keyed digest, never the token; a digest of the account facts the link is
    bound to; when it expires; when the mailer accepted it (``sent_at``); and
    when it signed in (``used_at``). ``created_by`` is the issuer, empty for a
    system issue. Rows stay as the issue record, and only the account's newest
    sent link signs in.

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
        """Whether this deployment can build links and hand them to a mailer: an origin, a sender and a default mailer.

        Where messaging is installed, Django's default mailer always exists (messaging's
        system mailer, which records mail), so this no longer says the mail can leave:
        a host with no transport mailer offers a link whose send raises and stamps
        nothing. Debt: the lane that sends the link's mail replaces this check with
        messaging's predicate, ``Channel.objects.system_mail_configured()``; this
        addon does not depend on messaging today, so that lane adds the dependency.
        """

        origin = public_origin(settings.ANGEE_PUBLIC_ORIGIN, debug=settings.DEBUG)
        return origin is not None and _default_sender_usable() and DEFAULT_MAILER_ALIAS in mailers

    @staticmethod
    def url_for(token: str) -> str:
        """Return the public URL that redeems ``token``; the token rides the fragment, which no request carries."""

        origin = public_origin(settings.ANGEE_PUBLIC_ORIGIN, debug=settings.DEBUG)
        if origin is None:
            raise ImproperlyConfigured(SIGN_IN_LINK_UNAVAILABLE_MESSAGE)
        return f"{origin}{SIGN_IN_LINK_PATH}#{urlencode({'token': token})}"

    @sensitive_variables("token", "body")
    def send(self, token: str) -> None:
        """Email ``token``'s link to the account and record the send.

        The message is rendered here and handed to Django's default mailer as
        plain text; this code neither stores nor logs it or the token. Where
        that mailer records mail, the link is stored with it. Once the mailer
        accepts it, ``sent_at`` is stamped, which makes it the account's current
        link. A mailer failure propagates and leaves ``sent_at`` empty, so the
        previously sent link keeps working.
        """

        user = cast(Any, self).user
        expires = timezone.localtime(self.expires_at)
        body = (
            "Use this link to sign in:\n\n"
            f"{self.url_for(token)}\n\n"
            f"It works once and expires on {expires:%Y-%m-%d %H:%M %Z}. A newer link replaces it.\n\n"
            "If you did not expect this email, you can ignore it.\n"
        )
        EmailMessage(SIGN_IN_LINK_SUBJECT, body, to=[user.email]).send(using=DEFAULT_MAILER_ALIAS)
        now = timezone.now()
        with system_context(reason="iam_messaging.sign_in_link.sent"):
            type(self)._base_manager.filter(pk=self.pk).update(sent_at=now, **auto_now_stamps(type(self), now))
        self.sent_at = now
        logger.info("Sent a sign-in link to user %s.", user.pk)
