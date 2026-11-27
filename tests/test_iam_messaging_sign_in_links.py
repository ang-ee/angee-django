"""Emailed sign-in links: the account action that issues one, its mail, its single-use redemption and the axes seam."""

import functools
import inspect
import logging
import pickle
import re
import secrets
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from smtplib import SMTPRecipientsRefused, SMTPServerDisconnected
from typing import Any
from urllib.parse import unquote

import pytest
from axes.models import AccessAttempt
from django.apps import apps
from django.conf import settings as django_settings
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, authenticate, get_user, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_login_failed
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ValidationError
from django.core.mail.backends.base import BaseEmailBackend
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.signals import post_init, pre_save
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.errors import MissingActorError
from rebac.schema import parse_zed

import angee.iam_messaging
from angee.base.build import merged_schemas
from angee.base.origins import PublicOriginUnavailable
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from angee.iam import schema as iam_schema
from angee.iam.auth import ONE_ATTEMPT_MESSAGE
from angee.iam.autoconfig import SETTINGS as IAM_SETTINGS
from angee.iam_messaging import schema as iam_messaging_schema
from angee.iam_messaging.auth import sign_in_credentials
from angee.iam_messaging.autoconfig import SETTINGS as LINK_SETTINGS
from angee.iam_messaging.models import (
    ISSUE_SIGN_IN_LINK,
    SIGN_IN_LINK_COOLDOWN_MESSAGE,
    SIGN_IN_LINK_NO_ADDRESS_MESSAGE,
    SIGN_IN_LINK_NO_BRAND_KIT_MESSAGE,
    SIGN_IN_LINK_NOT_RENDERED_MESSAGE,
    SIGN_IN_LINK_NOT_SHOWN_MESSAGE,
    SIGN_IN_LINK_PATH,
    SIGN_IN_LINK_QUEUED_MESSAGE,
    SIGN_IN_LINK_TEMPLATE,
    SIGN_IN_LINK_TEMPLATE_MISSING_MESSAGE,
    SIGN_IN_LINK_UNAVAILABLE_MESSAGE,
)
from angee.iam_messaging.testing.models import SignInLink
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Fragment, Handle, Message, Person
from angee.messaging_templates.engine import INVALID_VALUES, VALUE_NOT_PRINTED
from angee.messaging_templates.testing.models import MessageTemplate
from angee.resources.testing.models import Resource
from tests.conftest import addon_schema, create_platform_admin
from tests.iam_campaign import iam_admin as iam_admin
from tests.test_iam_account_actions import _manager
from tests.test_iam_login_throttle import LOGIN, _refusals
from tests.test_messaging_compose import _refused
from tests.test_messaging_system_mail import (
    LOCMEM,
    SENDER,
    FailingTransport,
    _chained,
    _delivery_logged,
    _failing,
    _install,
    _message_insert,
    _reported_secret,
)
from tests.test_messaging_templates_render import _reading
from tests.test_zed_extensions import _addon, _contrib_addon

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

User = get_user_model()
ORIGIN = "https://app.example.test"
DELIVERABLE = {
    "MAILERS": {"transport": LOCMEM},
    "DEFAULT_FROM_EMAIL": SENDER,
    "ANGEE_PUBLIC_ORIGIN": ORIGIN,
    "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": 0,
}
"""A deployment whose settings let links be mailed: a transport mailer, a valid default sender, a public origin."""
SECRET = "Sentinel_secret_never_logged_or_stored_0042"
"""A link secret a test mints in place of ``secrets.token_urlsafe(32)``, to look for it everywhere afterwards."""
READ = "tests.iam_messaging.sign_in_link.read"
ISSUE = "mutation($id: ID!) { issue_user_sign_in_link(id: $id) { ok message } }"
REDEEM = "mutation($token: String!) { redeem_sign_in_link(token: $token) { ok user { username } } }"
SPENT_MARK = "angee_iam_spent_credential"
LIST = "{ users(limit: 100) { username account_actions } }"
# The composed order, axes first: the bare harness authors ModelBackend ahead of it.
BACKENDS = [*IAM_SETTINGS["AUTHENTICATION_BACKENDS:append"], *LINK_SETTINGS["AUTHENTICATION_BACKENDS:append"]]


def _schema(name: str) -> Any:
    """IAM's schema bucket with this addon's contributions, as a host composing both serves it."""

    return addon_schema(iam_schema.schemas, name, iam_messaging_schema.schemas)


class RefusingTransport(BaseEmailBackend):
    """A transport whose server refuses the recipient for good, as a mailbox that does not exist is."""

    def send_messages(self, email_messages: Any) -> int:
        raise SMTPRecipientsRefused({message.to[0]: (550, b"No such mailbox") for message in email_messages})


class UnreachableTransport(BaseEmailBackend):
    """A transport whose server cannot be reached: the connection fails before anything is sent."""

    def open(self) -> bool:
        raise ConnectionRefusedError("mail server unreachable")

    def send_messages(self, email_messages: Any) -> int:
        raise AssertionError("nothing is sent through a connection that never opened")


class DroppingTransport(BaseEmailBackend):
    """A transport whose connection drops while the message is being sent, so nobody knows whether it left."""

    def send_messages(self, email_messages: Any) -> int:
        raise SMTPServerDisconnected("connection dropped mid-send")


class RefusingDefaultMailer(BaseEmailBackend):
    """Django's default mailer replaced by one that refuses every message."""

    def send_messages(self, email_messages: Any) -> int:
        raise OSError("the default mailer is not the link's path")


def _transport(backend: type[BaseEmailBackend]) -> dict[str, Any]:
    return {"transport": {"BACKEND": f"{__name__}.{backend.__name__}"}}


def _load_mail() -> Any:
    """Load what a link's mail needs, as provision does, and return the system channel.

    Messaging's system channel and its vendor, the shipped brand kit, the message template library, then this
    addon's template rows, which depend on the library's layout.
    """

    channel = _install()
    Resource.objects.load_addons(
        tuple(apps.get_app_config(label) for label in ("brandkit", "messaging_templates", "iam_messaging")),
        tiers=[Resource.Tier.INSTALL],
    )
    return channel


@pytest.fixture
def deliverable(db: None) -> Iterator[Any]:
    """A deployment that mails links (:data:`DELIVERABLE`) with the rows provision loads; yields the system channel."""

    del db
    with override_settings(**DELIVERABLE):
        yield _load_mail()


@pytest.fixture
def sent(django_capture_on_commit_callbacks: Any) -> Any:
    """Issue a link as ``actor`` in a committed request, and return the token its recorded mail carries."""

    def issue(target: Any, actor: Any) -> str:
        with django_capture_on_commit_callbacks(execute=True), actor_context(actor):
            target.with_actor(actor).issue_sign_in_link()
        return _token(_written(target).body)

    return issue


def _person(username: str, **attrs: Any) -> Any:
    return User.objects.create_user(username, f"{username}@example.com", **attrs)


def _mails(target: Any) -> list[Any]:
    """The system mail recorded to ``target``'s address in any letter case, oldest first."""

    with system_context(reason=READ):
        return list(
            Message._base_manager.select_related("channel")
            .filter(recipients__handle__value__iexact=target.email).order_by("pk"),
        )


def _written(target: Any) -> Any:
    """``target``'s newest system mail as the system channel's backend writes it to send."""

    message = _mails(target)[-1]
    with system_context(reason=READ):
        return message.channel.backend.email_message(message)


def _html(email: Any) -> str:
    (html,) = [alternative.content for alternative in email.alternatives if alternative.mimetype == "text/html"]
    return html


def _hrefs(html: str) -> list[str]:
    """Every link a browser reads in ``html``, by html5lib."""

    markup, _raw = _reading(html, scripting=False)
    return [dict(item[2]).get("href", "") for item in markup if item[0] == "start" and item[1] == "a"]


def _deliver(message: Any) -> dict[str, Any]:
    """Deliver ``message`` through the transport mailer as the worker does."""

    return deliver_message(message._meta.label_lower, message.pk, message.external_id)


def _token(body: str) -> str:
    match = re.search(rf"{re.escape(ORIGIN)}/sign-in-link#token=(\S+)", body)
    assert match is not None, "The mail carries no sign-in link."
    return unquote(match.group(1))


def _request() -> Any:
    return graphql_request(AnonymousUser(), session=True)


def _at(ip: str, *, session: bool = False) -> Any:
    """Return a GraphQL request from ``ip``; ``session`` adds the anonymous session a sign-in rotates."""

    request = RequestFactory().post("/graphql/public/", REMOTE_ADDR=ip)
    if session:
        request.user = AnonymousUser()
        SessionMiddleware(lambda _request: HttpResponse()).process_request(request)
    return request


def _redeem(token: str, ip: str = "198.51.100.1") -> Any:
    """Authenticate ``token`` from ``ip`` with the credentials the redeem mutation passes."""

    return authenticate(_at(ip), **sign_in_credentials(token))


