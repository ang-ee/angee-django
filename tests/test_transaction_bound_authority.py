"""Transaction-bound manager authority lifetime contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from django.db import connection, connections, transaction

from angee.base.authority import TransactionBoundAuthority

pytestmark = pytest.mark.django_db(transaction=True)


def _authority() -> TransactionBoundAuthority[object]:
    return TransactionBoundAuthority(
        "test_transaction_bound_authority",
        atomic_error="test authority requires atomic",
        nested_error="test authority cannot nest",
    )


def test_authority_requires_atomic_and_rejects_nesting() -> None:
    authority = _authority()
    alias = connection.alias

    with pytest.raises(RuntimeError, match="test authority requires atomic"):
        with authority.scope(alias, object()):
            pass

    with transaction.atomic(using=alias):
        with authority.scope(alias, object()):
            with pytest.raises(RuntimeError, match="test authority cannot nest"):
                with authority.scope(alias, object()):
                    pass


def test_authority_preserves_payload_and_outer_atomic_lifetime() -> None:
    authority = _authority()
    alias = connection.alias
    payload = object()

    with transaction.atomic(using=alias):
        with authority.scope(alias, payload) as yielded:
            assert yielded is payload
            assert authority.payload(alias) is payload
            assert authority.is_active(alias)
            with transaction.atomic(using=alias):
                assert authority.payload(alias) is payload
                assert authority.is_active(alias)
            assert authority.payload(alias) is payload
            assert authority.is_active(alias)
        assert authority.payload(alias) is None
        assert not authority.is_active(alias)

    with transaction.atomic(using=alias):
        with authority.scope(alias, None):
            assert authority.payload(alias) is None
            assert authority.is_active(alias)


def test_authority_opt_in_nesting_uses_distinct_context_local_tokens() -> None:
    authority = _authority()
    alias = connection.alias
    outer = object()
    inner = object()

    with transaction.atomic(using=alias):
        with authority.scope(alias, outer):
            outer_context = copy_context()
            with authority.scope(alias, inner, allow_nested=True):
                inner_context = copy_context()
                assert authority.payload(alias) is inner
                assert outer_context.run(authority.payload, alias) is outer
            assert authority.payload(alias) is outer
            assert not inner_context.run(authority.is_active, alias)
        assert not outer_context.run(authority.is_active, alias)


def test_authority_rejects_wrong_alias_without_consuming_payload() -> None:
    authority = _authority()
    alias = connection.alias
    other_alias = "authority_other"
    connections.settings[other_alias] = dict(connections.settings[alias])
    try:
        with transaction.atomic(using=alias):
            with authority.scope(alias, object()):
                assert authority.payload(other_alias) is None
                assert not authority.is_active(other_alias)
                assert authority.is_active(alias)
    finally:
        other = connections[other_alias]
        other.close()
        del connections[other_alias]
        del connections.settings[other_alias]


def test_authority_closes_copied_context_on_success_and_exception() -> None:
    authority = _authority()
    alias = connection.alias

    with transaction.atomic(using=alias):
        with authority.scope(alias, object()):
            successful_copy = copy_context()
        assert not successful_copy.run(authority.is_active, alias)

    with pytest.raises(ValueError, match="abort owner"):
        with transaction.atomic(using=alias):
            with authority.scope(alias, object()):
                failed_copy = copy_context()
                raise ValueError("abort owner")
    assert not failed_copy.run(authority.is_active, alias)


def test_authority_rejects_copied_context_in_another_thread() -> None:
    authority = _authority()
    alias = connection.alias

    with transaction.atomic(using=alias):
        with authority.scope(alias, object()):
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(copied.run, authority.is_active, alias)
                assert not result.result(timeout=5)
            assert authority.is_active(alias)


def test_authority_rejects_same_context_in_a_later_outer_atomic() -> None:
    authority = _authority()
    alias = connection.alias

    with transaction.atomic(using=alias):
        with authority.scope(alias, object()):
            retained = copy_context()

    with transaction.atomic(using=alias):
        assert not retained.run(authority.is_active, alias)
