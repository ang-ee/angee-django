"""Implementation-class metadata independent of Django field declarations."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, ClassVar

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from rebac import system_context


@dataclass(frozen=True, slots=True)
class ImplChoice:
    """Pickable implementation metadata shared by GraphQL and form defaults."""

    key: str
    label: str
    icon: str
    category: str
    defaults: dict[str, Any]


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
        """Return this impl's defaults merged along the MRO (base to derived; derived wins).

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
    def choice(cls) -> ImplChoice:
        """Return this impl's pickable choice metadata for forms (key/label/icon/category/defaults)."""

        return ImplChoice(
            key=cls.key,
            label=cls.display_label(),
            icon=cls.icon,
            category=cls.category,
            defaults=cls.effective_defaults(),
        )

    @classmethod
    def materialize(cls, instance: models.Model, *, provided: frozenset[str] = frozenset()) -> None:
        """Seed ``instance``'s fields from this impl's effective defaults on create.

        Seeds only fields the caller did not supply (``provided`` = the explicitly
        passed field names) - the reliable "unset" signal, so a boolean default such
        as ``login_enabled=True`` lands when omitted yet an explicit ``False`` the
        caller passed is never overwritten. A string foreign-key default resolves
        against the related model's ``slug`` (a missing target fails fast); mutable
        defaults are deep-copied so rows never alias the class-level dict. Django
        bulk write APIs do not call model ``save()``, so callers that need impl
        defaults in bulk must call this owner explicitly before the bulk write.
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
        except FieldDoesNotExist as error:
            raise FieldDoesNotExist(
                f"{type(instance).__name__}.{field.name} impl default targets {related._meta.label}, "
                "which must declare a slug field."
            ) from error
        with system_context(reason="angee.impl.materialize_fk"):
            target = related._base_manager.filter(slug=natural_key).first()
        if target is None:
            raise ValueError(
                f"{type(instance).__name__}.{field.name} impl default references "
                f"{related._meta.label} slug {natural_key!r}, but no row exists."
            )
        setattr(instance, field.name, target)