def test_issue_records_one_system_mail_to_the_account_and_stores_only_its_digest(deliverable, iam_admin, sent, caplog):
    target = _person("guest-ada")
    with caplog.at_level(logging.DEBUG):
        token = sent(target, iam_admin)

    [message] = _mails(target)
    assert (message.channel_id, message.status, message.direction) == (deliverable.pk, "queued", "outbound")
    assert _written(target).to == ["guest-ada@example.com"]
    assert mail.outbox == []
    secret = token.partition(".")[2]
    assert token.startswith(f"{target.public_id}.") and len(secret) == 43
    link = SignInLink._base_manager.get(user=target)
    stored = " ".join(str(value) for value in SignInLink._base_manager.filter(pk=link.pk).values().get().values())
    in_row = secret in stored or token in stored
    assert not in_row
    assert link.token_hash == SignInLink.token_digest(target.pk, secret)
    assert link.account_hash == SignInLink.account_digest(target)
    assert link.created_by_id == iam_admin.pk
    assert link.sent_at is not None and link.used_at is None
    ttl = timedelta(seconds=259_200)
    assert link.created_at + ttl - timedelta(seconds=5) <= link.expires_at <= link.created_at + ttl
    logged = secret in caplog.text
    assert not logged


def test_a_link_signs_in_exactly_once(deliverable, iam_admin, sent):
    target = _person("guest-once")
    token = sent(target, iam_admin)

    assert _redeem(token) == target
    link = SignInLink._base_manager.get(user=target)
    assert link.used_at is not None
    assert _redeem(token) is None


def test_a_newer_link_retires_every_older_one(deliverable, iam_admin, sent):
    target = _person("guest-twice")
    first = sent(target, iam_admin)
    second = sent(target, iam_admin)

    assert first != second
    assert _redeem(first) is None
    assert _redeem(second) == target
    assert SignInLink._base_manager.filter(user=target).count() == 2


def test_an_expired_link_is_refused_and_stays_unused(deliverable, iam_admin, sent):
    target = _person("guest-late")
    token = sent(target, iam_admin)
    SignInLink._base_manager.filter(user=target).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert _redeem(token) is None
    assert SignInLink._base_manager.get(user=target).used_at is None


@pytest.mark.parametrize(
    "mangle",
    [
        lambda token, other: token[:-1] + ("A" if token[-1] != "A" else "B"),
        lambda token, other: f"{other.public_id}.{token.partition('.')[2]}",
        lambda token, other: token.partition(".")[2],
        lambda token, other: "",
        lambda token, other: f"{token}{'x' * 300}",
        lambda token, other: "not-a-user.secret",
    ],
)
def test_tampered_or_malformed_tokens_are_refused_alike(deliverable, iam_admin, sent, mangle):
    target = _person("guest-real")
    other = _person("guest-other")
    sent(other, iam_admin)
    token = sent(target, iam_admin)

    assert _redeem(mangle(token, other)) is None
    assert SignInLink._base_manager.filter(used_at__isnull=False).count() == 0
    assert _redeem(token) == target


@pytest.mark.parametrize(
    "change",
    [{"is_staff": True}, {"is_superuser": True}, {"is_active": False}, {"email": "moved@example.com"}],
)
def test_redemption_rechecks_the_account(deliverable, iam_admin, sent, change):
    target = _person("guest-changed")
    token = sent(target, iam_admin)
    with system_context(reason="test.iam_messaging.sign_in_link.change"):
        User._base_manager.filter(pk=target.pk).update(**change)

    assert _redeem(token) is None
    assert SignInLink._base_manager.get(user=target).used_at is None


def test_issue_requires_the_permission_and_an_actor_or_the_system(
    deliverable, iam_admin, django_capture_on_commit_callbacks
):
    target = _person("guest-gated")
    plain = _person("plain-member")
    with pytest.raises(MissingActorError):
        target.issue_sign_in_link()
    with actor_context(plain), pytest.raises(PermissionDenied):
        target.with_actor(plain).issue_sign_in_link()
    assert not SignInLink._base_manager.exists() and _mails(target) == []

    with (
        django_capture_on_commit_callbacks(execute=True),
        system_context(reason="test.iam_messaging.sign_in_link.system"),
    ):
        User._base_manager.get(pk=target.pk).issue_sign_in_link()
    # System work: no causing actor, so neither the link nor its mail records one.
    assert SignInLink._base_manager.get(user=target).created_by_id is None
    [message] = _mails(target)
    assert message.created_by_id is None


@pytest.mark.parametrize(
    "attrs,refusal",
    [
        ({"is_active": False}, "Only active accounts can receive a sign-in link"),
        ({"is_staff": True}, "protected"),
        ({"is_superuser": True}, "protected"),
        ({"kind": "service"}, "Only person accounts"),
        ({"email": ""}, "no valid email address"),
        ({"email": "not-an-address"}, "no valid email address"),
        # Messaging's system mail rule decides, before anything is sent, and the account's refusal names no address:
        # the transport carries no address whose part before the @ is outside ASCII (cannot_carry) ...
        ({"email": "jörg@example.com"}, "no valid email address"),
        # ... and refuses a quoted local part holding an escaped space, which Django's own validator admits (invalid).
        ({"email": '"a\\ b"@example.com'}, "no valid email address"),
    ],
)
def test_issue_refuses_ineligible_accounts_and_sends_nothing(deliverable, iam_admin, attrs, refusal):
    target = User.objects.create_user("ineligible", **{"email": "ineligible@example.com", **attrs})
    with actor_context(iam_admin), pytest.raises(ValidationError, match=refusal) as refused:
        target.with_actor(iam_admin).issue_sign_in_link()
    if "email" in attrs:  # the account's own words, which name no address, and messaging's left behind
        assert refused.value.messages == [SIGN_IN_LINK_NO_ADDRESS_MESSAGE]
        assert refused.value.__suppress_context__ and refused.value.__cause__ is None
    with system_context(reason=READ):
        assert not SignInLink._base_manager.exists() and not Message._base_manager.exists()


def test_issue_refuses_the_actors_own_account(deliverable, db):
    admin = create_platform_admin("self-admin", password=None)
    with actor_context(admin), pytest.raises(ValidationError, match="protected"):
        User._base_manager.get(pk=admin.pk).with_actor(admin).issue_sign_in_link()


def test_a_rolled_back_issue_sends_nothing(deliverable, iam_admin, django_capture_on_commit_callbacks):
    target = _person("guest-rolled-back")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError, match="caller failed"), transaction.atomic():
            target.with_actor(iam_admin).issue_sign_in_link()
            raise RuntimeError("caller failed")
    assert callbacks == []
    with system_context(reason=READ):
        assert not SignInLink._base_manager.exists() and not Message._base_manager.exists()


@pytest.mark.parametrize("missing", ["MAILERS", "DEFAULT_FROM_EMAIL", "ANGEE_PUBLIC_ORIGIN"])
def test_an_undeliverable_deployment_offers_no_link_and_refuses(iam_admin, missing):
    target = _person("guest-undeliverable")
    broken = {"MAILERS": {}, "DEFAULT_FROM_EMAIL": "not an address", "ANGEE_PUBLIC_ORIGIN": "https://app.example.test/path"}
    settings = {**DELIVERABLE, missing: broken[missing]}
    with override_settings(**settings):
        assert ISSUE_SIGN_IN_LINK not in target.account_actions_for(to_subject_ref(iam_admin))
        with (
            actor_context(iam_admin),
            pytest.raises(ValidationError, match=re.escape(SIGN_IN_LINK_UNAVAILABLE_MESSAGE)),
        ):
            target.with_actor(iam_admin).issue_sign_in_link()
    assert not SignInLink._base_manager.exists()


def test_account_actions_offer_the_link_where_the_action_acts(deliverable, iam_admin):
    _person("guest-offered")
    _person("guest-inactive", is_active=False)
    _person("guest-staff", is_staff=True)
    rows = result_data(execute_schema(_schema("console"), LIST, user=iam_admin))["users"]
    offered = {row["username"]: ISSUE_SIGN_IN_LINK in row["account_actions"] for row in rows}
    assert offered["guest-offered"] is True
    assert offered["guest-inactive"] is offered["guest-staff"] is offered["campaign-admin"] is False


def test_graphql_issue_and_redeem_sign_the_person_in_once(deliverable, iam_admin, django_capture_on_commit_callbacks):
    target = _person("guest-graphql")
    with django_capture_on_commit_callbacks(execute=True):
        issued = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert result_data(issued)["issue_user_sign_in_link"] == {
        "ok": True,
        "message": SIGN_IN_LINK_QUEUED_MESSAGE,
    }
    token = _token(_written(target).body)
    answered = token.partition(".")[2] in str(issued.data)
    assert not answered
    # Recorded by the system, caused by the administrator whose request it was; that grants nothing.
    assert _mails(target)[-1].created_by_id == iam_admin.pk

    request = _request()
    signed_in = execute_schema(_schema("public"), REDEEM, {"token": token}, request=request)
    assert result_data(signed_in)["redeem_sign_in_link"] == {"ok": True, "user": {"username": "guest-graphql"}}
    assert str(request.session[SESSION_KEY]) == str(target.pk)
    reused = execute_schema(_schema("public"), REDEEM, {"token": token}, request=_request())
    assert result_data(reused)["redeem_sign_in_link"] == {"ok": False, "user": None}


