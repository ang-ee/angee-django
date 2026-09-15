"""Reusable transaction-bound authority for manager-owned mutations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import get_ident
from typing import Generic, TypeVar

from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper

_PayloadT = TypeVar("_PayloadT")


@dataclass(slots=True)
class _AuthorityToken(Generic[_PayloadT]):
    """One live capability bound to an exact database transaction context."""

    alias: str
    connection: BaseDatabaseWrapper
    outer_atomic: object
    thread_id: int
    payload: _PayloadT
    closed: bool = False


class TransactionBoundAuthority(Generic[_PayloadT]):
    """Bind a manager-owned capability to one existing outer transaction.

    The authority does not open or commit a transaction.  Its caller owns that
    lifecycle.  A copied context retains the same mutable token, so closing the
    originating scope revokes every copy before the local ContextVar is reset.
    """

    def __init__(
        self,
        context_name: str,
        *,
        atomic_error: str,
        nested_error: str,
    ) -> None:
        self._atomic_error = atomic_error
        self._nested_error = nested_error
        self._current: ContextVar[_AuthorityToken[_PayloadT] | None] = ContextVar(
            context_name,
            default=None,
        )

    @contextmanager
    def scope(
        self,
        alias: str,
        payload: _PayloadT,
        *,
        allow_nested: bool = False,
    ) -> Iterator[_PayloadT]:
        """Expose ``payload`` only inside the exact current outer transaction.

        Owners that need a strictly nested policy may opt in. Each nested scope
        receives its own token and must share the exact alias, connection,
        thread and outer transaction; closing it restores the parent token.
        """

        connection = connections[alias]
        normalized_alias = connection.alias
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise RuntimeError(self._atomic_error)
        parent = self._current.get()
        if parent is not None and (
            not allow_nested or self._active_token(alias) is not parent
        ):
            raise RuntimeError(self._nested_error)
        authority = _AuthorityToken(
            alias=normalized_alias,
            connection=connection,
            outer_atomic=connection.atomic_blocks[0],
            thread_id=get_ident(),
            payload=payload,
        )
        token = self._current.set(authority)
        try:
            yield payload
        finally:
            authority.closed = True
            self._current.reset(token)

    def payload(self, alias: str) -> _PayloadT | None:
        """Return the live payload, or ``None`` when this call has no authority.

        ``None`` may itself be an authorized payload.  Call :meth:`is_active`
        when a caller needs to distinguish that case from absent authority.
        """

        authority = self._active_token(alias)
        return None if authority is None else authority.payload

    def is_active(self, alias: str) -> bool:
        """Return whether this call owns the exact live transaction capability."""

        return self._active_token(alias) is not None

    def _active_token(self, alias: str) -> _AuthorityToken[_PayloadT] | None:
        """Resolve the current capability after validating every lifetime fence."""

        authority = self._current.get()
        connection = connections[alias]
        normalized_alias = connection.alias
        if authority is None:
            return None
        if (
            authority.closed
            or self._current.get() is not authority
            or authority.alias != normalized_alias
            or authority.connection is not connection
            or authority.thread_id != get_ident()
            or not connection.in_atomic_block
            or not connection.atomic_blocks
            or authority.outer_atomic is not connection.atomic_blocks[0]
        ):
            return None
        return authority
