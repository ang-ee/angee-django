"""PostgreSQL-only account uniqueness and one-time credential serialization."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.iam.models import AccountExists
from tests.iam_campaign import Person
from tests.iam_campaign import iam_admin as iam_admin

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL IAM row-lock and two-writer contract"),
]
User = get_user_model()


def _thread(call):
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def test_issue_password_locks_target_before_testing_eligibility(iam_admin):
    target = User.objects.create_user("locked-target")
    with actor_context(iam_admin), CaptureQueriesContext(connection) as captured:
        target.with_actor(iam_admin).issue_password()
    locks = [q["sql"] for q in captured if User._meta.db_table in q["sql"] and "FOR UPDATE" in q["sql"]]
    assert len(locks) == 1


def test_two_password_writers_return_exactly_one_credential(iam_admin):
    target = User.objects.create_user("password-race")
    ready = Barrier(2)

    def issue():
        user = User._base_manager.get(pk=target.pk)
        ready.wait(timeout=5)
        with actor_context(iam_admin):
            try:
                return user.with_actor(iam_admin).issue_password()
            except ValidationError as error:
                assert error.messages == ["This account already has a usable password."]
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_thread, issue) for _ in range(2)]
        outcomes = [future.result(timeout=15) for future in futures]
    passwords = [password for password in outcomes if password is not None]
    assert len(passwords) == 1
    stored = User._base_manager.get(pk=target.pk)
    assert stored.check_password(passwords[0])
    assert stored.password != passwords[0]


def test_two_person_creators_commit_one_account_and_one_parties_link(iam_admin):
    ready = Barrier(2)

    def create(username):
        ready.wait(timeout=5)
        with actor_context(iam_admin):
            try:
                return User.objects.create_person(username, " RACE@EXAMPLE.COM ").pk
            except AccountExists as error:
                assert error.args == ("ACCOUNT_EXISTS",)
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_thread, lambda name=name: create(name)) for name in ("racer-a", "racer-b")]
        outcomes = [future.result(timeout=15) for future in futures]
    assert sum(pk is not None for pk in outcomes) == 1
    account = User._base_manager.get(email="race@example.com")
    assert Person._base_manager.filter(user=account).count() == 1