def test_graphql_issue_without_the_permission_sends_nothing(deliverable, iam_admin):
    target = _person("guest-denied")
    plain = _person("plain-viewer")
    denied = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=plain)
    assert result_data(denied)["issue_user_sign_in_link"]["ok"] is False
    assert not SignInLink._base_manager.exists() and _mails(target) == []


@override_settings(AXES_FAILURE_LIMIT=2, AUTHENTICATION_BACKENDS=BACKENDS)
def test_axes_throttles_guessed_links_without_seeing_the_token(deliverable, iam_admin, sent):
    target = _person("guest-throttled")
    token = sent(target, iam_admin)
    secret = token.partition(".")[2]
    failures: list[dict[str, Any]] = []

    def receive(sender: Any, credentials: dict[str, Any], **kwargs: Any) -> None:
        failures.append(dict(credentials))

    request = _at("203.0.113.9")
    user_login_failed.connect(receive, weak=False)
    try:
        for attempt in range(2):
            assert authenticate(request, **sign_in_credentials(f"{target.public_id}.wrong-{attempt}")) is None
        assert authenticate(request, **sign_in_credentials(token)) is None
    finally:
        user_login_failed.disconnect(receive)

    assert SignInLink._base_manager.get(user=target).used_at is None
    masked = {"sign_in_token": "********************", "sign_in_account": target.public_id}
    assert failures and all(credentials == masked for credentials in failures)
    attempts = list(AccessAttempt.objects.values())
    recorded = secret in str(attempts) or "wrong-" in str(attempts)
    assert attempts
    assert not recorded
    assert {attempt["username"] for attempt in attempts} == {target.public_id}
    assert _redeem(token, "203.0.113.10") == target


@override_settings(AXES_FAILURE_LIMIT=3, AUTHENTICATION_BACKENDS=BACKENDS)
def test_genuine_spent_links_never_lock_a_person_out(deliverable, iam_admin, sent):
    target = _person("guest-stale")
    used = sent(target, iam_admin)
    assert _redeem(used, "198.51.100.7") == target
    superseded = sent(target, iam_admin)
    expired = sent(target, iam_admin)
    SignInLink._base_manager.filter(user=target).update(expires_at=timezone.now() - timedelta(seconds=1))
    retired = sent(target, iam_admin)
    with system_context(reason="test.iam_messaging.sign_in_link.password"):
        account = User._base_manager.get(pk=target.pk)
        account.set_password("correct-horse-battery")
        account.save(update_fields=["password", "updated_at"])
    current = sent(target, iam_admin)
    request = _at("198.51.100.7")
    for stale in (used, superseded, expired, retired, superseded, used):
        assert authenticate(request, **sign_in_credentials(stale)) is None

    assert not AccessAttempt.objects.filter(ip_address="198.51.100.7").exists()
    assert authenticate(request, username="guest-stale", password="correct-horse-battery") == target
    assert authenticate(request, **sign_in_credentials(current)) == target


@override_settings(AXES_FAILURE_LIMIT=2, AUTHENTICATION_BACKENDS=BACKENDS)
def test_a_spent_link_exempts_only_its_own_attempt_in_a_batched_request(deliverable, iam_admin, sent):
    target = _person("guest-batched")
    used = sent(target, iam_admin)
    assert _redeem(used) == target
    current = sent(target, iam_admin)
    request = _at("198.51.100.8")

    def failures() -> int:
        return sum(
            AccessAttempt.objects.filter(ip_address="198.51.100.8").values_list("failures_since_start", flat=True)
        )

    for token in (used, f"{target.public_id}.guess-1", used):
        assert authenticate(request, **sign_in_credentials(token)) is None
    assert failures() == 1
    assert authenticate(request, **sign_in_credentials(f"{target.public_id}.guess-2")) is None
    assert failures() == 2
    # The lockout now holds even after a spent link marked this same request.
    assert authenticate(request, **sign_in_credentials(used)) is None
    assert authenticate(request, **sign_in_credentials(current)) is None
    assert SignInLink._base_manager.filter(user=target, used_at__isnull=True).count() == 1


@override_settings(AXES_FAILURE_LIMIT=2, AUTHENTICATION_BACKENDS=BACKENDS)
def test_a_lockout_lifts_after_the_cool_off_and_a_sign_in_clears_nothing(deliverable, iam_admin, sent):
    target = _person("guest-cooled")
    token = sent(target, iam_admin)
    request = _at("198.51.100.9")
    for attempt in range(2):
        assert authenticate(request, **sign_in_credentials(f"{target.public_id}.guess-{attempt}")) is None
    assert authenticate(request, **sign_in_credentials(token)) is None

    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(hours=1, minutes=1))
    assert authenticate(request, **sign_in_credentials(f"{target.public_id}.guess-again")) is None
    signed_in = execute_schema(_schema("public"), REDEEM, {"token": token}, request=_at("198.51.100.9", session=True))
    assert result_data(signed_in)["redeem_sign_in_link"]["ok"] is True
    # Only the cool-off lifts failures; a sign-in from the same client keeps them.
    assert AccessAttempt.objects.get(ip_address="198.51.100.9").failures_since_start == 1


@pytest.mark.parametrize("parameters", [["ip_address"], ["username"], ["username", "ip_address"]])
def test_link_failures_are_recorded_under_the_account_they_name(deliverable, iam_admin, sent, parameters):
    victim = _person("guest-victim")
    bystander = _person("guest-bystander")
    victim_token = sent(victim, iam_admin)
    bystander_token = sent(bystander, iam_admin)
    with override_settings(AXES_LOCKOUT_PARAMETERS=parameters, AXES_FAILURE_LIMIT=3, AUTHENTICATION_BACKENDS=BACKENDS):
        sprayer = _at("192.0.2.1")
        for attempt in range(3):
            assert authenticate(sprayer, **sign_in_credentials(f"usr_nobody{attempt}.bogus")) is None
        for attempt in range(3):
            # One guess at the victim's account from each of three addresses.
            guess = sign_in_credentials(f"{victim.public_id}.bogus{attempt}")
            assert authenticate(_at(f"192.0.2.{10 + attempt}"), **guess) is None
        # No shared empty username: anyone else, elsewhere, signs in.
        assert _redeem(bystander_token, "203.0.113.77") == bystander
        # The victim's own account is throttled exactly when the account is a lockout key.
        expected = None if "username" in parameters else victim
        assert _redeem(victim_token, "203.0.113.78") == expected
    usernames = set(AccessAttempt.objects.values_list("username", flat=True))
    assert None not in usernames and victim.public_id in usernames


def test_deactivation_retires_links_for_good(deliverable, iam_admin, sent):
    target = _person("guest-offboarded")
    token = sent(target, iam_admin)
    with actor_context(iam_admin):
        revision = User._base_manager.get(pk=target.pk).account_revision
        target.with_actor(iam_admin).set_active(False, confirmed=True, expected_revision=revision)
        target.with_actor(iam_admin).set_active(True, confirmed=True, expected_revision=revision + 1)

    assert _redeem(token) is None
    link = SignInLink._base_manager.get(user=target)
    assert link.used_at is None and link.expires_at <= timezone.now()


@pytest.mark.parametrize("change", ["password", "sign_in"])
def test_a_password_change_or_any_sign_in_retires_outstanding_links(deliverable, iam_admin, sent, change):
    target = _person("guest-moved-on")
    token = sent(target, iam_admin)
    with system_context(reason="test.iam_messaging.sign_in_link.account"):
        account = User._base_manager.get(pk=target.pk)
        if change == "password":
            account.set_password("a-new-password-1")
            account.save(update_fields=["password", "updated_at"])
        else:
            User._base_manager.filter(pk=target.pk).update(last_login=timezone.now())

    assert _redeem(token) is None
    assert SignInLink._base_manager.get(user=target).used_at is None


def test_redemption_cycles_the_session_key(deliverable, iam_admin, sent):
    target = _person("guest-session")
    token = sent(target, iam_admin)
    request = _request()
    request.session["planted"] = "value"
    request.session.save()
    before = request.session.session_key

    redeemed = execute_schema(_schema("public"), REDEEM, {"token": token}, request=request)

    assert result_data(redeemed)["redeem_sign_in_link"]["ok"] is True
    assert str(request.session[SESSION_KEY]) == str(target.pk)
    assert request.session.session_key != before


def test_issue_waits_out_the_per_account_cooldown(deliverable, iam_admin, sent):
    target = _person("guest-cooldown")
    sent(target, iam_admin)
    with override_settings(ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN=60):
        with actor_context(iam_admin), pytest.raises(ValidationError) as refused:
            target.with_actor(iam_admin).issue_sign_in_link()
        assert refused.value.messages == [SIGN_IN_LINK_COOLDOWN_MESSAGE]
        SignInLink._base_manager.filter(user=target).update(created_at=timezone.now() - timedelta(seconds=61))
        assert sent(target, iam_admin)
    assert len(_mails(target)) == 2


