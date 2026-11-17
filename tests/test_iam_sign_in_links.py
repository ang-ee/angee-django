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
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.errors import MissingActorError

import angee.iam
from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from angee.iam.events import sign_in_link_sent
from angee.iam.models import SIGN_IN_LINK_PATH, SIGN_IN_LINK_UNAVAILABLE_MESSAGE
from tests.conftest import create_platform_admin
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_models import SignInLink
from tests.test_iam_graphql import _schema

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

User = get_user_model()
ORIGIN = "https://app.example.test"
DELIVERABLE = {
    "MAILERS": {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    "ANGEE_PUBLIC_ORIGIN": ORIGIN,
}
ISSUE = "mutation($id: ID!) { issue_user_sign_in_link(id: $id) { ok message } }"
REDEEM = "mutation($token: String!) { redeem_sign_in_link(token: $token) { ok user { username } } }"
LIST = "{ users(limit: 100) { username account_actions } }"


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


def _redeem(token: str) -> Any:
    return authenticate(_request(), sign_in_token=token)


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
    assert link.token_hash == SignInLink.token_digest(target, secret)
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
        ({"email": ""}, "no email address"),
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
    offered = {row["username"]: "ISSUE_SIGN_IN_LINK" in row["account_actions"] for row in rows}
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


@override_settings(AXES_FAILURE_LIMIT=2)
def test_axes_throttles_failed_links_without_seeing_the_token(deliverable, iam_admin, sent):
    target = _person("guest-throttled")
    token = sent(target, iam_admin)
    secret = token.partition(".")[2]
    failures: list[dict[str, Any]] = []

    def receive(sender: Any, credentials: dict[str, Any], **kwargs: Any) -> None:
        failures.append(dict(credentials))

    request = RequestFactory().post("/graphql/public/", REMOTE_ADDR="203.0.113.9")
    user_login_failed.connect(receive, weak=False)
    try:
        for attempt in range(2):
            assert authenticate(request, sign_in_token=f"{target.public_id}.wrong-{attempt}") is None
        assert authenticate(request, sign_in_token=token) is None
    finally:
        user_login_failed.disconnect(receive)

    assert SignInLink._base_manager.get(user=target).used_at is None
    assert failures and all(credentials == {"sign_in_token": "********************"} for credentials in failures)
    attempts = list(AccessAttempt.objects.values())
    assert attempts and secret not in str(attempts) and "wrong-" not in str(attempts)
    other = RequestFactory().post("/graphql/public/", REMOTE_ADDR="203.0.113.10")
    assert authenticate(other, sign_in_token=token) == target


def test_links_open_the_route_iams_web_fragment_declares():
    manifest = Path(angee.iam.__file__).with_name("web") / "src" / "index.ts"
    assert f'const SIGN_IN_LINK_PATH = "{SIGN_IN_LINK_PATH}";' in manifest.read_text()
