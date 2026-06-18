"""Framework base for registry-resolved implementation classes.

An :class:`~angee.base.fields.ImplClassField` column selects one of these by key.
Beyond the behaviour its domain needs, an impl carries shared metadata and
*defaults* — the field values the owning row materialises when the impl is
chosen. Defaults merge along the MRO, so a refinement (``GmailIMAP``) inherits its
base's defaults (``IMAPBridge``) and overrides only what differs. Abstract bases
leave ``key`` blank and stay out of the registry; concrete leaves (including the
``Generic*`` fallbacks) register a key and are pickable.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import FieldDoesNotExist
from django.db import models

# A field value counts as "blank" (eligible for a default) when it is one of
# these — never ``0``/``False``, which are meaningful explicit values.
_BLANK_VALUES: tuple[Any, ...] = (None, "", [], {}, ())


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
        """Return this impl's defaults merged along the MRO (base → derived; derived wins)."""

        merged: dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            own = base.__dict__.get("defaults")
            if own:
                merged.update(own)
        return merged

    @classmethod
    def display_label(cls) -> str:
        """Return this impl's own label, falling back to a title-cased key.

        ``label`` is each impl's name, not a domain trait: a refinement that omits
        it reads from its key (``gmail_imap`` → "Gmail Imap"), never the base's
        label. ``icon``/``category`` inherit normally.
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
    def materialize(cls, instance: models.Model, *, blank_only: bool = True) -> None:
        """Seed ``instance``'s fields from this impl's effective defaults.

        The owning row carries the materialised values (editable, self-descriptive)
        — defaults are a starting point, not a live binding. A foreign-key default
        given as a string is resolved against the related model's ``slug`` natural
        key; a missing target leaves the FK unset. ``blank_only`` (the default)
        never overwrites a value the row already has.
        """

        for field_name, value in cls.effective_defaults().items():
            try:
                field = instance._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            if field.many_to_one and isinstance(value, str):
                cls._materialize_fk(instance, field, value, blank_only=blank_only)
                continue
            if blank_only and getattr(instance, field_name) not in _BLANK_VALUES:
                continue
            setattr(instance, field_name, value)

    @staticmethod
    def _materialize_fk(instance: models.Model, field: Any, natural_key: str, *, blank_only: bool) -> None:
        """Resolve a string FK default against the related model's ``slug`` and assign it."""

        if blank_only and getattr(instance, field.attname) is not None:
            return
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
    form (API, resource seed) still gets the chosen impl's defaults. Form-created
    rows already carry the materialised, possibly edited values, so ``blank_only``
    leaves them untouched.
    """

    class Meta:
        """Abstract: contributes the create-time default seeding only."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Seed impl defaults on first insert, then persist."""

        if self._state.adding:
            for field in self._meta.get_fields():
                resolve = getattr(field, "resolve_class", None)
                if resolve is None:
                    continue
                key = getattr(self, field.attname, None)
                if not key:
                    continue
                impl = resolve(key)
                if isinstance(impl, type) and issubclass(impl, ImplBase):
                    impl.materialize(self, blank_only=True)
        super().save(*args, **kwargs)