@pytest.mark.parametrize(
    "overrides,deliverable_now",
    [
        pytest.param({}, True, id="all-present"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": ""}, False, id="no-origin"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "http://app.example.test"}, False, id="http-origin"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "http://app.example.test", "DEBUG": True}, True, id="http-under-debug"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "https://user:secret@app.example.test"}, False, id="origin-userinfo"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "https://app.example.test:notaport"}, False, id="origin-bad-port"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "https://app.example.test/"}, True, id="origin-trailing-slash"),
        pytest.param({"ANGEE_PUBLIC_ORIGIN": "https://*.example.test"}, False, id="origin-wildcard"),
        pytest.param({"MAILERS": {}}, False, id="no-transport"),
        # A default mailer is not a transport: messaging records Django's mail and sends none of it itself.
        pytest.param({"MAILERS": {"default": LOCMEM}}, False, id="a-default-mailer-alone"),
        pytest.param({"DEFAULT_FROM_EMAIL": "not an address"}, False, id="sender-not-an-address"),
        pytest.param(
            {"DEFAULT_FROM_EMAIL": "Angee <nöreply@example.test>"}, False, id="sender-the-transport-cannot-carry",
        ),
        pytest.param({"DEFAULT_FROM_EMAIL": "Sign-in <signin@example.test>"}, True, id="sender-with-a-name"),
    ],
)
def test_links_are_offered_with_an_https_origin_and_system_mail_configured(overrides, deliverable_now):
    """From settings alone: the origin a link opens, and messaging's ``system_mail_configured``.

    The test has no database, so a read of any row would fail it.
    """

    with override_settings(**{**DELIVERABLE, **overrides}):
        assert SignInLink.deliverable() is deliverable_now


ORIGINS = [
    ("https://app.example.test", "https://app.example.test"),
    ("https://app.example.test/", "https://app.example.test"),
    ("https://app.example.test.", "https://app.example.test."),
    ("https://app.example.test:8443", "https://app.example.test:8443"),
    ("https://localhost", "https://localhost"),
    ("https://10.0.0.1", "https://10.0.0.1"),
    ("https://[::1]:8000", "https://[::1]:8000"),
    ("https://bücher.example", "https://xn--bcher-kva.example"),
    ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
]
"""``(ANGEE_PUBLIC_ORIGIN, the origin a mail prints)`` for origins base accepts with ``DEBUG`` on and off: a name, a
trailing slash and dot, a port, a single label, an address of each family, an international name (printed in its
ASCII form)."""
DEBUG_ORIGINS = [
    ("http://localhost:6173", "http://localhost:6173"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("http://app.example.test", "http://app.example.test"),
]
"""The same for origins base accepts only under ``DEBUG``."""


@pytest.mark.parametrize(("origin", "printed", "debug"), [
    *(pytest.param(origin, printed, debug, id=f"{origin}-debug-{'on' if debug else 'off'}")
      for origin, printed in ORIGINS for debug in (False, True)),
    *(pytest.param(origin, printed, True, id=f"{origin}-debug-on") for origin, printed in DEBUG_ORIGINS),
])
def test_every_public_origin_base_accepts_gives_a_link_the_mail_can_carry(origin, printed, debug):
    """The template's link rule admits the deployment's own origin whatever its host, so the render's
    ``VALUE_NOT_LINKABLE`` cannot come from a link ``url_for`` builds (were it to, the action answers it as any
    refusal of the render, ``link_not_rendered``). If base's accessor and the rule ever part, this fails. No
    database."""

    token = f"usr_probe.{SECRET}"
    with override_settings(ANGEE_PUBLIC_ORIGIN=origin, DEBUG=debug):
        linked = MessageTemplate.objects.clean_link(SignInLink.url_for(token))
    assert linked == f"{printed}{SIGN_IN_LINK_PATH}#token={token}"


def test_links_open_the_route_this_addons_web_fragment_declares():
    manifest = Path(angee.iam_messaging.__file__).with_name("web") / "src" / "index.ts"
    assert f'const SIGN_IN_LINK_PATH = "{SIGN_IN_LINK_PATH}";' in manifest.read_text()


@override_settings(AUTHENTICATION_BACKENDS=BACKENDS)
def test_a_batched_request_shares_the_one_sign_in_attempt(deliverable, iam_admin, sent):
    owner = _person("guest-batch", password="batch-secret-pw")
    token = sent(owner, iam_admin)
    request = _at("203.0.113.52", session=True)  # A batch executes every operation on one request.

    first = execute_schema(_schema("public"), LOGIN, {"username": "guest-batch", "password": "wrong"}, request=request)
    second = execute_schema(_schema("public"), REDEEM, {"token": token}, request=request)

    assert result_data(first)["login"] == {"ok": False}
    assert [error.message for error in second.errors or ()] == [ONE_ATTEMPT_MESSAGE]
    assert SignInLink._base_manager.get(user=owner).used_at is None


@override_settings(
    AUTHENTICATION_BACKENDS=BACKENDS,
    AXES_FAILURE_LIMIT=2,
    AXES_LOCKOUT_PARAMETERS=["username"],
    AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT=False,
)
def test_a_spent_link_mark_never_opens_another_locked_account(deliverable, iam_admin, sent):
    _person("guest-locked", password="locked-secret-pw")
    holder = _person("guest-holder")
    spent = sent(holder, iam_admin)
    assert result_data(
        execute_schema(_schema("public"), REDEEM, {"token": spent}, request=_at("203.0.113.60", session=True))
    )
    for attempt in range(2):
        assert authenticate(_at("203.0.113.61"), username="guest-locked", password=f"bad-{attempt}") is None

    # Through GraphQL the interleaving is one request's three attempts: only the first runs.
    document = (
        'mutation($t: String!) { l: login(username: "guest-locked", password: "nope") { ok } '
        "s: redeem_sign_in_link(token: $t) { ok } "
        'hit: login(username: "guest-locked", password: "locked-secret-pw") { ok } }'
    )
    result = execute_schema(_schema("public"), document, {"t": spent}, request=_at("203.0.113.63", session=True))
    assert result.data is None
    assert _refusals(result) == {"s": ONE_ATTEMPT_MESSAGE}

    # Below the guard, a mark the lockout path left unread exempts only its own account.
    request = _at("203.0.113.64")
    assert authenticate(request, username="guest-locked", password="nope") is None
    assert request.axes_locked_out is True
    assert authenticate(request, **sign_in_credentials(spent)) is None
    assert vars(request)[SPENT_MARK] == holder.public_id
    assert authenticate(request, username="guest-locked", password="locked-secret-pw") is None


@override_settings(AXES_ENABLED=False)
def test_the_spent_mark_ends_with_its_attempt_even_when_it_raises(deliverable, iam_admin, sent):
    holder = _person("guest-raising")
    spent = sent(holder, iam_admin)
    assert result_data(
        execute_schema(_schema("public"), REDEEM, {"token": spent}, request=_at("203.0.113.65", session=True))
    )
    quiet = _at("203.0.113.66", session=True)
    assert (
        result_data(execute_schema(_schema("public"), REDEEM, {"token": spent}, request=quiet))["redeem_sign_in_link"][
            "ok"
        ]
        is False
    )
    assert SPENT_MARK not in vars(quiet)

    marked: list[str | None] = []

    def fail(sender: Any, request: Any = None, **kwargs: Any) -> None:
        marked.append(vars(request).get(SPENT_MARK))
        raise RuntimeError("receiver failed")

    raising = _at("203.0.113.67", session=True)
    user_login_failed.connect(fail, weak=False)
    try:
        result = execute_schema(_schema("public"), REDEEM, {"token": spent}, request=raising)
    finally:
        user_login_failed.disconnect(fail)
    assert result.errors
    # The link backend marked the attempt while it ran, and the mark ended with it.
    assert marked == [holder.public_id]
    assert SPENT_MARK not in vars(raising)


@override_settings(AUTHENTICATION_BACKENDS=BACKENDS, AXES_FAILURE_LIMIT=3, AXES_LOCKOUT_PARAMETERS=["username"])
def test_link_failures_stay_under_the_account_public_id_after_a_sign_in(deliverable, iam_admin, sent):
    target = _person("guest-keyed")
    token = sent(target, iam_admin)
    assert authenticate(_at("203.0.113.70"), **sign_in_credentials(f"{target.public_id}.guess")) is None

    signed_in = execute_schema(_schema("public"), REDEEM, {"token": token}, request=_at("203.0.113.70", session=True))

    assert result_data(signed_in)["redeem_sign_in_link"]["ok"] is True
    assert AccessAttempt.objects.get(username=target.public_id).failures_since_start == 1


_ISSUER = {
    "platform admin": lambda: create_platform_admin("decision-admin", password=None),
    "people manager": lambda: _manager("decision-manager"),
    "member": lambda: User.objects.create_user("decision-member", "decision-member@example.com"),
}
_TARGET = {
    "active person": lambda issuer: _person("decision-person"),
    "own account": lambda issuer: User._base_manager.get(pk=issuer.pk),
    "staff": lambda issuer: _person("decision-staff", is_staff=True),
    "superuser": lambda issuer: _person("decision-super", is_superuser=True),
    "another platform admin": lambda issuer: create_platform_admin("decision-peer", password=None),
    "inactive": lambda issuer: _person("decision-inactive", is_active=False),
    "service": lambda issuer: User.objects.create_user(
        "decision-service", "decision-service@example.com", kind="service",
    ),
}
_SENDS = "sends"


@pytest.mark.parametrize(("issuer", "target", "outcome"), [
    pytest.param("platform admin", "active person", _SENDS, id="admin-sends-to-an-active-person"),
    pytest.param("platform admin", "own account", "protected", id="never-to-own-account"),
    pytest.param("platform admin", "staff", "protected", id="never-to-staff"),
    pytest.param("platform admin", "superuser", "protected", id="never-to-a-superuser"),
    pytest.param("platform admin", "another platform admin", "protected", id="never-to-a-protected-account"),
    pytest.param("platform admin", "inactive", "Only active accounts", id="never-to-an-inactive-account"),
    pytest.param("platform admin", "service", "Only person accounts", id="never-to-a-service-account"),
    pytest.param("people manager", "active person", PermissionDenied, id="a-people-manager-may-not"),
    pytest.param("member", "active person", PermissionDenied, id="a-member-may-not"),
])
def test_who_may_send_a_link_to_whom(deliverable, db, issuer, target, outcome, django_capture_on_commit_callbacks):
    """The users list offers the link exactly where the action sends it, and a refused issue sends nothing."""

    actor = _ISSUER[issuer]()
    account = _TARGET[target](actor)
    assert (ISSUE_SIGN_IN_LINK in account.account_actions_for(to_subject_ref(actor))) is (outcome == _SENDS)
    with django_capture_on_commit_callbacks(execute=True), actor_context(actor):
        if outcome == _SENDS:
            account.with_actor(actor).issue_sign_in_link()
        elif outcome is PermissionDenied:
            with pytest.raises(PermissionDenied, match=ISSUE_SIGN_IN_LINK):
                account.with_actor(actor).issue_sign_in_link()
        else:
            with pytest.raises(ValidationError, match=outcome):
                account.with_actor(actor).issue_sign_in_link()
    assert len(_mails(account)) == SignInLink._base_manager.filter(user=account).count() == (outcome == _SENDS)


def test_a_link_session_records_iams_session_backend_and_outlives_the_link_backend(deliverable, iam_admin, sent):
    target = _person("guest-session-backend")
    token = sent(target, iam_admin)
    request = _request()

    redeemed = execute_schema(_schema("public"), REDEEM, {"token": token}, request=request)

    assert result_data(redeemed)["redeem_sign_in_link"]["ok"] is True
    assert request.session[BACKEND_SESSION_KEY] == "angee.iam.auth.ModelBackend"
    # With the link backend gone from the deployment, the session still loads its person.
    with override_settings(AUTHENTICATION_BACKENDS=["angee.iam.auth.ModelBackend"]):
        assert get_user(request) == target


def test_iam_alone_declares_no_sign_in_link_permission(tmp_path: Path) -> None:
    """IAM's own Zed names no link action; this addon's fragment adds it and unions it into the disclosures."""

    declaration = Path(apps.get_app_config("iam").path, "permissions.zed").read_text(encoding="utf-8")
    owned = parse_zed(declaration).get_definition("auth/user")
    assert owned is not None and ISSUE_SIGN_IN_LINK not in {permission.name for permission in owned.permissions}
    assert ISSUE_SIGN_IN_LINK not in declaration
    fragment = Path(apps.get_app_config("iam_messaging").path, "permissions.extends.zed").read_text(encoding="utf-8")
    iam = _addon(tmp_path, "angee.iam", "permissions.zed", declaration)
    merged = merged_schemas([iam, _contrib_addon(tmp_path, fragment, name="angee.iam_messaging")])
    composed = merged["angee.iam"].get_definition("auth/user")
    assert composed is not None
    names = {permission.name: permission.raw_text or str(permission.expression) for permission in composed.permissions}
    assert ISSUE_SIGN_IN_LINK in names
    assert all(ISSUE_SIGN_IN_LINK in names[point] for point in ("active_account_granted", "nonstaff_account_granted"))


def test_setting_a_password_retires_outstanding_links(deliverable, iam_admin, sent):
    target = _person("guest-new-password", password="Old-password-unique-731!")
    token = sent(target, iam_admin)
    with actor_context(iam_admin):
        revision = User._base_manager.get(pk=target.pk).account_revision
        target.with_actor(iam_admin).set_account_password("New-password-unique-942!", expected_revision=revision)

    assert _redeem(token) is None
    assert SignInLink._base_manager.get(user=target).used_at is None


# ---------- the link's mail ----------


def test_the_mail_is_the_shipped_template_with_one_link_that_signs_in_once(deliverable, iam_admin, sent):
    """Recorded on the system channel and queued, the link is current; delivered, it carries the link once."""

    target = _person("guest-mail", first_name="Ada")
    token = sent(target, iam_admin)
    assert SignInLink._base_manager.get(user=target).sent_at is not None
    [message] = _mails(target)
    assert _deliver(message)["delivered"]
    [wire] = mail.outbox
    url = SignInLink.url_for(token)
    assert (wire.subject, wire.from_email, wire.to) == ("Your sign-in link", SENDER, [target.email])
    assert _hrefs(_html(wire)) == [url]
    assert wire.body.count(url) == 1 and f"Sign in: {url}" in wire.body and "Hi Ada," in wire.body
    assert _redeem(token) == target
    assert _redeem(token) is None


def test_a_person_who_reads_spanish_gets_the_spanish_row_and_an_override_changes_the_mail(
    deliverable, iam_admin, sent,
):
    target = _person("guest-es", first_name="Lucía")
    with system_context(reason="tests.iam_messaging.sign_in_link.person"):
        Person._base_manager.create(display_name="Lucía", user=target, languages=["es"], created_by=target)
    token = sent(target, iam_admin)
    written = _written(target)
    assert written.subject == "Tu enlace para iniciar sesión"
    assert "Hola Lucía," in written.body and f"Iniciar sesión: {SignInLink.url_for(token)}" in written.body
    assert '<html lang="es" dir="ltr"' in _html(written)

    with system_context(reason="tests.iam_messaging.sign_in_link.override"):
        baseline = MessageTemplate._base_manager.get(kind="template", key=SIGN_IN_LINK_TEMPLATE, language="es")
        override = MessageTemplate.objects.override(baseline)
        override.subject = "Entra en tu cuenta"
        override.save()
    sent(target, iam_admin)
    assert _written(target).subject == "Entra en tu cuenta"


@pytest.mark.parametrize("language", ["en", "es"])
def test_each_shipped_row_previews_its_sample_with_no_warning(deliverable, language):
    with system_context(reason="tests.iam_messaging.sign_in_link.preview"):
        row = MessageTemplate.objects.get(kind="template", key=SIGN_IN_LINK_TEMPLATE, language=language)
        preview = row.preview()
    assert preview.rendered is not None and preview.warnings == []
    assert _hrefs(preview.rendered.html) == ["https://app.example.test/sign-in-link#token=usr_sample.not-a-real-secret"]


def test_a_first_name_that_spells_markup_stays_text(deliverable, iam_admin, sent):
    name = '<a href="https://x.example">x</a>'
    target = _person("guest-markup", first_name=name)
    token = sent(target, iam_admin)
    written = _written(target)
    assert _hrefs(_html(written)) == [SignInLink.url_for(token)]
    assert f"Hi {name}," in written.body


@pytest.mark.parametrize("ambient", [
    pytest.param(False, id="an-actor-pinned-with-no-ambient-actor"),
    pytest.param(True, id="an-actor-pinned-under-another-administrators-context"),
])
def test_a_link_and_its_mail_record_the_one_actor_the_issue_was_given(
    deliverable, iam_admin, django_capture_on_commit_callbacks, ambient,
):
    """The link's issuer is the account's pinned actor; its mail is caused by the same one, whatever is ambient."""

    target = _person("guest-caused")
    other = create_platform_admin("other-admin", password=None)
    with django_capture_on_commit_callbacks(execute=True), actor_context(other) if ambient else nullcontext():
        target.with_actor(iam_admin).issue_sign_in_link()
    [message] = _mails(target)
    with system_context(reason=READ):
        thread_creator = Message._base_manager.select_related("thread").get(pk=message.pk).thread.created_by_id
    assert SignInLink._base_manager.get(user=target).created_by_id == iam_admin.pk
    assert (message.created_by_id, thread_creator) == (iam_admin.pk, iam_admin.pk)


def test_the_link_does_not_pass_through_djangos_default_mailer(deliverable, iam_admin, sent):
    target = _person("guest-default-mailer")
    mailers = {**DELIVERABLE["MAILERS"], "default": {"BACKEND": f"{__name__}.RefusingDefaultMailer"}}
    with override_settings(MAILERS=mailers):
        token = sent(target, iam_admin)
    assert len(_mails(target)) == 1 and mail.outbox == []
    assert _redeem(token) == target


def test_deleting_the_mail_leaves_the_link_working_and_only_a_newer_link_retires_it(deliverable, iam_admin, sent):
    target = _person("guest-deleted-mail")
    first = sent(target, iam_admin)
    second = sent(target, iam_admin)
    with system_context(reason="tests.iam_messaging.sign_in_link.delete"):
        Message._base_manager.filter(pk__in=[message.pk for message in _mails(target)]).delete()
    assert _mails(target) == []
    assert _redeem(first) is None
    assert _redeem(second) == target


def test_a_link_whose_insert_fails_after_its_mail_leaves_no_mail_and_nothing_queued(
    deliverable, django_capture_on_commit_callbacks,
):
    """``issue`` is atomic itself: a caller outside the action's transaction never gets a mail without its link."""

    target = _person("guest-unsaved")

    def refuse(sender: Any, instance: Any, **kwargs: Any) -> None:
        raise IntegrityError("the link's insert failed")

    pre_save.connect(refuse, sender=SignInLink, dispatch_uid="tests.iam_messaging.refuse_link")
    try:
        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(IntegrityError, match="insert failed"),
        ):
            SignInLink.objects.issue(target, target.email, actor=None)
    finally:
        pre_save.disconnect(sender=SignInLink, dispatch_uid="tests.iam_messaging.refuse_link")
    assert callbacks == [] and _mails(target) == []
    with system_context(reason=READ):
        assert not SignInLink._base_manager.filter(user=target).exists()


# ---------- an address's spelling ----------


def _handles(address: str) -> list[str]:
    with system_context(reason=READ):
        return list(Handle._base_manager.filter(value__iexact=address).values_list("value", flat=True))


def test_an_account_whose_address_a_handle_spells_otherwise_is_mailed_at_the_handles_spelling(
    deliverable, iam_admin, sent,
):
    """The account stores its address lower case; the handle owner lands it on the handle as first spelled."""

    with system_context(reason="tests.iam_messaging.sign_in_link.handle"):
        Handle.objects.upsert(platform=Handle.Platform.EMAIL, value="Guest.Case@Example.COM")
    target = _person("guest.case")
    assert target.email == "guest.case@example.com"
    token = sent(target, iam_admin)
    assert _handles(target.email) == ["Guest.Case@Example.COM"]
    assert _written(target).to == ["Guest.Case@Example.COM"]
    assert _redeem(token) == target


def test_two_accounts_whose_addresses_differ_only_in_case_share_one_handle_and_keep_their_own_links(
    deliverable, iam_admin, sent,
):
    """Only a write past the user model's lower-casing (``update()``) stores a second spelling; the person-email
    constraint compares stored values, so both accounts exist. Pinned, not decided: which key an account's address
    and a handle share is the architect's question."""

    first = _person("guest-twin")
    second = _person("guest-twin-two")
    with system_context(reason="tests.iam_messaging.sign_in_link.spelling"):
        User._base_manager.filter(pk=second.pk).update(email="Guest-Twin@Example.com")
    second.refresh_from_db()
    first_token, second_token = sent(first, iam_admin), sent(second, iam_admin)
    assert _handles(first.email) == ["guest-twin@example.com"]
    assert [message.pk for message in _mails(first)] == [message.pk for message in _mails(second)]
    assert _written(second).to == ["guest-twin@example.com"]
    assert _redeem(second_token) == second
    assert _redeem(first_token) == first


# ---------- what is refused, before anything is written ----------


def _override(kind: str, key: str, **changes: Any) -> Any:
    """The deployment's override of a shipped English row, saved with ``changes``, as an administrator edits one."""

    with system_context(reason="tests.iam_messaging.sign_in_link.override"):
        baseline = MessageTemplate._base_manager.get(kind=kind, key=key, language="en", is_baseline=True)
        override = MessageTemplate.objects.override(baseline)
        for name, value in changes.items():
            setattr(override, name, value)
        override.full_clean()
        override.save()
    return override


GUARDED = """{% include "parts/greeting" with person=person only %}

Use the link below to sign in to {{ brand.name }}.

{% if person.first_name %}

{% include "blocks/button" with label="Sign in" url=link only %}

{% endif %}"""
"""The shipped text with the button behind a condition that is false for a person with no first name."""


@pytest.mark.parametrize("override", [
    pytest.param(lambda: _override("template", SIGN_IN_LINK_TEMPLATE, source=GUARDED),
                 id="the-link-under-a-false-condition"),
    pytest.param(lambda: _override("block", "button", text_source="{{ label }}"),
                 id="the-link-dropped-from-the-text-body"),
])
def test_a_template_that_does_not_show_the_link_is_refused_as_the_deployments_fault(
    deliverable, iam_admin, sent, monkeypatch, caplog, django_capture_on_commit_callbacks, override,
):
    """Offered, since only the send can tell; refused in band, nothing written, the earlier link still current."""

    target = _person("guest-hidden")  # no first name
    earlier = sent(target, iam_admin)
    override()
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    with system_context(reason=READ):
        links = list(SignInLink._base_manager.values_list("pk", flat=True))
        mails = list(Message._base_manager.values_list("pk", flat=True))
    with caplog.at_level(logging.DEBUG):
        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            actor_context(iam_admin),
            _refused(ValidationError) as refusal,
        ):
            target.with_actor(iam_admin).issue_sign_in_link()
        answered = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert (refusal.value.code, refusal.value.messages) == ("link_not_shown", [SIGN_IN_LINK_NOT_SHOWN_MESSAGE])
    assert refusal.value.__suppress_context__ and refusal.value.__cause__ is None
    warnings = [record.getMessage() for record in caplog.records if record.name == "angee.iam_messaging.models"
                and record.levelno == logging.WARNING]
    with system_context(reason=READ):
        row = MessageTemplate.objects.effective("template", SIGN_IN_LINK_TEMPLATE, person=None)
    assert warnings == 2 * [  # the verb's refusal, then the mutation's
        f"Refused a sign-in link: the message template {row.handler.template_name(row.key)} ({row.language}), "
        f"row {row.public_id}, or a block or part it includes, does not show the link ({VALUE_NOT_PRINTED})."
    ]
    assert callbacks == []
    assert result_data(answered)["issue_user_sign_in_link"] == {"ok": False, "message": SIGN_IN_LINK_NOT_SHOWN_MESSAGE}
    assert not any(SECRET in text for text in (caplog.text, repr(refusal.value), str(answered.errors)))
    with system_context(reason=READ):
        assert list(SignInLink._base_manager.values_list("pk", flat=True)) == links
        assert list(Message._base_manager.values_list("pk", flat=True)) == mails
    assert _redeem(earlier) == target


