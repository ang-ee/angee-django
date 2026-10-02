"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

from typing import Any, Generic, Self, TypeVar, cast

from django.db import models

from angee.base.scoping import lock_if_supported

_ModelT = TypeVar("_ModelT", bound=models.Model)


class _AngeeQuerySetMixin(Generic[_ModelT]):
    """Keep Angee's public lookup and lock API on every manager policy."""

    model: type[_ModelT]

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        if value == "":
            return None
        try:
            lookup = cast(Any, self.model).public_id_lookup(value)
            return cast(_ModelT | None, cast(Any, self).filter(**lookup).first())
        except TypeError, ValueError:
            return None

    def lock_if_supported(
        self, *, of: tuple[str, ...] = ("self",), skip_locked: bool = False, no_key: bool = False
    ) -> Self:
        """Expose shared lock intent on Angee querysets and managers."""

        return cast(
            Self,
            lock_if_supported(cast(models.QuerySet[_ModelT], self), of=of, skip_locked=skip_locked, no_key=no_key),
        )

    def locked_get(self, *args: Any, **kwargs: Any) -> _ModelT:
        """Return one row under a database row lock when the backend supports it."""

        return cast(models.QuerySet[_ModelT], self.lock_if_supported()).get(*args, **kwargs)
