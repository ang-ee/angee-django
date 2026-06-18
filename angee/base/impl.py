"""Framework base for registry-resolved implementation classes.

An :class:`~angee.base.fields.ImplClassField` column selects one of these by key.
Beyond the behaviour its domain needs, an impl carries shared metadata and
*defaults* — the field values the owning row materialises when the impl is
chosen. Defaults merge along the MRO, so refinements inherit their base's
defaults and override only what differs. Abstract bases leave ``key`` blank and
stay out of the registry; concrete leaves register a key and are pickable.
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from django.core.exceptions import FieldDoesNotExist
from django.db import models


class ImplBase:
    """Base for an implementation selectable by an ``ImplClassField`` key.

    Subclasses declare class-level ``key``/``label``/``icon``/``category`` and a
    ``defaults`` mapping of model-field values to seed. Behaviour lives on the
    domain subclass (e.g. ``IntegrationImpl``, ``OAuthProviderType``).
    """

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    icon: ClassVar[str] = ""
    category: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}

    @classmethod
    def effective_defaults(cls) -> dict[str, Any]:
        """Return this impl's defaults merged along the MRO (base → derived; derived wins).

        Dict-valued defaults (e.g. a ``config`` preset) merge one level deep, so a
        refinement adds keys to its base's dict default instead of replacing it;
        scalar values are overridden outright.
        """

        merged: dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            own = base.__dict__.get("defaults")
            if not own:
                continue
            for field_name, value in own.items():
                current = merged.get(field_name)
                if isinstance(current, dict) and isinstance(value, dict):
                    merged[field_name] = {**current, **value}
                else:
                    merged[field_name] = value
        return merged

    @classmethod
    def display_label(cls) -> str:
        """Return this impl's own label, falling back to a title-cased key.

        ``label`` is each impl's name, not a domain trait: a refinement that
        omits it reads from its key, never the base's label.
        ``icon``/``category`` inherit normally.
        """

        own_label = cls.__dict__.get("label")
        if own_label:
            return str(own_label)
        return cls.key.replace("_", " ").title() if cls.key else (cls.label or cls.__name__)

    @classmethod
    def choice(cls) -> dict[str, Any]:
        """Return this impl's pickable choice metadata for forms (key/label/icon/category/defaults)."""

        return {
            "key": cls.key,
            "label": cls.display_label(),
            "icon": cls.icon,
            "category": cls.category,
            "defaults": cls.effective_defaults(),
        }

    @classmethod
    def materialize(cls, instance: models.Model, *, provided: frozenset[str] = frozenset()) -> None:
        """Seed ``instance``'s fields from this impl's effective defaults on create.

        Seeds only fields the caller did not supply (``provided`` = the explicitly
        passed field names) — the reliable "unset" signal, so a boolean default such
        as ``login_enabled=True`` lands when omitted yet an explicit ``False`` the
        caller passed is never overwritten. A string foreign-key default resolves
        against the related model's ``slug`` (a missing target leaves the FK unset);
        mutable defaults are deep-copied so rows never alias the class-level dict.
        """

        for field_name, value in cls.effective_defaults().items():
            try:
                field = instance._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            if field_name in provided or getattr(field, "attname", field_name) in provided:
                continue
            if field.many_to_one and isinstance(value, str):
                cls._materialize_fk(instance, field, value)
                continue
            setattr(instance, field_name, copy.deepcopy(value))

    @staticmethod
    def _materialize_fk(instance: models.Model, field: Any, natural_key: str) -> None:
        """Resolve a string FK default against the related model's ``slug`` and assign it."""

        related = field.related_model
        try:
            related._meta.get_field("slug")
        except FieldDoesNotExist:
            return
        target = related._default_manager.filter(slug=natural_key).first()
        if target is not None:
            setattr(instance, field.name, target)


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record the caller-supplied field names so create-time seeding skips them."""

        self._impl_provided_fields = frozenset(kwargs)
        super().__init__(*args, **kwargs)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Seed impl defaults for unsupplied fields on first insert, then persist."""

        if self._state.adding:
            provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())
            for field in self._meta.get_fields():
                resolve = getattr(field, "resolve_class", None)
                if resolve is None:
                    continue
                key = getattr(self, field.attname, None)
                if not key:
                    continue
                impl = resolve(key)
                if isinstance(impl, type) and issubclass(impl, ImplBase):
                    impl.materialize(self, provided=provided)
        super().save(*args, **kwargs)