DATED = '{{ link|date:"c" }}'
"""The link read as a date: the console admits it, and the render refuses the link's value (``INVALID_VALUES``) in
words that quote its first characters."""


@pytest.mark.parametrize("override", [
    pytest.param(lambda shipped: {"source": f"{shipped.source}\n\n{DATED}"}, id="in-the-body"),
    pytest.param(lambda shipped: {"subject": f"{shipped.subject} {DATED}"}, id="in-the-subject"),
    pytest.param(lambda shipped: {"source": f"{shipped.source}\n\n{{% with dated=link %}}{{{{ dated|date:\"c\" }}}}"
                                            "{% endwith %}"}, id="inside-a-with-block"),
    pytest.param(lambda shipped: {"source": f'{shipped.source}\n\n{{% include "blocks/button" with label="Again" '
                                            'url=link|date:"c" only %}'}, id="as-a-buttons-url"),
])
def test_a_template_the_render_refuses_is_answered_in_the_actions_words_and_logged_by_its_code(
    deliverable, iam_admin, sent, monkeypatch, caplog, django_capture_on_commit_callbacks, override,
):
    """The render's own words can quote the values it was given, the link among them (the date check quotes a value's
    first 40 characters), so the action answers every refusal of the render in its own words, raised from nothing,
    and logs the row and the refusal's code: no character of the link reaches the answer or the log."""

    target = _person("guest-dated")
    earlier = sent(target, iam_admin)
    SignInLink._base_manager.filter(user=target).update(created_at=timezone.now() - timedelta(seconds=61))
    with system_context(reason=READ):
        shipped = MessageTemplate._base_manager.get(
            kind="template", key=SIGN_IN_LINK_TEMPLATE, language="en", is_baseline=True,
        )
    row = _override("template", SIGN_IN_LINK_TEMPLATE, **override(shipped))
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    with caplog.at_level(logging.DEBUG):
        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            actor_context(iam_admin),
            _refused(ValidationError) as refusal,
        ):
            target.with_actor(iam_admin).issue_sign_in_link()
        answered = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert (refusal.value.code, refusal.value.messages) == ("link_not_rendered", [SIGN_IN_LINK_NOT_RENDERED_MESSAGE])
    assert refusal.value.__suppress_context__ and refusal.value.__cause__ is None
    assert result_data(answered)["issue_user_sign_in_link"] == {
        "ok": False, "message": SIGN_IN_LINK_NOT_RENDERED_MESSAGE,
    }
    warnings = [record.getMessage() for record in caplog.records if record.name == "angee.iam_messaging.models"
                and record.levelno == logging.WARNING]
    assert warnings == 2 * [  # the verb's refusal, then the mutation's
        f"Refused a sign-in link: the message template {row.handler.template_name(row.key)} ({row.language}), "
        f"row {row.public_id}, or a block or part it includes, could not be rendered for it ({INVALID_VALUES})."
    ]
    shown = [text for text in (caplog.text, repr(refusal.value), str(answered.data), str(answered.errors))
             if SIGN_IN_LINK_PATH in text or target.public_id in text or SECRET in text]
    assert shown == [] and callbacks == []
    assert _redeem(earlier) == target
    with system_context(reason="tests.iam_messaging.sign_in_link.override"):
        row.delete()
    with override_settings(ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN=60):  # the refused issue started no cooldown
        assert sent(target, iam_admin)


