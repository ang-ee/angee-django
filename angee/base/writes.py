"""Manager-owned write fences and append-only queryset primitives.

A queryset policy override fires only under an ingress owner. Policy querysets
therefore inherit :class:`WriteFencedQuerySetMixin` directly instead of relying
on a later consumer to place the ingress methods somewhere in its MRO.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar, cast

from django.core import checks
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, models

from angee.base.authority import TransactionBoundAuthority

_ModelT = TypeVar("_ModelT", bound=models.Model)
_WritePayloadT = TypeVar("_WritePayloadT")


@dataclass(slots=True)
class WriteFenceToken(Generic[_WritePayloadT]):
    """One exact manager-owned ORM write inside a live transaction."""

    model: type[models.Model]
    operation: str
    object_identity: int | None
    payload: _WritePayloadT
    consumed: bool = False

    def matches(self, target: models.Model, operation: str) -> bool:
        """Return whether this unused token owns ``operation`` on ``target``."""

        return (
            not self.consumed
            and self.model is type(target)
            and self.operation == operation
            and self.object_identity == id(target)
        )

    def consume(self) -> None:
        """Consume this one-use capability, rejecting a second write."""

        if self.consumed:
            raise RuntimeError("Manager-owned write authority was already consumed.")
        self.consumed = True


class WriteFence(Generic[_WritePayloadT]):
    """Bind exact manager-owned write tokens to an existing outer transaction."""

    def __init__(
        self,
        context_name: str,
        *,
        atomic_error: str,
        nested_error: str,
    ) -> None:
        self._authority = TransactionBoundAuthority[WriteFenceToken[_WritePayloadT]](
            context_name,
            atomic_error=atomic_error,
            nested_error=nested_error,
        )

    @contextmanager
    def scope(
        self,
        alias: str,
        token: WriteFenceToken[_WritePayloadT],
    ) -> Iterator[WriteFenceToken[_WritePayloadT]]:
        """Expose ``token`` within its caller-owned outer transaction."""

        with self._authority.scope(alias, token):
            yield token

    def token(self, alias: str) -> WriteFenceToken[_WritePayloadT] | None:
        """Return the current live token for ``alias``, if this call owns it."""

        return self._authority.payload(alias)


class WriteFencedQuerySetMixin:
    """Route every bulk write ingress through one cooperative policy hook."""

    model: type[models.Model]

    def _validate_write_fence(
        self,
        operation: str,
        *,
        objects: tuple[models.Model, ...] = (),
        changed_fields: tuple[str, ...] = (),
        values: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        """Delegate the write decision to a model composing ``WriteFencedModel``."""

        validator = getattr(self.model, "validate_queryset_write_fence", None)
        if validator is not None:
            validator(
                operation,
                queryset=self,
                objects=objects,
                changed_fields=changed_fields,
                values=values or {},
                options=options or {},
            )

    def update(self, **kwargs: Any) -> int:
        """Validate an exact queryset update before dispatching to Django."""

        self._validate_write_fence(
            "update",
            changed_fields=tuple(kwargs),
            values=kwargs,
        )
        return cast(Any, super()).update(**kwargs)

    def bulk_create(
        self,
        objs: Iterable[models.Model],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[models.Model]:
        """Validate batched inserts without consuming a one-shot iterable twice."""

        objects = tuple(objs)
        update_field_names = None if update_fields is None else tuple(update_fields)
        unique_field_names = None if unique_fields is None else tuple(unique_fields)
        options = {
            "batch_size": batch_size,
            "ignore_conflicts": ignore_conflicts,
            "update_conflicts": update_conflicts,
            "update_fields": update_field_names,
            "unique_fields": unique_field_names,
        }
        self._validate_write_fence(
            "bulk_create",
            objects=objects,
            changed_fields=update_field_names or (),
            options=options,
        )
        return cast(Any, super()).bulk_create(objects, **options)

    def bulk_update(
        self,
        objs: Iterable[models.Model],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        """Validate batched updates against their materialized rows and fields."""

        objects, field_names = tuple(objs), tuple(fields)
        self._validate_write_fence(
            "bulk_update",
            objects=objects,
            changed_fields=field_names,
            options={"batch_size": batch_size},
        )
        # Django implements bulk_update() through update(); hooks must stay stateless.
        return cast(Any, super()).bulk_update(objects, field_names, batch_size=batch_size)

    def delete(self) -> tuple[int, dict[str, int]]:
        """Validate a queryset delete before Django's collector runs."""

        self._validate_write_fence("delete")
        return cast(Any, super()).delete()

    def _raw_delete(self, using: str) -> int:
        """Validate a collector fast-delete before Django issues direct SQL."""

        self._validate_write_fence("_raw_delete")
        return cast(Any, super())._raw_delete(using)


