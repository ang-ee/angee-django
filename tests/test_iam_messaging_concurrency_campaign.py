"""PostgreSQL-only serialization of sign-in links: one link signs in once, whatever races it."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext, override_settings
from rebac import actor_context

from angee.base.testing.queries import statement_startswith
from angee.iam_messaging.auth import sign_in_credentials
from angee.iam_messaging.testing.models import SignInLink
from tests.iam_campaign import iam_admin as iam_admin
from tests.test_iam_concurrency_campaign import _thread
from tests.test_iam_messaging_sign_in_links import DELIVERABLE, _token

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL sign-in link row-lock contract"),
]
User = get_user_model()


def _sign_in(token):
    return authenticate(RequestFactory().post("/graphql/public/"), **sign_in_credentials(token))


@override_settings(**DELIVERABLE)
def test_two_redemptions_of_one_sign_in_link_sign_in_once(iam_admin):
    target = User.objects.create_user("link-race", "link-race@example.com")
    with actor_context(iam_admin):
        target.with_actor(iam_admin).issue_sign_in_link()
    token = _token(mail.outbox[-1].body)
    ready = Barrier(2)

    def redeem():
        ready.wait(timeout=5)
        return _sign_in(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_thread, redeem) for _ in range(2)]
        outcomes = [future.result(timeout=15) for future in futures]
    assert [user.pk for user in outcomes if user is not None] == [target.pk]
    assert SignInLink._base_manager.get(user=target).used_at is not None


@override_settings(**DELIVERABLE)
def test_issuing_a_link_while_the_previous_one_is_redeemed_leaves_one_consistent_outcome(iam_admin):
    target = User.objects.create_user("issue-race", "issue-race@example.com")
    with actor_context(iam_admin):
        target.with_actor(iam_admin).issue_sign_in_link()
    previous = _token(mail.outbox[-1].body)
    ready = Barrier(2)

    def issue():
        user = User._base_manager.get(pk=target.pk)
        ready.wait(timeout=5)
        with actor_context(iam_admin):
            user.with_actor(iam_admin).issue_sign_in_link()

    def redeem():
        ready.wait(timeout=5)
        return _sign_in(previous)

    with ThreadPoolExecutor(max_workers=2) as pool:
        issued, redeemed = pool.submit(_thread, issue), pool.submit(_thread, redeem)
        issued.result(timeout=15)
        signed_in = redeemed.result(timeout=15)
    newest = _token(mail.outbox[-1].body)
    used = SignInLink._base_manager.filter(user=target, used_at__isnull=False).count()
    # Either the previous link signed in before the new one was sent, or the new
    # one was sent first and the previous one was no longer current.
    assert (signed_in.pk, used) == (target.pk, 1) if signed_in is not None else used == 0
    assert _sign_in(newest).pk == target.pk


@override_settings(**{**DELIVERABLE, "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": 60})
def test_two_issues_within_the_cooldown_store_one_link_and_send_one_mail(iam_admin):
    target = User.objects.create_user("issue-pair", "issue-pair@example.com")
    ready = Barrier(2)

    def issue():
        user = User._base_manager.get(pk=target.pk)
        ready.wait(timeout=5)
        with actor_context(iam_admin):
            try:
                user.with_actor(iam_admin).issue_sign_in_link()
            except ValidationError as error:
                assert error.messages == ["A sign-in link was sent to this account moments ago; wait to send another."]
                return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_thread, issue) for _ in range(2)]
        outcomes = sorted(future.result(timeout=15) for future in futures)
    assert outcomes == [False, True]
    assert SignInLink._base_manager.filter(user=target).count() == 1
    assert [message.to for message in mail.outbox] == [["issue-pair@example.com"]]


@override_settings(**{**DELIVERABLE, "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": 60})
def test_issuing_a_link_locks_the_account_before_it_reads_the_cooldown(iam_admin):
    target = User.objects.create_user("link-locked", "link-locked@example.com")
    with actor_context(iam_admin), CaptureQueriesContext(connection) as captured:
        target.with_actor(iam_admin).issue_sign_in_link()
    statements = [query["sql"] for query in captured]
    locks = [index for index, sql in enumerate(statements) if User._meta.db_table in sql and "FOR UPDATE" in sql]
    reads = [
        index for index, sql in enumerate(statements)
        if SignInLink._meta.db_table in sql and statement_startswith(sql, "SELECT")
    ]
    assert len(locks) == 1 and reads
    assert locks[0] < reads[0]