def _broken(state: str, channel: Any) -> None:
    """Take away what ``state`` names, as a deployment that skipped or undid part of provision does."""

    with system_context(reason="tests.iam_messaging.sign_in_link.break"):
        if state == "system channel missing":
            channel.delete()
        elif state == "system channel paused":
            channel.pause()
        elif state == "template missing":
            for row in MessageTemplate._base_manager.filter(key=SIGN_IN_LINK_TEMPLATE):
                row.delete()
        elif state == "no default brand kit":
            BrandKit.objects.filter(is_default=True).update(is_default=False)


@pytest.mark.parametrize(("state", "key", "code"), [
    pytest.param("system channel missing", "channel", "SYSTEM_CHANNEL_MISSING", id="system-channel-missing"),
    pytest.param("system channel paused", "channel", "OUTBOUND_EMAIL_CHANNEL_MISSING", id="system-channel-paused"),
    pytest.param("template missing", None, "template_missing", id="template-missing"),
    # The render's refusal for want of a default brand kit, the deployment's to fix, in words naming that cause.
    pytest.param("no default brand kit", None, "brand_kit_missing", id="no-default-brand-kit"),
    pytest.param("cooldown", None, None, id="within-the-cooldown"),
])
def test_a_refused_issue_writes_nothing_names_no_token_and_leaves_the_earlier_link_current(
    deliverable, iam_admin, sent, monkeypatch, caplog, django_capture_on_commit_callbacks, state, key, code,
):
    """Offered, since settings cannot tell these, and refused in band at the verb and at the mutation."""

    target = _person("guest-refused")
    earlier = sent(target, iam_admin)
    _broken(state, deliverable)
    with system_context(reason=READ):
        links = list(SignInLink._base_manager.values_list("pk", flat=True))
        mails = list(Message._base_manager.values_list("pk", flat=True))
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    cooldown = {"ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": 60 if state == "cooldown" else 0}
    assert ISSUE_SIGN_IN_LINK in target.account_actions_for(to_subject_ref(iam_admin))
    with (
        override_settings(**cooldown),
        caplog.at_level(logging.DEBUG),
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        actor_context(iam_admin),
        _refused(ValidationError, key=key, code=code) as refusal,
    ):
        target.with_actor(iam_admin).issue_sign_in_link()
    with override_settings(**cooldown), django_capture_on_commit_callbacks(execute=True) as answered:
        result = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    if state == "template missing":
        assert (refusal.value.code, refusal.value.messages) == (code, [SIGN_IN_LINK_TEMPLATE_MISSING_MESSAGE])
        assert refusal.value.__suppress_context__ and refusal.value.__cause__ is None
    if state == "no default brand kit":
        assert (refusal.value.code, refusal.value.messages) == (code, [SIGN_IN_LINK_NO_BRAND_KIT_MESSAGE])
        assert refusal.value.__suppress_context__ and refusal.value.__cause__ is None
        assert result_data(result)["issue_user_sign_in_link"]["message"] == SIGN_IN_LINK_NO_BRAND_KIT_MESSAGE
        assert "Refused a sign-in link" not in caplog.text
    if state == "cooldown":
        assert refusal.value.messages == [SIGN_IN_LINK_COOLDOWN_MESSAGE]
    assert result_data(result)["issue_user_sign_in_link"]["ok"] is False
    assert callbacks == answered == []
    with system_context(reason=READ):
        assert list(SignInLink._base_manager.values_list("pk", flat=True)) == links
        assert list(Message._base_manager.values_list("pk", flat=True)) == mails
    assert SECRET not in f"{refusal.value!r} {refusal.value.messages} {result.data} {result.errors}"
    assert SECRET not in caplog.text
    assert _cached_holding(SECRET) == []
    assert _redeem(earlier) == target