class WriteFencedModel(models.Model):
    """Require exact manager-issued authority for instance saves and deletes.

    A domain owner supplies one :class:`WriteFence`, an error message, and only
    the policy facts that differ by overriding :meth:`validate_write_fence`.
    The base owns exact-instance matching, one-use consumption, instance write
    entrypoints, and the matching queryset blockers. Inbound foreign keys must
    use ``on_delete=PROTECT`` or ``RESTRICT``: the collector's ``delete_batch``
    path cannot be intercepted by a model or queryset mixin, and persistence
    enforcement is never delegated to a database trigger.
    """

    write_fence: ClassVar[WriteFence[Any] | None] = None
    write_fence_error: ClassVar[str] = "Change this row through its native manager."
    write_fence_exception: ClassVar[type[Exception]] = TypeError

    class Meta:
        abstract = True

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require every concrete manager to carry the queryset ingress owner."""

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors
        for manager in cls._meta.managers:
            if not isinstance(manager.get_queryset(), WriteFencedQuerySetMixin):
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.{manager.name} must resolve a WriteFencedQuerySetMixin queryset.",
                        obj=cls,
                        id="angee.E019",
                    )
                )
        return errors

    @classmethod
    def validate_queryset_write_fence(
        cls,
        operation: str,
        *,
        queryset: models.QuerySet[Any],
        objects: tuple[models.Model, ...],
        changed_fields: tuple[str, ...],
        values: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> None:
        """Reject bulk writes; the manager-owned instance path is the sole writer."""

        del operation, queryset, objects, changed_fields, values, options
        raise cls.write_fence_exception(cls.write_fence_error)

    def validate_write_fence(
        self,
        token: WriteFenceToken[Any],
        *,
        operation: str,
        values: Mapping[str, Any],
    ) -> None:
        """Validate site-specific payload facts after exact target matching."""

        del token, operation, values

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Consume one exact manager-owned save authority and persist the row."""

        self._consume_write_fence("save", kwargs)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Consume one exact manager-owned delete authority and delete the row."""

        self._consume_write_fence("delete", kwargs)
        return super().delete(*args, **kwargs)

    def _consume_write_fence(self, operation: str, values: Mapping[str, Any]) -> None:
        """Validate and consume this row's live exact-instance capability."""

        fence = type(self).write_fence
        alias = str(values.get("using") or self._state.db or DEFAULT_DB_ALIAS)
        token = fence.token(alias) if fence is not None else None
        if token is None or not token.matches(self, operation):
            raise type(self).write_fence_exception(type(self).write_fence_error)
        self.validate_write_fence(token, operation=operation, values=values)
        token.consume()


class WriteFencedManagerMixin:
    """Give a domain manager the one canonical exact-instance save helper."""

    def _save(
        self,
        row: _ModelT,
        *,
        payload: Any = None,
        using: str | None = None,
        **kwargs: Any,
    ) -> _ModelT:
        """Save ``row`` under one one-use token in the caller's transaction."""

        fence = cast(WriteFence[Any] | None, getattr(type(row), "write_fence", None))
        if fence is None:
            raise TypeError(f"{type(row).__name__} declares no write fence.")
        alias = using or row._state.db or getattr(self, "db", DEFAULT_DB_ALIAS)
        token = WriteFenceToken(
            model=type(row),
            operation="save",
            object_identity=id(row),
            payload=payload,
        )
        with fence.scope(alias, token):
            row.save(using=alias, **kwargs)
        if not token.consumed:
            raise RuntimeError("Manager-owned save authority was not consumed.")
        return row


class WriteFencedSystemManagerMixin:
    """Expose guarded rows to Django and field-backed REBAC traversal."""

    write_fence_system_reason: ClassVar[str] = "angee.write_fence.system_manager"

    def get_queryset(self) -> models.QuerySet[Any]:
        """Return the manager queryset under its declared system context."""

        queryset = cast(Any, super()).get_queryset()
        return queryset.system_context(reason=self.write_fence_system_reason)


class ImmutableEvidenceQuerySet(WriteFencedQuerySetMixin, Generic[_ModelT]):
    """Append-only evidence collection with cooperative insert policy hooks."""

    model: type[_ModelT]
    db: str

    def immutable_error(self, operation: str) -> Exception:
        """Return the domain-facing error for a forbidden evidence mutation."""

        action = "deleted" if operation in {"delete", "raw_delete"} else "edited"
        return ValidationError(f"Evidence cannot be {action}.")

    def validate_evidence_insert(
        self,
        *,
        objects: tuple[models.Model, ...],
        ignore_conflicts: bool,
        update_conflicts: bool,
    ) -> None:
        """Reject conflict updates; subclasses may narrow insert authority further."""

        del objects, ignore_conflicts
        if update_conflicts:
            raise self.immutable_error("update")

    def validate_evidence_update(self, values: Mapping[str, Any]) -> bool:
        """Return whether one exceptional owner-approved queryset update may run."""

        del values
        return False

    def create(self, **kwargs: Any) -> _ModelT:
        """Validate one append before inserting it."""

        self.validate_evidence_insert(
            objects=(),
            ignore_conflicts=False,
            update_conflicts=False,
        )
        return cast(Any, super()).create(**kwargs)

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Validate append-only conflict policy and insert materialized rows."""

        objects = tuple(objs)
        self.validate_evidence_insert(
            objects=objects,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
        )
        return cast(Any, super()).bulk_create(
            objects,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def update(self, **kwargs: Any) -> int:
        """Reject post-insert changes outside an explicit cooperative exception."""

        if self.validate_evidence_update(kwargs):
            return cast(Any, super()).update(**kwargs)
        raise self.immutable_error("update")

    def bulk_update(
        self,
        objs: Iterable[_ModelT],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        """Reject batched evidence edits."""

        del objs, fields, batch_size
        raise self.immutable_error("bulk_update")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Reject ORM deletion of retained evidence."""

        raise self.immutable_error("delete")

    def _raw_delete(self, using: str) -> int:
        """Reject cascade deletion paths that bypass :meth:`delete`."""

        del using
        raise self.immutable_error("raw_delete")
