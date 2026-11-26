"""Login throttling at IAM's auth seam: one attempt per request, guesses counted, a spent credential exempt."""

from typing import Any

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import SESSION_KEY, authenticate, get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_login_failed
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import override_settings

from angee.graphql.testing.documents import execute_schema, result_data
from angee.iam.auth import ONE_ATTEMPT_MESSAGE, ModelBackend, authenticate_once, mark_spent_credential
from angee.iam.autoconfig import SETTINGS as IAM_SETTINGS
from tests.test_iam_graphql import _schema

User = get_user_model()
LOGIN = "mutation($username: String!, $password: String!) { login(username: $username, password: $password) { ok } }"
SPENT_MARK = "angee_iam_spent_credential"
# IAM's own order, axes first: the bare harness authors ModelBackend ahead of it.
BACKENDS = IAM_SETTINGS["AUTHENTICATION_BACKENDS:append"]


class SpentCredentialBackend(ModelBackend):
    """A credential backend standing in for any other than a password.

    ``test_secret="spent:<account>"`` is a genuine credential for ``<account>``
    that is spent; any other secret is a guess. Like every credential, it names
    the account it signs in as ``sign_in_account``.
    """

    def authenticate(  # type: ignore[override]
        self, request: Any, *, test_secret: str, sign_in_account: str = "",
    ) -> Any | None:
        spent, _, account = test_secret.partition(":")
        if spent == "spent":
            mark_spent_credential(request, account)
        return None

    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]


CREDENTIAL_BACKENDS = [*BACKENDS, f"{__name__}.SpentCredentialBackend"]


def _person(username: str, **attrs: Any) -> Any:
    return User.objects.create_user(username, f"{username}@example.com", **attrs)


def _at(ip: str, *, session: bool = False) -> Any:
    """Return a GraphQL request from ``ip``; ``session`` adds the anonymous session a sign-in rotates."""

    request = RequestFactory().post("/graphql/public/", REMOTE_ADDR=ip)
    if session:
        request.user = AnonymousUser()
        SessionMiddleware(lambda _request: HttpResponse()).process_request(request)
    return request


def _logins(attempts: list[tuple[str, str, str]]) -> str:
    """One GraphQL document carrying one aliased ``login`` per ``(alias, username, password)``."""

    fields = " ".join(
        f'{alias}: login(username: "{user}", password: "{password}") {{ ok }}' for alias, user, password in attempts
    )
    return f"mutation {{ {fields} }}"


def _refusals(result: Any) -> dict[str, str]:
    return {str(error.path[0]): error.message for error in result.errors or ()}


def _failures(ip: str) -> int:
    return sum(AccessAttempt.objects.filter(ip_address=ip).values_list("failures_since_start", flat=True))


@override_settings(AUTHENTICATION_BACKENDS=BACKENDS, AXES_FAILURE_LIMIT=3)
def test_a_request_makes_one_sign_in_attempt_however_many_fields_it_aliases(db):
    _person("guest-guarded", password="guarded-secret-pw")
    _person("guest-own", password="own-secret-pw")
    attempts = [
        ("g1", "guest-guarded", "bad-1"),
        ("me", "guest-own", "own-secret-pw"),
        ("hit", "guest-guarded", "guarded-secret-pw"),
    ]

    request = _at("203.0.113.50", session=True)
    result = execute_schema(_schema("public"), _logins(attempts), request=request)

    # The second field is refused, which nulls the payload and stops the fields after it.
    assert result.data is None
    assert _refusals(result) == {"me": ONE_ATTEMPT_MESSAGE}
    assert SESSION_KEY not in request.session
    assert AccessAttempt.objects.get(ip_address="203.0.113.50").failures_since_start == 1


@override_settings(AUTHENTICATION_BACKENDS=BACKENDS, AXES_FAILURE_LIMIT=3)
def test_a_clients_own_sign_ins_never_clear_its_guesses(db):
    _person("guest-targeted", password="targeted-secret-pw")
    _person("guest-guesser", password="guesser-secret-pw")

    def login(username: str, password: str) -> Any:
        request = _at("203.0.113.53", session=True)
        return result_data(
            execute_schema(_schema("public"), LOGIN, {"username": username, "password": password}, request=request)
        )

    assert login("guest-targeted", "bad-1") == {"login": {"ok": False}}
    assert login("guest-targeted", "bad-2") == {"login": {"ok": False}}
    assert login("guest-guesser", "guesser-secret-pw") == {"login": {"ok": True}}
    assert login("guest-targeted", "bad-3") == {"login": {"ok": False}}
    assert login("guest-targeted", "targeted-secret-pw") == {"login": {"ok": False}}


@override_settings(AUTHENTICATION_BACKENDS=CREDENTIAL_BACKENDS, AXES_FAILURE_LIMIT=2)
def test_a_spent_credential_is_no_guess_for_the_account_it_names_and_only_in_its_attempt(db):
    holder = _person("spent-holder", password="holder-secret-pw")
    other = _person("spent-other")
    request = _at("203.0.113.80")

    for _ in range(3):
        assert authenticate(request, test_secret=f"spent:{holder.public_id}", sign_in_account=holder.public_id) is None
    assert _failures("203.0.113.80") == 0
    # The mark ended with its attempt: the next guess at the same account counts.
    assert authenticate(request, test_secret="guess", sign_in_account=holder.public_id) is None
    assert _failures("203.0.113.80") == 1
    # A mark for one account exempts no credential naming another.
    assert authenticate(request, test_secret=f"spent:{holder.public_id}", sign_in_account=other.public_id) is None
    assert _failures("203.0.113.80") == 2
    # Two counted guesses lock the client out, password included.
    assert authenticate(request, username="spent-holder", password="holder-secret-pw") is None


@override_settings(AUTHENTICATION_BACKENDS=CREDENTIAL_BACKENDS, AXES_ENABLED=False)
def test_the_spent_mark_ends_with_the_attempt_even_when_it_raises(db):
    holder = _person("spent-raising")
    credentials = {"test_secret": f"spent:{holder.public_id}", "sign_in_account": holder.public_id}
    quiet = _at("203.0.113.81")
    assert authenticate_once(quiet, **credentials) is None
    assert SPENT_MARK not in vars(quiet)

    marked: list[str | None] = []

    def fail(sender: Any, request: Any = None, **kwargs: Any) -> None:
        marked.append(vars(request).get(SPENT_MARK))
        raise RuntimeError("receiver failed")

    raising = _at("203.0.113.82")
    user_login_failed.connect(fail, weak=False)
    try:
        with pytest.raises(RuntimeError, match="receiver failed"):
            authenticate_once(raising, **credentials)
    finally:
        user_login_failed.disconnect(fail)
    # The backend marked the attempt while it ran, and the mark ended with it.
    assert marked == [holder.public_id]
    assert SPENT_MARK not in vars(raising)