# ---------- where the token is, and where it never is ----------


def _held(root: Any, *, depth: int = 8) -> Iterator[str]:
    """Every text ``root`` holds, walked to ``depth``: a partial's function, arguments and keywords, a bound method's
    object, a function's closure cells and defaults, an object's ``__dict__`` and ``__slots__``, a container's items.

    Classes, modules and a function's globals are not walked: they hold code, not what a call was given.
    """

    seen: set[int] = set()
    pending: list[tuple[Any, int]] = [(root, 0)]
    while pending:
        value, level = pending.pop()
        if id(value) in seen or inspect.isclass(value) or inspect.ismodule(value):
            continue
        seen.add(id(value))
        if isinstance(value, str):
            yield value
            continue
        if isinstance(value, bytes | bytearray):
            yield bytes(value).decode(errors="replace")
            continue
        if level == depth:
            continue
        children: list[Any] = []
        if isinstance(value, functools.partial):
            children += [value.func, value.args, value.keywords]
        if inspect.isroutine(value):  # a bound method's object, a builtin's too
            children += [getattr(value, "__self__", None), getattr(value, "__func__", None)]
        if inspect.isfunction(value):
            for cell in value.__closure__ or ():
                try:
                    children.append(cell.cell_contents)
                except ValueError:  # an empty cell
                    pass
            children += [value.__defaults__, value.__kwdefaults__]
        if isinstance(value, dict):
            children += [*value.keys(), *value.values()]
        elif isinstance(value, list | tuple | set | frozenset):
            children += list(value)
        if not inspect.isroutine(value):
            children += list(getattr(value, "__dict__", {}).values())
            slots = (name for kind in type(value).__mro__ for name in getattr(kind, "__slots__", ()))
            children += [getattr(value, name) for name in slots if isinstance(name, str) and hasattr(value, name)]
        pending.extend((child, level + 1) for child in children)


def _cached_holding(text: str) -> list[str]:
    """The aliases of the configured caches one of whose entries holds ``text``, its key or its stored value.

    Django's cache API lists no keys, so the scan reads the store of the local-memory backend every cache of the
    test host is, and refuses any other backend rather than pass it unread.
    """

    found = []
    for alias in django_settings.CACHES:
        cache = caches[alias]
        assert isinstance(cache, LocMemCache), f"The cache {alias!r} is not one this scan can read."
        entries = list(cache._cache.items())
        if any(text in piece for key, stored in entries for piece in (key, *_held(pickle.loads(stored)))):
            found.append(alias)
    return found


def _tables_holding(text: str) -> set[str]:
    """Every database table one of whose rows holds ``text`` in a column, read as stored."""

    found = set()
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
            if any(text in str(value) for row in cursor.fetchall() for value in row):
                found.add(table)
    return found


def test_the_token_is_stored_only_in_its_mail_never_logged_and_held_by_no_callback(
    deliverable, iam_admin, monkeypatch, caplog, django_capture_on_commit_callbacks,
):
    target = _person("guest-stored")
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    with (
        caplog.at_level(logging.DEBUG),
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        actor_context(iam_admin),
    ):
        target.with_actor(iam_admin).issue_sign_in_link()
    assert callbacks and not any(SECRET in piece for callback in callbacks for piece in _held(callback))
    assert _cached_holding(SECRET) == []
    [message] = _mails(target)
    # The message's preview, and the text fragments its text and HTML parts point to; the link row keeps a digest.
    stored = {Message._meta.db_table, Fragment._meta.db_table}
    assert _tables_holding(SECRET) == stored
    with system_context(reason=READ):
        assert SignInLink.url_for(f"{target.public_id}.{SECRET}") in Message._base_manager.get(pk=message.pk).preview
    assert _deliver(message)["delivered"]
    assert _tables_holding(SECRET) == stored
    assert _cached_holding(SECRET) == []
    with system_context(reason="tests.iam_messaging.sign_in_link.delete"):
        Message._base_manager.filter(pk=message.pk).delete()
    assert _tables_holding(SECRET) == {Fragment._meta.db_table}
    assert SECRET not in caplog.text


# ---------- error reports ----------


def _chain(error: BaseException | None) -> list[BaseException]:
    """``error`` and every exception a report of it chains, as Django's reporter follows them."""

    chained = []
    while error is not None:
        chained.append(error)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return chained


