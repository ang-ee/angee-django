"""The Python email key and stored-column uniqueness."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.iam.models import AccountExists, AmbiguousAccountEmail
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_campaign import legacy_person_emails as legacy_person_emails

User = get_user_model()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        (" \t\r\n\u2003", ""),
        (" \tMiXeD.Local@EXAMPLE.COM\n", "mixed.local@example.com"),
        ("\u00a0ÄBC@EXAMPLE.COM\u2003", "äbc@example.com"),
        ("İ@EXAMPLE.COM", "i\u0307@example.com"),
        ("STRAẞE@EXAMPLE.COM", "straße@example.com"),
        ("K@EXAMPLE.COM", "k@example.com"),
    ],
)
def test_python_email_owner_strips_unicode_whitespace_and_lowercases_whole_address(raw, expected):
    assert User.objects.normalize_email(raw) == expected
    assert User.objects.normalize_email(expected) == expected


@pytest.mark.parametrize("kind", ["person", "service"])
def test_clean_and_email_saves_use_the_same_python_key(db, kind):
    user = User(username="normalize", kind=kind, email="\u2003Ä@EXAMPLE.COM\u00a0")
    user.clean()
    assert user.email == "ä@example.com"
    with system_context(reason="test.email.normalization"):
        user.email = "\u2003İ@EXAMPLE.COM\u00a0"
        user.save()
        assert User._base_manager.get(pk=user.pk).email == "i\u0307@example.com"
        user.email = "\tNEXT@EXAMPLE.COM\n"
        user.save(update_fields=["email"])
    assert User._base_manager.get(pk=user.pk).email == "next@example.com"


def test_partial_password_save_neither_loads_nor_rewrites_deferred_legacy_email(db):
    user = User.objects.create_user("partial", "partial@example.com")
    User._base_manager.filter(pk=user.pk).update(email=" Legacy@EXAMPLE.COM ")
    partial = User._base_manager.defer("email").get(pk=user.pk)
    partial.set_unusable_password()
    with system_context(reason="test.email.partial"), CaptureQueriesContext(connection) as captured:
        partial.save(update_fields=["password"])
    user_queries = [q["sql"] for q in captured if User._meta.db_table in q["sql"]]
    assert len(user_queries) == 1
    assert user_queries[0].startswith("UPDATE")
    assert '"email"' not in user_queries[0]
    assert "email" in partial.get_deferred_fields()
    assert User._base_manager.get(pk=user.pk).email == " Legacy@EXAMPLE.COM "


def test_bulk_create_and_queryset_update_bypass_documented_normalization(db):
    with system_context(reason="test.email.bulk"):
        User.objects.bulk_create([User(username="bulk", email=" BULK@EXAMPLE.COM ")])
        assert User._base_manager.get(username="bulk").email == " BULK@EXAMPLE.COM "
        User.objects.filter(username="bulk").update(email=" UPDATE@EXAMPLE.COM ")
        assert User._base_manager.get(username="bulk").email == " UPDATE@EXAMPLE.COM "
        User.objects.filter(username="bulk").update(email=User.objects.normalize_email(" UPDATE@EXAMPLE.COM "))
    assert User._base_manager.get(username="bulk").email == "update@example.com"


@pytest.mark.parametrize("attrs", [{}, {"is_active": False}, {"is_staff": True}, {"is_superuser": True}])
def test_person_email_lookup_includes_every_person_and_duplicate_creation_refuses(iam_admin, attrs):
    existing = User.objects.create_user("existing", "Ä@EXAMPLE.COM", **attrs)
    with CaptureQueriesContext(connection) as captured:
        assert User.objects.person_for_email("\u2003Ä@EXAMPLE.COM\u00a0") == existing
    selects = [q["sql"] for q in captured if q["sql"].startswith("SELECT") and User._meta.db_table in q["sql"]]
    assert len(selects) == 1
    assert "LIMIT 2" in selects[0]
    assert "LOWER(" not in selects[0].upper()
    assert "TRIM(" not in selects[0].upper()
    with actor_context(iam_admin), pytest.raises(AccountExists) as caught:
        User.objects.create_person("duplicate", "\tÄ@EXAMPLE.COM\n")
    assert caught.value.args == ("ACCOUNT_EXISTS",)
    assert not User._base_manager.filter(username="duplicate").exists()


def test_person_email_lookup_ignores_services_empty_keys_and_missing_accounts(db):
    User.objects.create_user("service", "only-service@example.com", kind="service")
    User.objects.create_user("empty-a")
    User.objects.create_user("empty-b")
    for email in ("only-service@example.com", "missing@example.com", "", " \t\n"):
        assert User.objects.person_for_email(email) is None


def test_ambiguous_lookup_refuses_with_code_only_and_creation_does_not_add_a_third(legacy_person_emails, iam_admin):
    for username in ("legacy-a", "legacy-b"):
        User.objects.create_user(username, "ambiguous@example.com")
    with pytest.raises(AmbiguousAccountEmail) as caught:
        User.objects.person_for_email(" AMBIGUOUS@EXAMPLE.COM ")
    assert caught.value.args == ("ACCOUNT_EMAIL_AMBIGUOUS",)
    with actor_context(iam_admin), pytest.raises(AccountExists, match="^ACCOUNT_EXISTS$"):
        User.objects.create_person("legacy-c", "ambiguous@example.com")
    assert User._base_manager.filter(email="ambiguous@example.com").count() == 2


@pytest.mark.parametrize("attrs", [{}, {"is_active": False}, {"is_staff": True}])
def test_constraint_rejects_normalized_duplicate_even_through_trusted_create_user(db, attrs):
    User.objects.create_user("first", "key@example.com", **attrs)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("second", " KEY@EXAMPLE.COM ")
    assert not User._base_manager.filter(username="second").exists()


def test_unique_constraint_excludes_services_and_empty_person_emails(db):
    User.objects.create_user("person", "shared@example.com")
    for username in ("service-a", "service-b"):
        User.objects.create_user(username, " SHARED@EXAMPLE.COM ", kind="service")
    for username in ("empty-a", "empty-b"):
        User.objects.create_user(username, " \t\n")
    assert User._base_manager.count() == 5
