"""Row-owned implementation default materialization and config validation."""
from __future__ import annotations

import copy
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.fields.reverse_related import ForeignObjectRel
from rebac import system_context

from angee.base.fields.implementation import ImplClassField
from angee.base.implementations import ImplBase, resolve_all_impl_classes
from angee.base.models.identity import public_id_of
from angee.base.permissions import require_rebac_queryset

if TYPE_CHECKING:
    from django.db.models.fetch_modes import FetchMode


class ImplDefaultsMixin(models.Model):
    """Materialise impl defaults on create for every ``ImplClassField`` on the model.

    The backend safety net behind the form-level prefill: a row created without a
    form (API, resource seed) still gets the chosen impl's defaults — for the fields
    the caller did not supply. Form-created rows pass their (possibly edited) values,
    so the impl never overrides them, even when a value equals the model default.

    Every save stores the selected implementation's validated configuration in
    the model's configuration column, system work included: ``config`` unless the
    model names another (:attr:`impl_config_field`). A configuration the
    implementation refuses is reported under that column's name.
    """

    class Meta:
        """Own row defaults, typed config and persisted selector invariants."""

        abstract = True

    impl_config_field: ClassVar[str] = "config"
    """The concrete JSON column holding the configuration of the model's typed implementation selector."""

    _loaded_impl_keys: dict[str, Any]

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require one concrete JSON config owner for typed row implementations.

        Plain field defaults and rowless implementations do not declare a row
        config contract. Native effective fields account for inherited and
        overridden selectors on the final composed model.
        """

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors
        selectors: list[ImplClassField[Any]] = []
        for field in cls._meta.get_fields():
            if not isinstance(field, ImplClassField) or field.base_class is None:
                continue
            try:
                implementations = resolve_all_impl_classes(field.base_class)
            except ImportError, ImproperlyConfigured:
                # The installed registry check owns invalid declarations; this
                # model check owns only the row/config relationship.
                continue
            if any(
                issubclass(impl, ImplBase) and impl.config_model is not None
                for impl in implementations
            ):
                selectors.append(field)
        if not selectors:
            return errors
        try:
            cls._config_field()
        except FieldDoesNotExist as error:
            errors.append(checks.Error(str(error), obj=cls, id="angee.E046"))
        if len(selectors) > 1:
            errors.append(checks.Error(
                f"{cls._meta.label} has multiple typed config selectors: "
                f"{', '.join(field.name for field in selectors)}.",
                hint="One implementation selector owns the model's config JSON column.",
                obj=cls,
                id="angee.E047",
            ))
        return errors

    @classmethod
    def from_db(
        cls, db: str | None, field_names: Collection[str], values: Collection[Any],
        *, fetch_mode: FetchMode | None = None,
    ) -> Self:
        """Remember loaded impl keys so every persisted write ingress enforces immutability."""

        instance = super().from_db(db, field_names, values, fetch_mode=fetch_mode)
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
        loaded = dict(self._loaded_impl_keys)
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

        self._loaded_impl_keys = {}
        self._impl_provided_fields = frozenset(kwargs)
        super().__init__(*args, **kwargs)

    def mark_impl_provided_fields(self, field_names: Iterable[str]) -> None:
        """Record fields assigned after construction by a structured write ingress."""

        provided: frozenset[str] = self._impl_provided_fields
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
        loaded = dict(self._loaded_impl_keys)
        updated = None if update_fields is None else set(update_fields)
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or field.attname not in self.__dict__:
                continue
            if adding or updated is None or field.name in updated or field.attname in updated:
                loaded[field.attname] = self.__dict__[field.attname]
        self._loaded_impl_keys = loaded

    def prepare_impl_defaults(self) -> None:
        """Prepare unsupplied implementation defaults before validation or insertion.

        A key outside the field's choices prepares nothing: its
        :class:`ImplClassField` refuses it when the row is validated or inserted,
        so a caller who may not save the row gets that save's refusal instead.
        """

        if not self._state.adding:
            return
        provided: frozenset[str] = self._impl_provided_fields
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField):
                continue
            key = getattr(self, field.attname, None)
            if not key:
                continue
            key = field.key_for(key)
            setattr(self, field.attname, key)
            if not field.has_key(key):
                continue
            try:
                impl = field.resolve_class(key)
            except ValueError as error:
                raise ValidationError({field.name: str(error)}) from error
            if isinstance(impl, type) and issubclass(impl, ImplBase):
                self.materialize_impl_defaults(impl, provided=provided)

    def clean_fields(self, exclude: Collection[str] | None = None) -> None:
        """Refuse an implementation key outside its field's choices on its own.

        The fields that implementation would have defaulted are not the
        caller's error while its key is unknown, so the key's field error
        stands alone. A stored row keeps such a key it holds
        (:meth:`ImplClassField.holds`): an update does not validate a key it
        is not changing.
        """

        excluded = set(exclude or ())
        unknown = {
            field.name: field.invalid_key(key)
            for field in self._meta.get_fields()
            if isinstance(field, ImplClassField)
            and field.name not in excluded
            and (key := getattr(self, field.attname, None))
            and not field.has_key(key)
            and not field.holds(self, key)
        }
        if unknown:
            raise ValidationError(unknown)
        super().clean_fields(exclude)

    def validate_impl_keys(self, *, update_fields: Any = None) -> None:
        """Reject persisted implementation switches at the shared model boundary."""

        if self._state.adding and self.pk is None:
            return
        updated = None if update_fields is None else set(update_fields)
        loaded = self._loaded_impl_keys
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

    @classmethod
    def _config_field(cls) -> models.JSONField:
        """Resolve the model's one JSON configuration column (:attr:`impl_config_field`)."""

        model: type[models.Model] = cls
        field = model._meta.get_field(cls.impl_config_field)
        if not isinstance(field, models.JSONField) or not field.concrete:
            raise FieldDoesNotExist(f"{cls._meta.label}.{cls.impl_config_field} must be a concrete JSONField.")
        return field

    def impl_config_error(self, error: ValidationError) -> ValidationError:
        """Return ``error``, an implementation's refusal of a configuration, under this model's column.

        An implementation names what it refuses from ``config`` down
        (``ImplBase.parse_config``); the row reports it at its own column, with
        the same path below it.
        """

        name = self.impl_config_field
        if name == "config":
            return error
        if not hasattr(error, "error_dict"):
            return ValidationError({name: error.error_list})
        return ValidationError({
            f"{name}{key[len('config'):]}" if key == "config" or key.startswith("config.") else key: errors
            for key, errors in error.error_dict.items()
        })

    def apply_config_patch(self, patch: object) -> set[str]:
        """Merge top-level config keys, removing explicit ``None`` values.

        Return changed model field names for ``save(update_fields=...)``. Saving
        still validates and normalizes the merged config through its impl.
        """

        field = self._config_field()
        if not isinstance(patch, Mapping):
            raise ValidationError({field.name: "Config patch must be an object."})
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
        return {field.name}

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Validate declared config models on save, leaving untyped row config alone.

        A new row's key outside the field's choices has no config model to
        validate against: the field refuses that key when the row is inserted.
        A stored row that keeps such a key it holds has no implementation to
        read its config either, so the config is written only as it is stored;
        a changed one is refused under the config column.
        """

        name = self.impl_config_field
        if not self._state.adding and (
            (update_fields is not None and name not in update_fields)
            or (update_fields is None and name in self.get_deferred_fields())
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
            if not field.has_key(key):
                if self._state.adding:
                    continue
                if not field.holds(self, key):
                    raise ValidationError({field.name: field.invalid_key(key)})
                self._refuse_changed_config_under(field)
                continue
            impl = field.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase) and impl.config_model is not None:
                config_field = self._config_field()
                if config_field.attname in self.get_deferred_fields() and (
                    update_fields is None or config_field.name not in update_fields
                ):
                    continue
                try:
                    normalized = impl.normalize_config(config_field.value_from_object(self))
                except ValidationError as error:
                    raise self.impl_config_error(error) from None
                config_field.save_form_data(self, normalized)

    def _refuse_changed_config_under(self, field: ImplClassField[Any]) -> None:
        """Refuse a config other than the stored one while ``field`` keeps a key no implementation reads."""

        try:
            config_field = self._config_field()
        except FieldDoesNotExist:
            return
        with system_context(reason="base.impl.held_key_config"):
            stored = type(self)._base_manager.using(self._state.db).filter(pk=self.pk).values_list(
                config_field.attname, flat=True,
            ).first()
        if config_field.value_from_object(self) != stored:
            raise ValidationError({config_field.name: (
                f"The configuration cannot change while the {field.verbose_name} is not registered."
            )})

    def materialize_impl_defaults(
        self,
        impl: type[ImplBase],
        *,
        provided: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Seed this row from the implementation defaults on create.

        Seeds only fields the caller did not supply. The configuration defaults
        (the ``config`` key of ``ImplBase.effective_defaults``) seed the model's
        configuration column (:attr:`impl_config_field`). A string foreign-key default
        resolves against the related model's ``slug``, a seeded row: a missing one
        raises ``ImproperlyConfigured`` with the load guidance. Mutable defaults are
        deep-copied so rows never alias the class-level dict.
        """

        changed: set[str] = set()
        for field_name, value in impl.effective_defaults().items():
            if field_name == "config":
                field_name = self.impl_config_field
            try:
                field = self._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            attname = field.attname if isinstance(field, models.Field) else field_name
            if field_name in provided or attname in provided:
                continue
            before = getattr(self, attname)
            targets = _relation_default_queryset(field, value, querysets=lambda related: related._base_manager.all())
            if targets is not None:
                with system_context(reason="base.impl.materialize_fk"):
                    target = targets.first()
                if target is None:
                    # A slug default names a seeded row: the catalogue the resource loader seeds.
                    raise ImproperlyConfigured(
                        f"{type(self).__name__}.{field.name} impl default references "
                        f"{targets.model._meta.label} slug {value!r}, but no row exists. Run "
                        "`manage.py resources load` before creating it."
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