def _reported(error: BaseException, *, below: str | None = None, address: str = "") -> tuple[list[Any], list[str]]:
    """What Django's report of ``error`` shows of the link: each ``(function, variable)`` holding the sentinel secret
    (from ``below`` down), and the messages of the chained exceptions that carry the secret, a link or ``address``."""

    shown = _reported_secret(error, below=below, needle=SECRET)
    needles = [SECRET, SIGN_IN_LINK_PATH + "#", *([address] if address else [])]
    carried = [type(item).__name__ for item in _chain(error) if any(needle in str(item) for needle in needles)]
    return shown, carried


def _link_insert(sql: str, params: Any) -> bool:
    return sql.startswith(f'INSERT INTO "{SignInLink._meta.db_table}"')


def _link_read(sql: str, params: Any) -> bool:
    return f'FROM "{SignInLink._meta.db_table}"' in sql and not sql.startswith(("INSERT", "UPDATE", "DELETE"))


@pytest.mark.parametrize("depth", [
    "before-the-render", "in-the-renders-refusal", "inside-the-write", "the-links-own-insert",
])
def test_an_error_report_of_a_failing_issue_shows_the_token_in_no_frame(
    deliverable, iam_admin, settings, monkeypatch, depth,
):
    """With ``DEBUG`` off, as a deployment's error mail runs: each depth of the send, the token minted by then, and
    the account's address from the action's own frame down."""

    settings.DEBUG = False
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    target = _person("guest-reported")
    failing: Any = nullcontext()
    if depth == "before-the-render":
        def no_origin(sender: Any, instance: Any, **kwargs: Any) -> None:
            settings.ANGEE_PUBLIC_ORIGIN = ""  # the origin goes between the offer and the link's URL

        post_init.connect(no_origin, sender=MessageTemplate, dispatch_uid="tests.iam_messaging.no_origin")
    elif depth == "in-the-renders-refusal":
        _override("template", SIGN_IN_LINK_TEMPLATE, source=GUARDED)
    else:
        failing = _failing(_message_insert if depth == "inside-the-write" else _link_insert)
    try:
        with failing, actor_context(iam_admin), pytest.raises(Exception) as failed:
            target.with_actor(iam_admin).issue_sign_in_link()
    finally:
        post_init.disconnect(sender=MessageTemplate, dispatch_uid="tests.iam_messaging.no_origin")
    expected = {"before-the-render": PublicOriginUnavailable, "in-the-renders-refusal": ValidationError}
    assert isinstance(failed.value, expected.get(depth, DatabaseError))
    assert _reported(failed.value, address=target.email) == ([], [])
    assert _reported_secret(failed.value, below="issue_sign_in_link", needle=target.email) == []


def test_an_error_report_of_a_failing_delivery_shows_the_link_in_no_frame(
    deliverable, iam_admin, settings, monkeypatch, caplog, django_capture_on_commit_callbacks,
):
    """The worker's path: the transport fails, then recording it fails too. The recording's failure leaves the task
    alone, with nothing chained, the transport's error logged by its types alone, and no frame shows the link."""

    settings.DEBUG = False
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    target = _person("guest-delivery-report")
    with django_capture_on_commit_callbacks(execute=True), actor_context(iam_admin):
        target.with_actor(iam_admin).issue_sign_in_link()
    # Only the ids: this frame is in the report, and a message's text form is its preview.
    label, pk, key = next((row._meta.label_lower, row.pk, row.external_id) for row in _mails(target))
    failure = ConnectionError("The transport is down.")
    settings.MAILERS = {"transport": {"BACKEND": f"{FailingTransport.__module__}.FailingTransport",
                                      "OPTIONS": {"failure": failure}}}

    def recording(sql: str, params: Any) -> bool:
        return failure.__traceback__ is not None and sql.startswith(f'UPDATE "{Message._meta.db_table}" ')

    with _failing(recording), caplog.at_level(logging.DEBUG), pytest.raises(DatabaseError) as failed:
        deliver_message(label, pk, key)
    assert failure not in _chain(failed.value)
    assert [type(error) for error in _chained(failed.value)] == [DatabaseError]  # its suppressed context included
    assert _delivery_logged(caplog) == [
        "Recording a failed delivery failed (DatabaseError); the delivery had failed with "
        "DeliveryUncertain (ConnectionError).",
    ]
    assert _reported(failed.value, address=target.email) == ([], [])
    assert SECRET not in caplog.text


def _failing_digest(key_salt: str, value: str, **kwargs: Any) -> Any:
    raise RuntimeError("The digest failed.")


@pytest.mark.parametrize("depth", ["the-links-read", "the-digest"])
@pytest.mark.parametrize("entry", ["the-manager", "the-backend"])
def test_an_error_report_of_a_failing_redemption_shows_the_token_in_no_frame(
    deliverable, iam_admin, sent, settings, monkeypatch, entry, depth,
):
    """A read of the account's links fails, or the secret's digest does, whose own frame holds the account's pk and
    the secret: each entry that takes the token hides every local, so nothing from it down shows it (this test's own
    frame holds the token, so the report is read from the entry down)."""

    settings.DEBUG = False
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    target = _person("guest-redemption-report")
    sent(target, iam_admin)
    if depth == "the-digest":
        monkeypatch.setattr("angee.iam_messaging.models.salted_hmac", _failing_digest)
    failing = _failing(_link_read) if depth == "the-links-read" else nullcontext()
    with failing, pytest.raises(DatabaseError if depth == "the-links-read" else RuntimeError) as failed:
        if entry == "the-manager":
            SignInLink.objects.redeem(f"{target.public_id}.{SECRET}")
        else:
            authenticate(_at("198.51.100.80"), **sign_in_credentials(f"{target.public_id}.{SECRET}"))
    assert _reported(failed.value, below="redeem" if entry == "the-manager" else "authenticate") == ([], [])


@pytest.mark.parametrize("mutation", ["redeem_sign_in_link", "issue_user_sign_in_link"])
def test_an_unexpected_failure_through_a_mutation_is_logged_with_no_traceback_and_no_token(
    deliverable, iam_admin, sent, settings, monkeypatch, caplog, mutation,
):
    """No log record of a resolver's unexpected failure carries a traceback, so no report (an ``AdminEmailHandler``'s
    included) is built from one: Angee's schema logs the class, path and frames as text and detaches the exception
    before Strawberry logs the error. With the token a variable, no record and no answer holds it."""

    settings.DEBUG = False
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: SECRET)
    target = _person("guest-mutation-report")
    with caplog.at_level(logging.DEBUG):
        if mutation == "redeem_sign_in_link":
            sent(target, iam_admin)
            with _failing(_link_read):
                answered = execute_schema(_schema("public"), REDEEM, {"token": f"{target.public_id}.{SECRET}"},
                                          request=_request())
        else:
            with _failing(_link_insert):
                answered = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert [(error.message, error.extensions) for error in answered.errors or ()] == [
        ("An unexpected error occurred.", {"code": "INTERNAL"}),
    ]
    assert f"Unexpected GraphQL resolver error (DatabaseError) at path ['{mutation}']" in caplog.text
    assert [record.name for record in caplog.records if record.exc_info] == []
    shown = SECRET in caplog.text or SECRET in str(answered.data)
    assert not shown


# ---------- sent means recorded and queued ----------


def test_a_link_whose_delivery_fails_is_still_sent_and_replaces_the_link_that_worked(deliverable, iam_admin, sent):
    target = _person("guest-bounced")
    worked = sent(target, iam_admin)
    assert _deliver(_mails(target)[-1])["delivered"]
    with override_settings(MAILERS=_transport(RefusingTransport)):
        bounced = sent(target, iam_admin)
        assert _deliver(_mails(target)[-1])["status"] == "failed"
    assert None not in SignInLink._base_manager.filter(user=target).values_list("sent_at", flat=True)
    assert _redeem(worked) is None
    assert _redeem(bounced) == target


@pytest.mark.parametrize(("transport", "status"), [
    pytest.param(UnreachableTransport, "retry", id="a-failed-connection-is-retried"),
    pytest.param(DroppingTransport, "uncertain", id="a-delivery-that-drops-mid-send-is-not"),
])
def test_a_recorded_link_is_current_whether_its_delivery_is_retried_or_left_uncertain(
    deliverable, iam_admin, sent, caplog, transport, status,
):
    target = _person(f"guest-{status}")
    earlier = sent(target, iam_admin)
    with override_settings(MAILERS=_transport(transport)), caplog.at_level(logging.DEBUG):
        current = sent(target, iam_admin)
        message = _mails(target)[-1]
        assert _deliver(message)["status"] == status
    logged = current.partition(".")[2] in caplog.text
    assert not logged
    with system_context(reason="tests.iam_messaging.sign_in_link.due"):
        # Time passes: a retry falls due; an uncertain delivery is scheduled for nothing.
        Message._base_manager.filter(pk=message.pk, scheduled_at__isnull=False).update(
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )
    again = _deliver(message)
    assert again["delivered"] is (status == "retry")
    assert len(mail.outbox) == (status == "retry")
    with system_context(reason=READ):
        assert Message._base_manager.get(pk=message.pk).status == ("sent" if status == "retry" else "uncertain")
    assert _redeem(earlier) is None
    assert _redeem(current) == target
