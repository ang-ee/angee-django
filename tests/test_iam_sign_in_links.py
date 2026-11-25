"""Sign-in links: the account verb that emails one, its single-use redemption and the axes seam."""

import logging
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import SESSION_KEY, authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_login_failed
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.errors import MissingActorError

import angee.iam
from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from angee.iam.auth import ONE_ATTEMPT_MESSAGE, sign_in_credentials
from angee.iam.autoconfig import SETTINGS as IAM_SETTINGS
from angee.iam.events import sign_in_link_sent
from angee.iam.models import SIGN_IN_LINK_PATH, SIGN_IN_LINK_UNAVAILABLE_MESSAGE
from tests.conftest import create_platform_admin
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_models import SignInLink
from tests.test_iam_graphql import _schema
from tests.test_iam_login_throttle import LOGIN, _refusals

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

User = get_user_model()
ORIGIN = "https://app.example.test"
DELIVERABLE = {
    "MAILERS": {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    "ANGEE_PUBLIC_ORIGIN": ORIGIN,
    "ANGEE_IAM_SIGN_IN_LINK_COOLDOWN": 0,
}
ISSUE = "mutation($id: ID!) { issue_user_sign_in_link(id: $id) { ok message } }"
REDEEM = "mutation($token: String!) { redeem_sign_in_link(token: $token) { ok user { username } } }"
SPENT_MARK = "angee_iam_spent_credential"
LIST = "{ users(limit: 100) { username account_actions } }"
# IAM's own order, axes first: the bare harness authors ModelBackend ahead of it.
BACKENDS = IAM_SETTINGS["AUTHENTICATION_BACKENDS:append"]


class RefusingEmailBackend(BaseEmailBackend):
    """A default mailer that refuses every message, as an unreachable mail server does."""

    def send_messages(self, email_messages: Any) -> int:
        raise OSError("mail server down")


@pytest.fixture
def deliverable() -> Iterator[None]:
    """A deployment with Django's default mailer (locmem) and a public origin."""

    with override_settings(**DELIVERABLE):
        yield


@pytest.fixture
def sent(django_capture_on_commit_callbacks: Any) -> Any:
    """Run the email the verb defers to commit, as a committed request would."""

    def issue(target: Any, actor: Any) -> str:
        with django_capture_on_commit_callbacks(execute=True):
            target.with_actor(actor).issue_sign_in_link()
        return _token(mail.outbox[-1].body)

    return issue


def _person(username: str, **attrs: Any) -> Any:
    return User.objects.create_user(username, f"{username}@example.com", **attrs)


def _token(body: str) -> str:
    match = re.search(rf"{re.escape(ORIGIN)}/sign-in-link#token=(\S+)", body)
    assert match is not None, body
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


def test_issue_emails_one_link_to_the_account_and_stores_only_its_digest(deliverable, iam_admin, sent, caplog):
    target = _person("guest-ada")
    events: list[dict[str, Any]] = []

    def receive(sender: Any, instance: Any, link: Any, **kwargs: Any) -> None:
        events.append({"sender": sender, "instance": instance, "link": link, "extra": kwargs})

    sign_in_link_sent.connect(receive, weak=False)
    try:
        with caplog.at_level(logging.DEBUG):
            token = sent(target, iam_admin)
    finally:
        sign_in_link_sent.disconnect(receive)

    [message] = mail.outbox
    assert message.to == ["guest-ada@example.com"]
    assert message.content_subtype == "plain"
    secret = token.partition(".")[2]
    assert token.startswith(f"{target.public_id}.") and len(secret) == 43
    link = SignInLink._base_manager.get(user=target)
    stored = " ".join(str(value) for value in SignInLink._base_manager.filter(pk=link.pk).values().get().values())
    assert secret not in stored and token not in stored
    assert link.token_hash == SignInLink.token_digest(target.pk, secret)
    assert link.account_hash == SignInLink.account_digest(target)
    assert link.created_by_id == iam_admin.pk
    assert link.sent_at is not None and link.used_at is None
    ttl = timedelta(seconds=259_200)
    assert link.created_at + ttl - timedelta(seconds=5) <= link.expires_at <= link.created_at + ttl
    assert secret not in caplog.text
    assert [(event["sender"], event["instance"].pk, event["link"].pk) for event in events] == [
        (User, target.pk, link.pk),
    ]
    assert events[0]["link"].sent_at is not None and events[0]["extra"] == {"signal": sign_in_link_sent}


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
    with system_context(reason="test.iam.sign_in_link.change"):
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
    assert not SignInLink._base_manager.exists() and mail.outbox == []

    with django_capture_on_commit_callbacks(execute=True), system_context(reason="test.iam.sign_in_link.system"):
        User._base_manager.get(pk=target.pk).issue_sign_in_link()
    assert SignInLink._base_manager.get(user=target).created_by_id is None
    assert len(mail.outbox) == 1


@pytest.mark.parametrize(
    "attrs,refusal",
    [
        ({"is_active": False}, "Only active accounts can receive a sign-in link"),
        ({"is_staff": True}, "protected"),
        ({"is_superuser": True}, "protected"),
        ({"kind": "service"}, "Only person accounts"),
        ({"email": ""}, "no valid email address"),
        ({"email": "not-an-address"}, "no valid email address"),
    ],
)
def test_issue_refuses_ineligible_accounts_and_sends_nothing(deliverable, iam_admin, attrs, refusal):
    target = User.objects.create_user("ineligible", **{"email": "ineligible@example.com", **attrs})
    with actor_context(iam_admin), pytest.raises(ValidationError, match=refusal):
        target.with_actor(iam_admin).issue_sign_in_link()
    assert not SignInLink._base_manager.exists() and mail.outbox == []


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
    assert callbacks == [] and mail.outbox == []
    assert not SignInLink._base_manager.exists()


@pytest.mark.parametrize("missing", ["MAILERS", "ANGEE_PUBLIC_ORIGIN"])
def test_an_undeliverable_deployment_offers_no_link_and_refuses(iam_admin, missing):
    target = _person("guest-undeliverable")
    settings = {**DELIVERABLE, missing: {} if missing == "MAILERS" else "https://app.example.test/path"}
    with override_settings(**settings):
        assert "issue_sign_in_link" not in target.account_actions_for(to_subject_ref(iam_admin))
        with (
            actor_context(iam_admin),
            pytest.raises(ValidationError, match=re.escape(SIGN_IN_LINK_UNAVAILABLE_MESSAGE)),
        ):
            target.with_actor(iam_admin).issue_sign_in_link()
    assert not SignInLink._base_manager.exists()


def test_account_actions_offer_the_link_where_the_verb_acts(deliverable, iam_admin):
    _person("guest-offered")
    _person("guest-inactive", is_active=False)
    _person("guest-staff", is_staff=True)
    rows = result_data(execute_schema(_schema("console"), LIST, user=iam_admin))["users"]
    offered = {row["username"]: "issue_sign_in_link" in row["account_actions"] for row in rows}
    assert offered["guest-offered"] is True
    assert offered["guest-inactive"] is offered["guest-staff"] is offered["campaign-admin"] is False


def test_graphql_issue_and_redeem_sign_the_person_in_once(deliverable, iam_admin, django_capture_on_commit_callbacks):
    target = _person("guest-graphql")
    with django_capture_on_commit_callbacks(execute=True):
        issued = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert result_data(issued)["issue_user_sign_in_link"] == {
        "ok": True,
        "message": "Sign-in link sent to the account's email.",
    }
    token = _token(mail.outbox[-1].body)
    assert token.partition(".")[2] not in str(issued.data)

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
    assert not SignInLink._base_manager.exists() and mail.outbox == []


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
    assert attempts and secret not in str(attempts) and "wrong-" not in str(attempts)
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
    with system_context(reason="test.iam.sign_in_link.password"):
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


def test_a_failed_send_keeps_the_delivered_link_working(
    deliverable, iam_admin, sent, django_capture_on_commit_callbacks
):
    target = _person("guest-undelivered")
    delivered = sent(target, iam_admin)
    refusing = {"default": {"BACKEND": f"{__name__}.RefusingEmailBackend"}}
    with (
        override_settings(MAILERS=refusing),
        pytest.raises(OSError, match="mail server down"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        target.with_actor(iam_admin).issue_sign_in_link()

    rows = list(SignInLink._base_manager.filter(user=target).order_by("pk").values_list("sent_at", flat=True))
    assert len(rows) == 2 and rows[0] is not None and rows[1] is None
    assert _redeem(delivered) == target


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
    with system_context(reason="test.iam.sign_in_link.account"):
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
    with override_settings(ANGEE_IAM_SIGN_IN_LINK_COOLDOWN=60):
        with actor_context(iam_admin), pytest.raises(ValidationError, match="moments ago"):
            target.with_actor(iam_admin).issue_sign_in_link()
        SignInLink._base_manager.filter(user=target).update(created_at=timezone.now() - timedelta(seconds=61))
        assert sent(target, iam_admin)
    assert len(mail.outbox) == 2


@pytest.mark.parametrize(
    "overrides,deliverable_now",
    [
        ({"ANGEE_PUBLIC_ORIGIN": "http://app.example.test"}, False),
        ({"ANGEE_PUBLIC_ORIGIN": "http://app.example.test", "DEBUG": True}, True),
        ({"ANGEE_PUBLIC_ORIGIN": "https://user:secret@app.example.test"}, False),
        ({"ANGEE_PUBLIC_ORIGIN": "https://app.example.test:notaport"}, False),
        ({"ANGEE_PUBLIC_ORIGIN": "https://app.example.test/"}, True),
        ({"ANGEE_PUBLIC_ORIGIN": "https://*.example.test"}, False),
        ({"DEFAULT_FROM_EMAIL": "not an address"}, False),
        ({"DEFAULT_FROM_EMAIL": "Sign-in <signin@example.test>"}, True),
    ],
)
def test_links_need_an_https_origin_and_a_valid_sender(overrides, deliverable_now):
    with override_settings(**{**DELIVERABLE, **overrides}):
        assert SignInLink.deliverable() is deliverable_now


def test_links_open_the_route_iams_web_fragment_declares():
    manifest = Path(angee.iam.__file__).with_name("web") / "src" / "index.ts"
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

    def fail(sender: Any, **kwargs: Any) -> None:
        raise RuntimeError("receiver failed")

    raising = _at("203.0.113.67", session=True)
    user_login_failed.connect(fail, weak=False)
    try:
        result = execute_schema(_schema("public"), REDEEM, {"token": spent}, request=raising)
    finally:
        user_login_failed.disconnect(fail)
    assert result.errors
    assert SPENT_MARK not in vars(raising)


@override_settings(AUTHENTICATION_BACKENDS=BACKENDS, AXES_FAILURE_LIMIT=3, AXES_LOCKOUT_PARAMETERS=["username"])
def test_link_failures_stay_under_the_account_public_id_after_a_sign_in(deliverable, iam_admin, sent):
    target = _person("guest-keyed")
    token = sent(target, iam_admin)
    assert authenticate(_at("203.0.113.70"), **sign_in_credentials(f"{target.public_id}.guess")) is None

    signed_in = execute_schema(_schema("public"), REDEEM, {"token": token}, request=_at("203.0.113.70", session=True))

    assert result_data(signed_in)["redeem_sign_in_link"]["ok"] is True
    assert AccessAttempt.objects.get(username=target.public_id).failures_since_start == 1
