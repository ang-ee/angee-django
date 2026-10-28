"""Row-owned implementation default materialization and config validation."""
from __future__ import annotations

import copy
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, Self

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.db.models.fields.reverse_related import ForeignObjectRel
from rebac import system_context

from angee.base.fields.implementation import ImplClassField
from angee.base.identity import public_id_of
from angee.base.implementations import ImplBase
from angee.base.permissions import require_rebac_queryset


class ImplDefaultsMixin(models.Model):
    """Materialise impl defaults on create for every ``ImplClassField`` on the model.

    The backend safety net behind the form-level prefill: a row created without a
    form (API, resource seed) still gets the chosen impl's defaults — for the fields
    the caller did not supply. Form-created rows pass their (possibly edited) values,
    so the impl never overrides them, even when a value equals the model default.
    """

    class Meta:
        """Abstract: contributes the create-time default seeding only."""

        abstract = True

    @classmethod
    def from_db(cls, db: str | None, field_names: Collection[str], values: Collection[Any]) -> Self:
        """Remember loaded impl keys so every persisted write ingress enforces immutability."""

        instance = super().from_db(db, field_names, values)
        loaded = dict(zip(field_names, values, strict=True))
        instance._loaded_impl_keys = {
            field.attname: loaded[field.attname]
            for field in instance._meta.get_fields()
            if isinstance(field, ImplClassField) and field.attname in loaded
        }
        return instance

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: models.QuerySet[Any] | None = None,
    ) -> None:
        """Keep the immutable-key snapshot coherent when Django reloads those fields."""

        refreshed = None if fields is None else set(fields)
        super().refresh_from_db(using=using, fields=refreshed, from_queryset=from_queryset)
        loaded = dict(getattr(self, "_loaded_impl_keys", {}))
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or not field.create_only:
                continue
            if refreshed is not None and field.name not in refreshed and field.attname not in refreshed:
                continue
            if field.attname in self.__dict__:
                loaded[field.attname] = self.__dict__[field.attname]
        self._loaded_impl_keys = loaded

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record the caller-supplied field names so create-time seeding skips them."""

        self._impl_provided_fields = frozenset(kwargs)
        super().__init__(*args, **kwargs)

    def mark_impl_provided_fields(self, field_names: Iterable[str]) -> None:
        """Record fields assigned after construction by a structured write ingress."""

        provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())
        self._impl_provided_fields = provided | frozenset(field_names)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Seed impl defaults for unsupplied fields on first insert, then persist."""

        adding = self._state.adding
        if adding:
            self.prepare_impl_defaults()
        update_fields = kwargs.get("update_fields")
        self.validate_impl_keys(update_fields=update_fields)
        self.validate_impl_configs(update_fields=update_fields)
        super().save(*args, **kwargs)
        loaded = dict(getattr(self, "_loaded_impl_keys", {}))
        updated = None if update_fields is None else set(update_fields)
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or field.attname not in self.__dict__:
                continue
            if adding or updated is None or field.name in updated or field.attname in updated:
                loaded[field.attname] = self.__dict__[field.attname]
        self._loaded_impl_keys = loaded

    def prepare_impl_defaults(self) -> None:
        """Prepare unsupplied implementation defaults before validation or insertion."""

        if not self._state.adding:
            return
        provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField):
                continue
            key = getattr(self, field.attname, None)
            if not key:
                continue
            key = field.key_for(key)
            setattr(self, field.attname, key)
            try:
                impl = field.resolve_class(key)
            except ValueError as error:
                raise ValidationError({field.name: str(error)}) from error
            if isinstance(impl, type) and issubclass(impl, ImplBase):
                self.materialize_impl_defaults(impl, provided=provided)

    def validate_impl_keys(self, *, update_fields: Any = None) -> None:
        """Reject persisted implementation switches at the shared model boundary."""

        if self._state.adding and self.pk is None:
            return
        updated = None if update_fields is None else set(update_fields)
        loaded = getattr(self, "_loaded_impl_keys", {})
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or not field.create_only:
                continue
            # A deferred selector omitted from the write keeps Django's loaded-field save semantics.
            if not self._state.adding and updated is None and field.attname not in self.__dict__:
                continue
            if field.attname not in loaded:
                if updated is not None and field.name not in updated and field.attname not in updated:
                    continue
                with system_context(reason="base.impl.validate_stored_key"):
                    stored_row = type(self)._base_manager.filter(pk=self.pk).values_list(field.attname).first()
                if stored_row is None and self._state.adding:
                    continue
                if stored_row is None:
                    raise ValidationError({field.name: "Stored implementation selection could not be verified."})
                stored = stored_row[0]
                loaded[field.attname] = stored
            if updated is not None and field.name not in updated and field.attname not in updated:
                continue
            if getattr(self, field.attname) != loaded[field.attname]:
                raise ValidationError({field.name: "Implementation selection is create-only."})

    def _config_field(self) -> models.Field[Any, Any]:
        """Resolve the concrete config column supplied by the composed model."""

        model: type[models.Model] = type(self)
        field = model._meta.get_field("config")
        if not isinstance(field, models.Field):
            raise FieldDoesNotExist(f"{model._meta.label}.config must be a concrete model field.")
        return field

    def apply_config_patch(self, patch: object) -> set[str]:
        """Merge top-level config keys, removing explicit ``None`` values.

        Return changed model field names for ``save(update_fields=...)``. Saving
        still validates and normalizes the merged config through its impl.
        """

        if not isinstance(patch, Mapping):
            raise ValidationError({"config": "Config patch must be an object."})
        field = self._config_field()
        current = field.value_from_object(self) or {}
        merged = dict(current)
        for key, value in patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if merged == current:
            return set()
        field.save_form_data(self, merged)
        return {"config"}

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Validate declared config models on save, leaving untyped row config alone."""

        if not self._state.adding and (
            (update_fields is not None and "config" not in update_fields)
            or (update_fields is None and "config" in self.get_deferred_fields())
        ):
            # Django omits deferred columns from an ordinary save. Resolving a
            # deferred selector first would load and write an untouched column.
            return
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField):
                continue
            key = getattr(self, field.attname, None)
            if not key:
                continue
            impl = field.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase) and impl.config_model is not None:
                config_field = self._config_field()
                if config_field.attname in self.get_deferred_fields() and (
                    update_fields is None or config_field.name not in update_fields
                ):
                    continue
                normalized = impl.normalize_config(config_field.value_from_object(self))
                config_field.save_form_data(self, normalized)

    def materialize_impl_defaults(
        self,
        impl: type[ImplBase],
        *,
        provided: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Seed this row from the implementation defaults on create.

        Seeds only fields the caller did not supply. A string foreign-key default
        resolves against the related model's ``slug``; mutable defaults are
        deep-copied so rows never alias the class-level dict.
        """

        changed: set[str] = set()
        for field_name, value in impl.effective_defaults().items():
            try:
                field = self._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            if field_name in provided or getattr(field, "attname", field_name) in provided:
                continue
            attname = getattr(field, "attname", field_name)
            before = getattr(self, attname)
            targets = _relation_default_queryset(field, value, querysets=lambda related: related._base_manager.all())
            if targets is not None:
                with system_context(reason="base.impl.materialize_fk"):
                    target = targets.first()
                if target is None:
                    raise ValueError(
                        f"{type(self).__name__}.{field.name} impl default references "
                        f"{targets.model._meta.label} slug {value!r}, but no row exists."
                    )
                setattr(self, field.name, target)
            else:
                setattr(self, field_name, copy.deepcopy(value))
            if getattr(self, attname) != before:
                changed.add(attname)
        return changed


def _relation_default_queryset(
    field: models.Field[Any, Any] | ForeignObjectRel,
    value: Any,
    *,
    querysets: Callable[[type[models.Model]], models.QuerySet[Any]],
) -> models.QuerySet[Any] | None:
    """Own the one rule mapping a string foreign-key default to its target slug."""
    if not isinstance(field, models.ForeignKey) or not isinstance(value, str):
        return None
    related = field.related_model
    try:
        related._meta.get_field("slug")
    except FieldDoesNotExist as error:
        raise FieldDoesNotExist(
            f"{field.model.__name__}.{field.name} impl default targets "
            f"{related._meta.label}, which must declare a slug field."
        ) from error
    return querysets(related).filter(slug=value)


def readable_impl_defaults(model: type[models.Model], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Project row defaults through native field policy and actor-visible identities."""
    result = dict(defaults)
    for name, value in tuple(result.items()):
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        targets = _relation_default_queryset(
            field, value, querysets=lambda related: require_rebac_queryset(related).scoped()
        )
        if targets is None:
            continue
        target = targets.first()
        if target is None:
            del result[name]
        else:
            result[name] = public_id_of(target)
    return result
